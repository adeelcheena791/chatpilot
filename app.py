from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
import json, os, hashlib, uuid, time, threading, webbrowser
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'chatpilot_secret_2024'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ── SUPABASE ──
from supabase import create_client
SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY = os.environ.get('SUPABASE_ANON_KEY', '')
db = create_client(SUPABASE_URL, SUPABASE_KEY)

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
    try:
        result = db.table('users').select('*').eq('email', email).execute()
        if not result.data:
            return jsonify({'success': False, 'message': 'Invalid email or password.'})
        user = result.data[0]
        if user['password'] != hash_password(password):
            return jsonify({'success': False, 'message': 'Invalid email or password.'})
        session['user'] = {'email': email, 'name': user['name'], 'username': user['username']}
        return jsonify({'success': True})
    except Exception as e:
        print(f"Login error: {e}")
        return jsonify({'success': False, 'message': 'Server error.'})

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
    try:
        # Check email exists
        existing = db.table('users').select('email').eq('email', email).execute()
        if existing.data:
            return jsonify({'success': False, 'message': 'Email already registered.'})
        # Check username exists
        existing_username = db.table('users').select('username').eq('username', username).execute()
        if existing_username.data:
            return jsonify({'success': False, 'message': 'Username already taken.'})
        # Create user
        profile_id = str(uuid.uuid4())[:8]
        db.table('users').insert({
            'email': email,
            'name': name,
            'username': username,
            'password': hash_password(password),
            'profile_id': profile_id
        }).execute()
        session['user'] = {'email': email, 'name': name, 'username': username}
        return jsonify({'success': True})
    except Exception as e:
        print(f"Register error: {e}")
        return jsonify({'success': False, 'message': 'Server error.'})

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
            raw = resp.read()
            print(f"✅ Gemini response: {raw[:300]}")
            result = json.loads(raw)
            text = result['candidates'][0]['content']['parts'][0]['text'].strip()
            text = text.replace('```json', '').replace('```', '').strip()
            suggestions = json.loads(text)
            print(f"✅ Suggestions: {suggestions}")
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
    current_email = session['user']['email']
    try:
        result = db.table('users').select('name, username, profile_id').execute()
        users = result.data
        filtered = [
            u for u in users
            if u.get('username') and (query in u['username'].lower() or query in u['name'].lower())
            and u.get('email', '') != current_email
        ]
        return jsonify({'success': True, 'users': filtered[:5]})
    except Exception as e:
        print(f"Search error: {e}")
        return jsonify({'success': True, 'users': []})

@app.route('/add/<profile_id>')
def add_friend_page(profile_id):
    if 'user' not in session:
        return redirect(url_for('login_page'))
    try:
        result = db.table('users').select('*').eq('profile_id', profile_id).execute()
        if not result.data:
            return "User not found!", 404
        target_user = result.data[0]
        return render_template('add_friend.html', target=target_user)
    except Exception as e:
        return "Error!", 500

@app.route('/send_friend_request', methods=['POST'])
def send_friend_request():
    if 'user' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'})
    data = request.get_json()
    target_profile_id = data.get('profile_id')
    current_email = session['user']['email']
    try:
        # Find target user
        result = db.table('users').select('*').eq('profile_id', target_profile_id).execute()
        if not result.data:
            return jsonify({'success': False, 'message': 'User not found'})
        target = result.data[0]
        target_email = target['email']
        if target_email == current_email:
            return jsonify({'success': False, 'message': 'Cannot add yourself!'})
        # Check already friends
        existing = db.table('friends').select('*').eq('user_email', current_email).eq('friend_email', target_email).execute()
        if existing.data:
            return jsonify({'success': False, 'message': 'Already friends!'})
        # Check request already sent
        existing_req = db.table('friend_requests').select('*').eq('from_email', current_email).eq('to_email', target_email).execute()
        if existing_req.data:
            return jsonify({'success': False, 'message': 'Request already sent!'})
        # Send request
        db.table('friend_requests').insert({
            'from_email': current_email,
            'from_name': session['user']['name'],
            'from_username': session['user']['username'],
            'to_email': target_email
        }).execute()
        return jsonify({'success': True, 'message': 'Friend request sent!'})
    except Exception as e:
        print(f"Friend request error: {e}")
        return jsonify({'success': False, 'message': 'Server error.'})

@app.route('/get_friend_requests')
def get_friend_requests():
    if 'user' not in session:
        return jsonify({'success': False})
    current_email = session['user']['email']
    try:
        result = db.table('friend_requests').select('*').eq('to_email', current_email).execute()
        requests = [{'email': r['from_email'], 'name': r['from_name'], 'username': r['from_username']} for r in result.data]
        return jsonify({'success': True, 'requests': requests})
    except Exception as e:
        print(f"Get requests error: {e}")
        return jsonify({'success': True, 'requests': []})

@app.route('/accept_friend', methods=['POST'])
def accept_friend():
    if 'user' not in session:
        return jsonify({'success': False})
    data = request.get_json()
    friend_email = data.get('email')
    current_email = session['user']['email']
    try:
        # Add both as friends
        db.table('friends').insert({'user_email': current_email, 'friend_email': friend_email}).execute()
        db.table('friends').insert({'user_email': friend_email, 'friend_email': current_email}).execute()
        # Delete request
        db.table('friend_requests').delete().eq('from_email', friend_email).eq('to_email', current_email).execute()
        return jsonify({'success': True})
    except Exception as e:
        print(f"Accept friend error: {e}")
        return jsonify({'success': False, 'message': 'Error.'})

@app.route('/get_friends')
def get_friends():
    if 'user' not in session:
        return jsonify({'success': False})
    current_email = session['user']['email']
    try:
        result = db.table('friends').select('friend_email').eq('user_email', current_email).execute()
        friends_list = []
        for row in result.data:
            friend_email = row['friend_email']
            user_result = db.table('users').select('name, username, email, profile_id').eq('email', friend_email).execute()
            if user_result.data:
                friends_list.append(user_result.data[0])
        return jsonify({'success': True, 'friends': friends_list})
    except Exception as e:
        print(f"Get friends error: {e}")
        return jsonify({'success': True, 'friends': []})

@app.route('/get_profile')
def get_profile():
    if 'user' not in session:
        return jsonify({'success': False})
    current_email = session['user']['email']
    try:
        result = db.table('users').select('profile_id').eq('email', current_email).execute()
        if result.data:
            return jsonify({'success': True, 'profile_id': result.data[0]['profile_id']})
        return jsonify({'success': False})
    except Exception as e:
        return jsonify({'success': False})

# ── SOCKET EVENTS ──
@socketio.on('connect')
def on_connect():
    if 'user' in session:
        my_email = session['user']['email']
        join_room(my_email)
        print(f"✅ {my_email} connected")

@socketio.on('disconnect')
def on_disconnect():
    print(f"❌ Client disconnected")

@socketio.on('join')
def on_join(data):
    if 'user' in session:
        my_email = session['user']['email']
        join_room(my_email)

@socketio.on('message')
def on_message(data):
    if 'user' not in session:
        return
    my_email = session['user']['email']
    friend_email = data.get('room', '')
    sender_name = session['user']['name']
    text = data.get('text', '')
    if friend_email and friend_email != my_email:
        emit('message', {'text': text, 'sender': sender_name}, to=friend_email)
        print(f"📨 {my_email} → {friend_email}: {text}")

# ── RUN ──
if __name__ == '__main__':
    def open_browser():
        time.sleep(1.5)
        webbrowser.open('http://127.0.0.1:5000')
    threading.Thread(target=open_browser).start()
    socketio.run(app, debug=False, allow_unsafe_werkzeug=True)