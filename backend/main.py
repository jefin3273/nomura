from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests
import json
import logging
import sqlite3
import hashlib
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import re
import logging
import speech_recognition as sr
from werkzeug.utils import secure_filename
import uuid
import datetime
import secrets
import os

app = Flask(__name__)
CORS(app) 

# Configuration
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Ensure upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# LM Studio API configuration
LM_STUDIO_BASE_URL = "http://127.0.0.1:1234"
CHAT_ENDPOINT = f"{LM_STUDIO_BASE_URL}/v1/chat/completions"
MODELS_ENDPOINT = f"{LM_STUDIO_BASE_URL}/v1/models"

def generate_event_template(prompt):
    payload = {
        "model": "your-model-id",  # Replace with your LM Studio model ID
        "messages": [
            {"role": "system", "content": "You are an event organizer assistant that creates templates for community cleanup events."},
            {"role": "user", "content": f"Create a detailed event template for this cleanup drive prompt: {prompt}"}
        ],
        "temperature": 0.5,
        "max_tokens": 600
    }

    response = requests.post(CHAT_ENDPOINT, json=payload)
    if response.status_code == 200:
        result = response.json()
        return result['choices'][0]['message']['content']
    else:
        raise Exception(f"LM Studio error: {response.status_code}")

def init_db():
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            phone TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT 1
        )
    ''')
    
    # Password reset tokens table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Events table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            date TEXT NOT NULL,
            place TEXT NOT NULL,
            image TEXT,
            admin_id INTEGER NOT NULL,
            max_participants INTEGER DEFAULT 50,
            current_participants INTEGER DEFAULT 0,
            waste_collected REAL DEFAULT 0.0,
            status TEXT DEFAULT 'upcoming',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (admin_id) REFERENCES users (id)
        )
    ''')
    
    # Event participants table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS event_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (event_id) REFERENCES events (event_id),
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    conn.commit()
    conn.close()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db_connection():
    conn = sqlite3.connect('users.db')
    conn.row_factory = sqlite3.Row
    return conn

# LLM Integration
def generate_event_with_llm(prompt):
    try:
        headers = {
            'Content-Type': 'application/json',
        }
        
        system_prompt = """You are an AI assistant that creates beach cleanup event templates. 
        Based on the user's prompt, create a JSON response with the following structure:
        {
            "title": "Event title",
            "description": "Detailed event description",
            "place": "Location/beach name",
            "date": "YYYY-MM-DD format",
            "max_participants": number
        }
        
        Make the events engaging, environmental-focused, and include details about what participants should bring, meeting points, and expected outcomes."""
        
        data = {
            "model": "local-model",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 500
        }
        
        response = requests.post(CHAT_ENDPOINT, headers=headers, json=data, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            content = result['choices'][0]['message']['content']
            
            # Try to extract JSON from the response
            try:
                # Find JSON in the response
                start_idx = content.find('{')
                end_idx = content.rfind('}') + 1
                json_str = content[start_idx:end_idx]
                event_data = json.loads(json_str)
                return event_data
            except:
                # Fallback if JSON parsing fails
                return {
                    "title": "Beach Cleanup Event",
                    "description": content,
                    "place": "Local Beach",
                    "date": (datetime.datetime.now() + datetime.timedelta(days=7)).strftime("%Y-%m-%d"),
                    "max_participants": 50
                }
        else:
            return None
    except Exception as e:
        print(f"LLM Error: {e}")
        return None

# Helper functions
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, hash):
    return hash_password(password) == hash

def validate_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def validate_phone(phone):
    # Remove any spaces, dashes, or plus signs
    clean_phone = re.sub(r'[^\d]', '', phone)
    # Check if it's a valid Indian phone number (10 digits)
    return len(clean_phone) == 10 or (len(clean_phone) == 12 and clean_phone.startswith('91'))

def send_reset_email(email, token):
    # Configure your email settings here
    SMTP_SERVER = "smtp.gmail.com"  # Change to your SMTP server
    SMTP_PORT = 587
    EMAIL_ADDRESS = "your-email@gmail.com"  # Change to your email
    EMAIL_PASSWORD = "your-app-password"    # Change to your app password
    
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_ADDRESS
        msg['To'] = email
        msg['Subject'] = "Password Reset Request"
        
        reset_link = f"http://localhost:3000/reset-password?token={token}"
        body = f"""
        Hi,
        
        You have requested to reset your password. Click the link below to reset your password:
        
        {reset_link}
        
        This link will expire in 1 hour.
        
        If you didn't request this, please ignore this email.
        
        Best regards,
        Your App Team
        """
        
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        text = msg.as_string()
        server.sendmail(EMAIL_ADDRESS, email, text)
        server.quit()
        
        return True
    except Exception as e:
        print(f"Email sending failed: {e}")
        return False


# Admin Routes
@app.route('/api/admin/dashboard', methods=['GET'])
def admin_dashboard():
    try:
        conn = get_db_connection()
        
        # Total events
        total_events = conn.execute('SELECT COUNT(*) as count FROM events').fetchone()['count']
        
        # Total participants
        total_participants = conn.execute('SELECT COUNT(*) as count FROM event_participants').fetchone()['count']
        
        # Total waste collected
        total_waste = conn.execute('SELECT SUM(waste_collected) as total FROM events').fetchone()['total'] or 0
        
        # Upcoming events
        upcoming_events = conn.execute(
            'SELECT COUNT(*) as count FROM events WHERE date >= date("now")'
        ).fetchone()['count']
        
        # Recent events
        recent_events = conn.execute('''
            SELECT e.*, u.name as admin_name 
            FROM events e 
            JOIN users u ON e.admin_id = u.id 
            ORDER BY e.created_at DESC 
            LIMIT 5
        ''').fetchall()
        
        conn.close()
        
        return jsonify({
            'status': 'success',
            'data': {
                'total_events': total_events,
                'total_participants': total_participants,
                'total_waste_collected': round(total_waste, 2),
                'upcoming_events': upcoming_events,
                'recent_events': [dict(row) for row in recent_events]
            }
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/admin/events', methods=['GET'])
def get_all_events():
    try:
        conn = get_db_connection()
        events = conn.execute('''
            SELECT e.*, u.name as admin_name 
            FROM events e 
            JOIN users u ON e.admin_id = u.id 
            ORDER BY e.created_at DESC
        ''').fetchall()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'events': [dict(row) for row in events]
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/admin/events/generate', methods=['POST'])
def generate_event_admin():
    try:
        data = request.get_json()
        prompt = data.get('prompt')
        admin_id = data.get('admin_id', 1)  # Default admin ID
        
        if not prompt:
            return jsonify({'status': 'error', 'message': 'Prompt is required'}), 400
        
        # Generate event using LLM
        event_data = generate_event_with_llm(prompt)
        
        if not event_data:
            return jsonify({'status': 'error', 'message': 'Failed to generate event'}), 500
        
        # Add additional fields
        event_data['event_id'] = str(uuid.uuid4())
        event_data['admin_id'] = admin_id
        
        return jsonify({
            'status': 'success',
            'event': event_data
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/admin/events', methods=['POST'])
def create_event_admin():
    try:
        data = request.get_json()
        
        # Generate unique event ID
        event_id = str(uuid.uuid4())
        
        conn = get_db_connection()
        conn.execute('''
            INSERT INTO events (event_id, title, description, date, place, admin_id, max_participants)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            event_id,
            data['title'],
            data.get('description', ''),
            data['date'],
            data['place'],
            data.get('admin_id', 1),
            data.get('max_participants', 50)
        ))
        conn.commit()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'message': 'Event created successfully',
            'event_id': event_id
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/admin/events/<event_id>', methods=['PUT'])
def update_event(event_id):
    try:
        data = request.get_json()
        
        conn = get_db_connection()
        conn.execute('''
            UPDATE events 
            SET title = ?, description = ?, date = ?, place = ?, max_participants = ?
            WHERE event_id = ?
        ''', (
            data['title'],
            data.get('description', ''),
            data['date'],
            data['place'],
            data.get('max_participants', 50),
            event_id
        ))
        conn.commit()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'message': 'Event updated successfully'
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/admin/events/<event_id>', methods=['DELETE'])
def delete_event(event_id):
    try:
        conn = get_db_connection()
        
        # Delete event participants first
        conn.execute('DELETE FROM event_participants WHERE event_id = ?', (event_id,))
        
        # Delete event
        conn.execute('DELETE FROM events WHERE event_id = ?', (event_id,))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'message': 'Event deleted successfully'
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/upload', methods=['POST'])
def upload_file():
    try:
        if 'file' not in request.files:
            return jsonify({'status': 'error', 'message': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'status': 'error', 'message': 'No file selected'}), 400
        
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            # Add timestamp to avoid conflicts
            filename = f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            
            return jsonify({
                'status': 'success',
                'filename': filename,
                'url': f'/uploads/{filename}'
            })
        else:
            return jsonify({'status': 'error', 'message': 'Invalid file type'}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# Routes
@app.route('/generate-event-template', methods=['POST'])
def generate_event():
    try:
        data = request.get_json()
        prompt = data.get('prompt', '')
        if not prompt:
            return jsonify({"success": False, "error": "Prompt is required."}), 400

        content = generate_event_template(prompt)
        return jsonify({"success": True, "template": content})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/create-event', methods=['POST'])
def create_event():
    try:
        data = request.get_json()
        admin_id = data['admin_id']
        date = data['date']
        place = data['place']
        image = data.get('image', '')
        template = data['template']

        conn = sqlite3.connect('events.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO events (admin_id, date, place, image, template)
            VALUES (?, ?, ?, ?, ?)
        ''', (admin_id, date, place, image, template))
        conn.commit()
        conn.close()

        return jsonify({"success": True, "message": "Event created successfully"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/get-events', methods=['GET'])
def get_events():
    try:
        conn = sqlite3.connect('events.db')
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM events")
        rows = cursor.fetchall()
        conn.close()

        events = [
            {
                "event_id": row[0],
                "admin_id": row[1],
                "date": row[2],
                "place": row[3],
                "image": row[4],
                "template": row[5],
                "created_at": row[6],
            }
            for row in rows
        ]

        return jsonify({"success": True, "events": events})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/speech-to-text', methods=['GET'])
def speech_to_text():
    try:
        recognizer = sr.Recognizer()
        with sr.Microphone() as source:
            print("Listening...")
            audio = recognizer.listen(source, timeout=5)

        text = recognizer.recognize_google(audio)
        return jsonify({"success": True, "text": text})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy"})

@app.route('/api/register', methods=['POST'])
def register():
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['name', 'email', 'phone', 'password', 'confirmPassword']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'error': f'{field} is required'}), 400
        
        # Validate email format
        if not validate_email(data['email']):
            return jsonify({'error': 'Invalid email format'}), 400
        
        # Validate phone format
        if not validate_phone(data['phone']):
            return jsonify({'error': 'Invalid phone number format'}), 400
        
        # Check if passwords match
        if data['password'] != data['confirmPassword']:
            return jsonify({'error': 'Passwords do not match'}), 400
        
        # Check password strength
        if len(data['password']) < 6:
            return jsonify({'error': 'Password must be at least 6 characters long'}), 400
        
        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        
        # Check if user already exists
        cursor.execute('SELECT id FROM users WHERE email = ?', (data['email'],))
        if cursor.fetchone():
            conn.close()
            return jsonify({'error': 'User with this email already exists'}), 400
        
        # Hash password and insert user
        password_hash = hash_password(data['password'])
        cursor.execute('''
            INSERT INTO users (name, email, phone, password_hash)
            VALUES (?, ?, ?, ?)
        ''', (data['name'], data['email'], data['phone'], password_hash))
        
        user_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return jsonify({
            'message': 'User registered successfully',
            'user_id': user_id,
            'user': {
                'id': user_id,
                'name': data['name'],
                'email': data['email'],
                'phone': data['phone']
            }
        }), 201
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        
        if not data.get('email') or not data.get('password'):
            return jsonify({'error': 'Email and password are required'}), 400
        
        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, name, email, phone, password_hash, is_active
            FROM users WHERE email = ?
        ''', (data['email'],))
        
        user = cursor.fetchone()
        conn.close()
        
        if not user:
            return jsonify({'error': 'Invalid email or password'}), 401
        
        if not user[5]:  # is_active
            return jsonify({'error': 'Account is deactivated'}), 401
        
        if not verify_password(data['password'], user[4]):  # password_hash
            return jsonify({'error': 'Invalid email or password'}), 401
        
        return jsonify({
            'message': 'Login successful',
            'user_id': user[0],
            'user': {
                'id': user[0],
                'name': user[1],
                'email': user[2],
                'phone': user[3]
            }
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/forgot-password', methods=['POST'])
def forgot_password():
    try:
        data = request.get_json()
        
        if not data.get('email'):
            return jsonify({'error': 'Email is required'}), 400
        
        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        
        # Check if user exists
        cursor.execute('SELECT id FROM users WHERE email = ?', (data['email'],))
        user = cursor.fetchone()
        
        if not user:
            # Don't reveal if email exists or not for security
            return jsonify({'message': 'If the email exists, a reset link has been sent'}), 200
        
        # Generate reset token
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(hours=1)
        
        # Store token in database
        cursor.execute('''
            INSERT INTO password_reset_tokens (user_id, token, expires_at)
            VALUES (?, ?, ?)
        ''', (user[0], token, expires_at))
        
        conn.commit()
        conn.close()
        
        # Send email (uncomment when email is configured)
        # if send_reset_email(data['email'], token):
        #     return jsonify({'message': 'Password reset link sent to your email'}), 200
        # else:
        #     return jsonify({'error': 'Failed to send email'}), 500
        
        # For development, return the token (remove in production)
        return jsonify({
            'message': 'Password reset link sent to your email',
            'token': token  # Remove this in production
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/reset-password', methods=['POST'])
def reset_password():
    try:
        data = request.get_json()
        
        if not data.get('token') or not data.get('password') or not data.get('confirmPassword'):
            return jsonify({'error': 'Token, password, and confirm password are required'}), 400
        
        if data['password'] != data['confirmPassword']:
            return jsonify({'error': 'Passwords do not match'}), 400
        
        if len(data['password']) < 6:
            return jsonify({'error': 'Password must be at least 6 characters long'}), 400
        
        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        
        # Verify token
        cursor.execute('''
            SELECT user_id FROM password_reset_tokens
            WHERE token = ? AND expires_at > ? AND used = 0
        ''', (data['token'], datetime.now()))
        
        token_data = cursor.fetchone()
        
        if not token_data:
            conn.close()
            return jsonify({'error': 'Invalid or expired token'}), 400
        
        user_id = token_data[0]
        
        # Update password
        password_hash = hash_password(data['password'])
        cursor.execute('UPDATE users SET password_hash = ? WHERE id = ?', (password_hash, user_id))
        
        # Mark token as used
        cursor.execute('UPDATE password_reset_tokens SET used = 1 WHERE token = ?', (data['token'],))
        
        conn.commit()
        conn.close()
        
        return jsonify({'message': 'Password reset successfully'}), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/user/<int:user_id>', methods=['GET'])
def get_user(user_id):
    try:
        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, name, email, phone, created_at
            FROM users WHERE id = ? AND is_active = 1
        ''', (user_id,))
        
        user = cursor.fetchone()
        conn.close()
        
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        return jsonify({
            'user': {
                'id': user[0],
                'name': user[1],
                'email': user[2],
                'phone': user[3],
                'created_at': user[4]
            }
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def get_available_models():
    try:
        response = requests.get(MODELS_ENDPOINT, timeout=10)
        if response.status_code == 200:
            return response.json().get('data', [])
        logger.error(f"Failed to get models: {response.status_code}")
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"Error connecting to LM Studio: {e}")
        return []


def generate_quiz_with_ai(context, num_questions=5):
    try:
        models = get_available_models()
        if not models:
            raise Exception("No models available in LM Studio")

        model_id = models[0].get('id', 'local-model')

        system_prompt = """You are an educational quiz generator. Create environmental quiz questions as a valid JSON array.

CRITICAL: Return ONLY a JSON array, nothing else. No extra text, no markdown, no wrapper objects.

Exact format required:
[
  {
    "question": "Question text?",
    "options": ["A", "B", "C", "D"],
    "correct": 0,
    "explanation": "Brief explanation."
  }
]"""

        user_prompt = f"Create {num_questions} quiz questions about: {context}. Focus on environmental facts, decomposition time, and recycling."

        payload = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.3,
            "max_tokens": 800,
            "stream": False
        }

        response = requests.post(
            CHAT_ENDPOINT,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=600
        )

        if response.status_code == 200:
            result = response.json()
            content = result['choices'][0]['message']['content'].strip()

            logger.info(f"Raw AI response snippet: {content[:200]}...")

            # Save raw for debugging if needed
            with open("raw_ai_response.txt", "w", encoding="utf-8") as f:
                f.write(content)

            # Try to extract JSON array using regex
            match = re.search(r'\[\s*{.*?}\s*]', content, re.DOTALL)
            if not match:
                raise ValueError("Could not extract JSON array from model response")

            content = match.group(0)
            quiz_data = json.loads(content)

            if isinstance(quiz_data, list):
                validated = []
                for q in quiz_data:
                    if (
                        isinstance(q, dict)
                        and 'question' in q
                        and 'options' in q
                        and 'correct' in q
                        and 'explanation' in q
                        and isinstance(q['options'], list)
                        and len(q['options']) == 4
                        and isinstance(q['correct'], int)
                        and 0 <= q['correct'] < 4
                    ):
                        validated.append(q)

                if validated:
                    logger.info(f"Parsed {len(validated)} valid questions.")
                    return validated[:num_questions]

            raise ValueError("Validation failed for AI quiz data")

        logger.error(f"LM Studio API error: {response.status_code} - {response.text}")
        return create_fallback_quiz(context)

    except Exception as e:
        logger.error(f"Error in generate_quiz_with_ai: {e}")
        return create_fallback_quiz(context)


def create_fallback_quiz(context):
    context = context.lower()
    questions = []

    if 'bottle' in context or 'plastic' in context:
        questions.extend([
            {
                "question": "How long does a plastic bottle take to decompose?",
                "options": ["50 years", "450 years", "10 years", "100 years"],
                "correct": 1,
                "explanation": "Plastic bottles take approximately 450 years to decompose."
            },
            {
                "question": "What percentage of plastic bottles are recycled globally?",
                "options": ["Less than 30%", "50%", "70%", "90%"],
                "correct": 0,
                "explanation": "Less than 30% of plastic bottles are recycled globally."
            }
        ])

    if 'cigarette' in context or 'butt' in context:
        questions.append({
            "question": "How long do cigarette butts take to decompose?",
            "options": ["1-5 years", "10-12 years", "25 years", "2-3 months"],
            "correct": 1,
            "explanation": "Cigarette butts take 10-12 years due to their filters."
        })

    if 'styrofoam' in context or 'foam' in context:
        questions.append({
            "question": "How long does styrofoam take to break down?",
            "options": ["50 years", "100 years", "500+ years", "Never completely"],
            "correct": 3,
            "explanation": "Styrofoam never completely biodegrades."
        })

    questions.extend([
        {
            "question": "What is the best approach to waste management?",
            "options": ["Reduce, Reuse, Recycle", "Burn everything", "Bury in landfills", "Throw in ocean"],
            "correct": 0,
            "explanation": "Reduce, Reuse, Recycle is the best strategy."
        },
        {
            "question": "Which of these materials is biodegradable?",
            "options": ["Plastic bags", "Apple cores", "Aluminum cans", "Glass bottles"],
            "correct": 1,
            "explanation": "Apple cores are biodegradable; the others are not."
        }
    ])

    return questions[:5]


@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "message": "Flask backend is running"})


@app.route('/models', methods=['GET'])
def get_models():
    return jsonify({"models": get_available_models()})


@app.route('/generate-quiz', methods=['POST'])
def generate_quiz():
    try:
        data = request.get_json()
        if not data or 'context' not in data:
            return jsonify({"error": "Context is required"}), 400

        context = data['context']
        num_questions = data.get('num_questions', 5)
        if not isinstance(num_questions, int) or not (1 <= num_questions <= 10):
            num_questions = 5

        logger.info(f"Generating quiz for: {context}")
        quiz_questions = generate_quiz_with_ai(context, num_questions)

        return jsonify({
            "success": True,
            "quiz": quiz_questions,
            "context": context
        })

    except Exception as e:
        logger.error(f"Server error: {e}")
        return jsonify({"error": "Internal server error"}), 500


if __name__ == '__main__':
    print("✅ Flask server running on http://localhost:5000")
    print("🔁 Ensure LM Studio is running at http://127.0.0.1:1234")
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
