from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory
import cv2
import numpy as np
from PIL import Image, ImageDraw
import fitz
import os
import uuid
import json

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Change this to a secure key

UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Ensure upload folder exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# User management
USERS_FILE = 'users.json'

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r') as f:
            raw_users = json.load(f)
        users = {}
        for username, data in raw_users.items():
            if isinstance(data, str):
                users[username] = {'password': data, 'email': ''}
            elif isinstance(data, dict):
                users[username] = {
                    'password': data.get('password', ''),
                    'email': data.get('email', '')
                }
        return users
    return {'admin': {'password': 'password', 'email': 'admin@example.com'}}

def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)

users = load_users()

# ---------------- LOAD FILE ---------------- #
def load_file(file_path):
    ext = file_path.split(".")[-1].lower()
    if ext in ["jpg", "jpeg", "png"]:
        return np.array(Image.open(file_path).convert("RGB"))
    elif ext == "pdf":
        pdf = fitz.open(file_path)
        page = pdf[0]
        pix = page.get_pixmap(dpi=200)
        return np.array(Image.frombytes("RGB", [pix.width, pix.height], pix.samples))
    return None

# ---------------- DETECT ---------------- #
def detect_visual_tampering(image):
    # Edge-based tampering detection with conservative parameters
    image = cv2.resize(image, (800, 1000))  # Resize for consistent processing
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (7, 7), 0)
    edges = cv2.Canny(blur, 150, 300)  # Higher thresholds for fewer edges
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    dilated = cv2.dilate(edges, kernel, iterations=1)
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    # Use PIL for highlighting with transparency
    marked = Image.fromarray(image).convert('RGBA')
    draw = ImageDraw.Draw(marked, 'RGBA')
    red_boxes = 0
    
    for c in contours:
        area = cv2.contourArea(c)
        if area < 2000:  # Higher min area to reduce false positives
            continue
        
        x, y, w, h = cv2.boundingRect(c)
        if w > 4 * h:  # Ignore long horizontal lines (text)
            continue
        
        # Draw semi-transparent red highlight
        draw.rectangle([x, y, x + w, y + h], fill=(255, 0, 0, 100))
        # Draw red border
        draw.rectangle([x, y, x + w, y + h], outline=(255, 0, 0, 255), width=2)
        red_boxes += 1
    
    marked = np.array(marked)
    return marked, red_boxes, image

# ---------------- DECISION ---------------- #
def verdict(red_boxes):
    if red_boxes >= 6:
        return "FAKE", 60
    elif red_boxes >= 3:
        return "SUSPICIOUS", 80
    else:
        return "REAL", 95

@app.route('/')
def home():
    if 'logged_in' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = users.get(username)
        if user and user.get('password') == password:
            session['logged_in'] = True
            session['username'] = username
            flash('Welcome back! You are now logged in.', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password. Please try again.', 'error')
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        email = request.form.get('email', '').strip()
        if username in users:
            flash('That username is already taken. Choose another one.', 'error')
        elif not username or not password or not email:
            flash('Username, email, and password are all required.', 'error')
        else:
            users[username] = {'password': password, 'email': email}
            save_users(users)
            flash('Signup successful! You can now log in.', 'success')
            return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash('Logged out successfully', 'info')
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if 'logged_in' not in session:
        return redirect(url_for('login'))
    return render_template('dashboard.html')

@app.route('/upload', methods=['POST'])
def upload():
    if 'logged_in' not in session:
        return redirect(url_for('login'))
    
    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('dashboard'))
    
    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('dashboard'))
    
    if file:
        cleanup_upload_folder()
        filename = str(uuid.uuid4()) + os.path.splitext(file.filename)[1]
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        image = load_file(file_path)
        if image is None:
            flash('Unsupported file type', 'error')
            os.remove(file_path)
            return redirect(url_for('dashboard'))
        
        marked, red_count, resized = detect_visual_tampering(image)
        result, confidence = verdict(red_count)
        
        # Save images
        base_name = os.path.splitext(filename)[0]
        original_filename = 'original_' + base_name + '.jpg'
        marked_filename = 'marked_' + base_name + '.png'
        
        Image.fromarray(cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)).save(os.path.join(app.config['UPLOAD_FOLDER'], original_filename))
        Image.fromarray(marked).save(os.path.join(app.config['UPLOAD_FOLDER'], marked_filename))

        # Remove the temporary raw upload
        try:
            os.remove(file_path)
        except OSError:
            pass
        
        return render_template('result.html', 
                             original=original_filename, 
                             marked=marked_filename, 
                             red_count=red_count, 
                             result=result, 
                             confidence=confidence)

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

def cleanup_upload_folder():
    for existing_file in os.listdir(app.config['UPLOAD_FOLDER']):
        existing_path = os.path.join(app.config['UPLOAD_FOLDER'], existing_file)
        if os.path.isfile(existing_path):
            try:
                os.remove(existing_path)
            except OSError:
                pass

if __name__ == '__main__':
    app.run(debug=True)

