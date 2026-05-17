from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
import json, os, hashlib, uuid, time, threading, webbrowser
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'chatpilot_secret_2024'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

USERS_FILE = 'users.json'

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# ── PAGES ──
@app.route('/')
def welcome():
    return render_template('welcome.html')

@app.route('/login')
def login_page():
    return render_template('auth.html')

@app.route('/register')
def register_page():
    return render_template('auth.html')

@app.route('/chat')
def chat_page():
    if 'user' not in session:
        return redirect(url_for('login_page'))
    return render_template('chat.html', user=session['user'])

@app.route('/get_user')
def get_user():
    if 'user' not in session:
        return jsonify({'success': False})
    return jsonify({'success': True, 'user': session['user']})

# ── AUTH ──
@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    email = data.get('email', '').lower().strip()
    password = data.get('password', '')
    users = load_users()
    user = users.get(email)
    if not user or user['password'] != hash_password(password):
        return jsonify({'success': False, 'message': 'Invalid email or password.'})
    session['user'] = {'email': email, 'name': user['name'], 'username': user['username']}
    return jsonify({'success': True})

@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    name = data.get('name', '').strip()
    username = data.get('username', '').strip().lower()
    email = data.get('email', '').lower().strip()
    password = data.get('password', '')
    if not all([name, username, email, password]):
        return jsonify({'success': False, 'message': 'All fields are required.'})
    if len(password) < 6:
        return jsonify({'success': False, 'message': 'Password must be at least 6 characters.'})
    users = load_users()
    if email in users:
        return jsonify({'success': False, 'message': 'Email already registered.'})
    for u in users.values():
        if u['username'] == username:
            return jsonify({'success': False, 'message': 'Username already taken.'})
    users[email] = {
        'name': name,
        'username': username,
        'email': email,
        'password': hash_password(password),
        'friends': [],
        'friend_requests': [],
        'profile_id': str(uuid.uuid4())[:8]
    }
    save_users(users)
    session['user'] = {'email': email, 'name': name, 'username': username}
    return jsonify({'success': True})

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('welcome'))

# ── AI SUGGESTIONS ──
@app.route('/get_suggestions', methods=['POST'])
def get_suggestions():
    import urllib.request
    data = request.get_json()
    messages = data.get('messages', [])
    tone = data.get('tone', 'friend')

    tone_instructions = {
        'friend': 'casual, fun, friendly tone with emojis. Like texting a close friend.',
        'teacher': 'very respectful, polite and formal tone. Like messaging a professor or teacher.',
        'business': 'professional, clear and confident tone. Like a business email or formal chat.'
    }

    tone_desc = tone_instructions.get(tone, tone_instructions['friend'])
    chat_history = '\n'.join([
        f"{'Me' if m['role'] == 'assistant' else 'Them'}: {m['content']}"
        for m in messages[-10:]
    ])

    prompt = f"""You are a smart AI chat assistant helping someone reply in a conversation.

Tone to use: {tone_desc}

Here is the conversation so far:
{chat_history}

Based on this EXACT conversation above, suggest 3 smart natural reply options for "Me" to send next.

STRICT RULES:
- Read the conversation carefully and understand what the other person just said
- Each suggestion must DIRECTLY respond to the last message
- Match the tone: {tone_desc}
- Each suggestion max 12 words
- Sound like a real human wrote them
- Return ONLY a valid JSON array of exactly 3 strings
- No explanation, no markdown, just the JSON array

Example format: ["reply one", "reply two", "reply three"]"""

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}]
    }).encode('utf-8')

    gemini_key = os.environ.get('GEMINI_API_KEY', '')
    req = urllib.request.Request(
        f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}',
        data=payload,
        headers={'Content-Type': 'application/json'}
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            text = result['candidates'][0]['content']['parts'][0]['text'].strip()
            text = text.replace('```json', '').replace('```', '').strip()
            suggestions = json.loads(text)
            return jsonify({'suggestions': suggestions})
    except Exception as e:
        import traceback
        print(f"❌ AI Error: {e}")
        print(traceback.format_exc())
        fallbacks = {
            'friend': ["Sounds good! 🙌", "Can't make it 😅", "Tell me more 👀"],
            'teacher': ["Thank you, I will do that.", "Could you clarify please?", "I understand, thank you."],
            'business': ["Understood, I'll get on it.", "Could we schedule a call?", "Thank you for the update."]
        }
        return jsonify({'suggestions': fallbacks.get(tone, fallbacks['friend'])})

# ── FRIENDS ──
@app.route('/search_user')
def search_user():
    if 'user' not in session:
        return jsonify({'success': False})
    query = request.args.get('q', '').lower().strip()
    users = load_users()
    current_email = session['user']['email']
    results = []
    for email, u in users.items():
        if email == current_email:
            continue
        if query in u['username'].lower() or query in u['name'].lower():
            results.append({
                'name': u['name'],
                'username': u['username'],
                'profile_id': u['profile_id']
            })
    return jsonify({'success': True, 'users': results[:5]})

@app.route('/add/<profile_id>')
def add_friend_page(profile_id):
    if 'user' not in session:
        return redirect(url_for('login_page'))
    users = load_users()
    target_user = None
    for email, u in users.items():
        if u['profile_id'] == profile_id:
            target_user = u
            break
    if not target_user:
        return "User not found!", 404
    return render_template('add_friend.html', target=target_user)

@app.route('/send_friend_request', methods=['POST'])
def send_friend_request():
    if 'user' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'})
    data = request.get_json()
    target_profile_id = data.get('profile_id')
    users = load_users()
    current_email = session['user']['email']
    target_email = None
    for email, u in users.items():
        if u['profile_id'] == target_profile_id:
            target_email = email
            break
    if not target_email:
        return jsonify({'success': False, 'message': 'User not found'})
    if target_email == current_email:
        return jsonify({'success': False, 'message': 'You cannot add yourself!'})
    if 'friend_requests' not in users[target_email]:
        users[target_email]['friend_requests'] = []
    for req in users[target_email]['friend_requests']:
        if req['email'] == current_email:
            return jsonify({'success': False, 'message': 'Request already sent!'})
    if current_email in users[target_email].get('friends', []):
        return jsonify({'success': False, 'message': 'Already friends!'})
    users[target_email]['friend_requests'].append({
        'email': current_email,
        'name': session['user']['name'],
        'username': session['user']['username']
    })
    save_users(users)
    return jsonify({'success': True, 'message': 'Friend request sent!'})

@app.route('/get_friend_requests')
def get_friend_requests():
    if 'user' not in session:
        return jsonify({'success': False})
    users = load_users()
    current_email = session['user']['email']
    if current_email not in users:
        session.clear()
        return jsonify({'success': False})
    requests = users[current_email].get('friend_requests', [])
    return jsonify({'success': True, 'requests': requests})
@app.route('/accept_friend', methods=['POST'])
def accept_friend():
    if 'user' not in session:
        return jsonify({'success': False})
    data = request.get_json()
    friend_email = data.get('email')
    users = load_users()
    current_email = session['user']['email']
    if 'friends' not in users[current_email]:
        users[current_email]['friends'] = []
    if 'friends' not in users[friend_email]:
        users[friend_email]['friends'] = []
    if friend_email not in users[current_email]['friends']:
        users[current_email]['friends'].append(friend_email)
    if current_email not in users[friend_email]['friends']:
        users[friend_email]['friends'].append(current_email)
    users[current_email]['friend_requests'] = [
        r for r in users[current_email].get('friend_requests', [])
        if r['email'] != friend_email
    ]
    save_users(users)
    return jsonify({'success': True})

@app.route('/get_friends')
def get_friends():
    if 'user' not in session:
        return jsonify({'success': False})
    users = load_users()
    current_email = session['user']['email']
    if current_email not in users:
        session.clear()
        return jsonify({'success': False, 'redirect': '/login'})
    friend_emails = users[current_email].get('friends', [])

@app.route('/get_profile')
def get_profile():
    if 'user' not in session:
        return jsonify({'success': False})
    users = load_users()
    current_email = session['user']['email']
    profile_id = users[current_email]['profile_id']
    return jsonify({'success': True, 'profile_id': profile_id})

# ── SOCKET EVENTS ──
@socketio.on('connect')
def on_connect():
    print(f"🔌 SOMEONE CONNECTED - sid: {request.sid}")
    if 'user' in session:
        my_email = session['user']['email']
        join_room(my_email)
        print(f"✅ {my_email} connected and joined room")
    else:
        print("⚠️ Connected but NO SESSION!")

@socketio.on('disconnect')
def on_disconnect():
    print(f"❌ Client disconnected")

@socketio.on('join')
def on_join(data):
    if 'user' in session:
        my_email = session['user']['email']
        join_room(my_email)
        print(f"✅ {my_email} joined room")

@socketio.on('message')
def on_message(data):
    if 'user' not in session:
        return
    my_email = session['user']['email']
    friend_email = data.get('room', '')
    sender_name = session['user']['name']
    text = data.get('text', '')
    if friend_email and friend_email != my_email:
        emit('message', {
            'text': text,
            'sender': sender_name
        }, to=friend_email)
        print(f"📨 {my_email} → {friend_email}: {text}")

# ── RUN ──
if __name__ == '__main__':
    def open_browser():
        time.sleep(1.5)
        webbrowser.open('http://127.0.0.1:5000')
    threading.Thread(target=open_browser).start()

    try:
        from pyngrok import ngrok
        tunnels = ngrok.get_tunnels()
        for tunnel in tunnels:
            ngrok.disconnect(tunnel.public_url)
        ngrok.kill()
        time.sleep(1)
        public_url = ngrok.connect(5000)
        clean_url = public_url.public_url
        print("\n" + "="*50)
        print("🚀 ChatPilot AI is LIVE!")
        print(f"🌍 Public URL: {clean_url}")
        print("👆 Share this link with anyone!")
        print("="*50 + "\n")
    except Exception as e:
        print("\n" + "="*50)
        print("🚀 ChatPilot AI is LIVE!")
        print("💻 Local URL: http://127.0.0.1:5000")
        print("="*50 + "\n")

    socketio.run(app, debug=False, allow_unsafe_werkzeug=True)