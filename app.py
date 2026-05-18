import os
import re
import json
import mimetypes
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, Response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from PIL import Image
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, and_
from flask_socketio import SocketIO, emit
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv

# Load configuration from .env
load_dotenv()

# Set absolute paths
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# MIME Fix for various environments
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('application/javascript', '.js')

app = Flask(__name__, 
            static_folder=os.path.join(BASE_DIR, 'static'),
            template_folder=os.path.join(BASE_DIR, 'templates'))

# ===================== CONFIGURATION =====================
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'traveller-stop-v7-2026-premium')
# Use PostgreSQL for high-concurrency (5000+ users). Defaults to local SQLite if URL not provided.
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', f'sqlite:///{os.path.join(BASE_DIR, "data", "database.db")}')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 15 * 1024 * 1024 # 15MB limit

db = SQLAlchemy(app)
# SocketIO for real-time updates. eventlet is recommended for production.
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')
oauth = OAuth(app)

# Google OAuth setup
google = oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

# ===================== DATABASE MODELS =====================
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=True) # Optional for OAuth users
    google_id = db.Column(db.String(100), unique=True, nullable=True)
    is_admin = db.Column(db.Boolean, default=False)
    role = db.Column(db.String(20), default='traveler')
    phone = db.Column(db.String(20))
    location = db.Column(db.String(200))
    profession = db.Column(db.String(100))
    qualification = db.Column(db.String(100))
    profile_pic = db.Column(db.String(255))
    nid = db.Column(db.String(50))
    nid_verified = db.Column(db.Boolean, default=False)
    blocked = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    rooms = db.relationship('Room', backref='owner', lazy=True)
    bookings = db.relationship('Booking', backref='user', lazy=True)

class Room(db.Model):
    __tablename__ = 'rooms'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    location = db.Column(db.String(200), nullable=False)
    price_per_night = db.Column(db.Float, nullable=False)
    image_url = db.Column(db.String(255))
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    available_from = db.Column(db.String(50))
    available_to = db.Column(db.String(50))
    amenities = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    bookings = db.relationship('Booking', backref='room', lazy=True)

class Booking(db.Model):
    __tablename__ = 'bookings'
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    checkin = db.Column(db.String(50), nullable=False)
    checkout = db.Column(db.String(50), nullable=False)
    original_amount = db.Column(db.Float)
    total_amount = db.Column(db.Float)
    discount = db.Column(db.Float, default=0)
    coupon_used = db.Column(db.String(50))
    status = db.Column(db.String(20), default='pending_payment')
    payment_status = db.Column(db.String(20), default='unpaid')
    booked_at = db.Column(db.DateTime, default=datetime.utcnow)

class Payment(db.Model):
    __tablename__ = 'payments'
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, nullable=False)
    user_id = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(100))
    method = db.Column(db.String(50))
    amount = db.Column(db.Float)
    sender = db.Column(db.String(100))
    trxid = db.Column(db.String(100), unique=True)
    status = db.Column(db.String(20), default='PENDING')
    time = db.Column(db.DateTime, default=datetime.utcnow)

class SMSPayment(db.Model):
    __tablename__ = 'sms_payments'
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float)
    trxid = db.Column(db.String(100), unique=True)
    raw = db.Column(db.Text)
    time = db.Column(db.DateTime, default=datetime.utcnow)

class EmailPayment(db.Model):
    __tablename__ = 'email_payments'
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float)
    trxid = db.Column(db.String(100), unique=True)
    raw = db.Column(db.Text)
    time = db.Column(db.DateTime, default=datetime.utcnow)

class Coupon(db.Model):
    __tablename__ = 'coupons'
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    discount_percent = db.Column(db.Integer, nullable=False)
    max_uses = db.Column(db.Integer, default=100)
    used = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ===================== REAL-TIME USER TRACKING =====================
online_users_count = 0

@socketio.on('connect')
def handle_connect():
    global online_users_count
    online_users_count += 1
    emit('update_online_users', {'count': online_users_count}, broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    global online_users_count
    if online_users_count > 0:
        online_users_count -= 1
    emit('update_online_users', {'count': online_users_count}, broadcast=True)

# ===================== HELPER FUNCTIONS =====================
def get_user_by_email(email):
    return User.query.filter(User.email.ilike(email)).first()

def get_user_by_id(user_id):
    if not user_id: return None
    return db.session.get(User, user_id)

def is_profile_complete(user):
    if not user: return False
    required = ['location', 'phone', 'profession', 'qualification', 'nid']
    return all(bool(getattr(user, field)) for field in required) and user.nid_verified

def save_uploaded_file(file, folder_type):
    if not file or not file.filename: return None
    ext = file.filename.rsplit('.', 1)[1].lower()
    if ext not in {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}: return None
    
    try:
        filename = secure_filename(file.filename)
        unique_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
        folder = os.path.join(BASE_DIR, 'static/uploads', 'profile_pics' if folder_type == 'profile' else 'rooms')
        os.makedirs(folder, exist_ok=True)
        full_path = os.path.join(folder, unique_name)
        url_path = f"uploads/{'profile_pics' if folder_type == 'profile' else 'rooms'}/{unique_name}"
        
        with Image.open(file) as img:
            if img.mode in ("RGBA", "P"): img = img.convert("RGB")
            # Automatically resize to save space and load faster
            max_size = (1200, 800) if folder_type == 'room' else (400, 400)
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            img.save(full_path, "JPEG", optimize=True, quality=85)
        return url_path
    except Exception as e:
        print(f"❌ Image save error: {e}")
        return None

def is_strong_password(password):
    if len(password) < 8: return False
    if not re.search(r"[a-z]", password): return False
    if not re.search(r"[A-Z]", password): return False
    if not re.search(r"[0-9]", password): return False
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password): return False
    return True

def is_allowed_email(email):
    allowed_domains = ['gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com']
    domain = email.split('@')[-1].lower()
    return domain in allowed_domains

# ===================== JINJA FILTERS & CONTEXT =====================
@app.template_filter('datetimeformat')
def datetimeformat(value, fmt='%Y-%m-%d'):
    if not value: return ""
    if isinstance(value, str):
        try: value = datetime.fromisoformat(value)
        except: return value
    return value.strftime(fmt)

@app.context_processor
def inject_global_data():
    user = get_user_by_id(session.get('user_id'))
    return {
        'current_user': user,
        'profile_complete': is_profile_complete(user),
        'online_users': online_users_count,
        'get_room_image': lambda img: url_for('static', filename=img) if img and not img.startswith('http') else (img or "https://images.unsplash.com/photo-1522771739844-649f6d175d97")
    }

# ===================== MAIN ROUTES =====================
@app.route('/')
def index():
    featured = Room.query.order_by(Room.created_at.desc()).limit(8).all()
    return render_template('index.html', featured_rooms=featured)

@app.route('/rooms')
def rooms_page():
    loc = request.args.get('location', '').strip()
    ci = request.args.get('checkin', '').strip()
    co = request.args.get('checkout', '').strip()
    query = Room.query
    if loc: query = query.filter(Room.location.ilike(f"%{loc}%"))
    rooms = query.all()
    if ci and co:
        # Real-time availability check against database bookings
        rooms = [r for r in rooms if not Booking.query.filter(
            Booking.room_id == r.id, 
            Booking.status.notin_(['cancelled', 'cancel_requested']), 
            or_(and_(Booking.checkin < co, Booking.checkout > ci))
        ).first()]
    return render_template('rooms.html', rooms=rooms, location=loc)

@app.route('/room/<int:room_id>')
def room_detail(room_id):
    room = db.session.get(Room, room_id)
    if not room: return redirect(url_for('rooms_page'))
    return render_template('room_detail.html', room=room)

@app.route('/post-room', methods=['GET', 'POST'])
def post_room():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = get_user_by_id(session['user_id'])
    if not is_profile_complete(user):
        flash('Please complete your profile and NID verification first.', 'warning')
        return redirect(url_for('profile'))
    if request.method == 'POST':
        img = save_uploaded_file(request.files.get('room_image'), 'room')
        room = Room(
            title=request.form['title'], description=request.form['description'],
            location=request.form['location'], price_per_night=float(request.form['price']),
            image_url=img, owner_id=user.id, available_from=request.form.get('available_from'),
            available_to=request.form.get('available_to'), amenities=request.form.getlist('amenities')
        )
        db.session.add(room)
        db.session.commit()
        # Notify all users in real-time about new listing
        socketio.emit('new_room_posted', {'title': room.title, 'location': room.location}, broadcast=True)
        flash('Room posted successfully!', 'success')
        return redirect(url_for('profile'))
    return render_template('post_room.html')

@app.route('/edit-room/<int:room_id>', methods=['GET', 'POST'])
def edit_room(room_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    room = db.session.get(Room, room_id)
    if not room or room.owner_id != session['user_id']: return redirect(url_for('profile'))
    if request.method == 'POST':
        room.title = request.form['title']
        room.description = request.form['description']
        room.location = request.form['location']
        room.price_per_night = float(request.form['price'])
        img = save_uploaded_file(request.files.get('room_image'), 'room')
        if img: room.image_url = img
        db.session.commit()
        flash('Room updated!', 'success')
        return redirect(url_for('profile'))
    return render_template('edit_room.html', room=room)

@app.route('/delete-room/<int:room_id>', methods=['POST'])
def delete_room(room_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    room = db.session.get(Room, room_id)
    if not room or room.owner_id != session['user_id']: return redirect(url_for('profile'))
    if room.image_url and not room.image_url.startswith('http'):
        try: os.remove(os.path.join(BASE_DIR, 'static', room.image_url))
        except: pass
    db.session.delete(room)
    db.session.commit()
    flash('Room deleted!', 'success')
    return redirect(url_for('profile'))

@app.route('/book/<int:room_id>', methods=['POST'])
def book_room(room_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user = get_user_by_id(session['user_id'])
    if not is_profile_complete(user): return redirect(url_for('profile'))
    ci, co = request.form['checkin'], request.form['checkout']
    try:
        nights = (datetime.strptime(co, '%Y-%m-%d') - datetime.strptime(ci, '%Y-%m-%d')).days
    except: return redirect(url_for('room_detail', room_id=room_id))
    
    room = db.session.get(Room, room_id)
    if not room or nights <= 0: return redirect(url_for('room_detail', room_id=room_id))
    
    total = room.price_per_night * nights
    coupon_code = request.form.get('coupon_code', '').upper()
    cp = Coupon.query.filter_by(code=coupon_code, active=True).first()
    discount = (total * cp.discount_percent / 100) if cp and cp.used < cp.max_uses else 0
    if cp and discount > 0: cp.used += 1
    
    booking = Booking(
        room_id=room_id, user_id=user.id, checkin=ci, checkout=co,
        original_amount=total, total_amount=total-discount, discount=discount,
        coupon_used=coupon_code if discount > 0 else None
    )
    db.session.add(booking)
    db.session.commit()
    # Real-time alert for admin dashboard
    socketio.emit('new_booking', {'booking_id': booking.id, 'user': user.username}, broadcast=True)
    return redirect(url_for('payment_page', booking_id=booking.id))

@app.route('/payment/<int:booking_id>')
def payment_page(booking_id):
    b = db.session.get(Booking, booking_id)
    if not b or b.user_id != session.get('user_id'): return redirect(url_for('profile'))
    return render_template('payment.html', booking=b)

@app.route('/pay', methods=['POST'])
def pay():
    if 'user_id' not in session: return jsonify({"status": "failed"})
    bid = int(request.form['booking_id'])
    trx = request.form['trxid'].strip()
    amt = float(request.form['amount'])
    
    if Payment.query.filter_by(trxid=trx).first(): return jsonify({"status": "failed", "msg": "TRXID already used"})
    
    # Auto-verify against webhooks
    verified = SMSPayment.query.filter_by(trxid=trx).first() or EmailPayment.query.filter_by(trxid=trx).first()
    booking = db.session.get(Booking, bid)
    
    status = 'PAID' if (verified and abs(verified.amount - amt) < 0.1) else 'PENDING'
    if request.form['method'] == 'binance': status = 'PENDING' # Manual verification for crypto
    
    p = Payment(booking_id=bid, user_id=session['user_id'], name=request.form['name'],
                method=request.form['method'], amount=amt, sender=request.form['sender'],
                trxid=trx, status=status)
    db.session.add(p)
    if status == 'PAID':
        booking.payment_status, booking.status = 'paid', 'confirmed'
    db.session.commit()
    # Real-time status update for the user
    socketio.emit('payment_status_update', {'booking_id': bid, 'status': status}, broadcast=True)
    return jsonify({"status": "success", "msg": f"Payment {status}."})

@app.route('/cancel-booking/<int:booking_id>', methods=['POST'])
def cancel_booking(booking_id):
    if 'user_id' not in session: return jsonify({"success": False}), 401
    b = db.session.get(Booking, booking_id)
    if not b or b.user_id != session['user_id']: return jsonify({"success": False}), 403
    if b.status in ['cancelled', 'cancel_requested']: return jsonify({"success": False})
    
    if b.payment_status == 'paid': b.status = 'cancel_requested'
    else: b.status = 'cancelled'
    db.session.commit()
    return jsonify({"success": True, "message": "Booking cancellation processed."})

# ===================== AUTHENTICATION ROUTES =====================
@app.route('/login/google')
def google_login():
    redirect_uri = url_for('google_authorize', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/login/google/authorize')
def google_authorize():
    token = google.authorize_access_token()
    user_info = google.get('https://openidconnect.googleapis.com/v1/userinfo').json()
    
    user = User.query.filter_by(google_id=user_info['sub']).first()
    if not user:
        user = User.query.filter_by(email=user_info['email']).first()
        if user:
            user.google_id = user_info['sub']
        else:
            user = User(
                username=user_info['name'],
                email=user_info['email'],
                google_id=user_info['sub'],
                profile_pic=user_info.get('picture', ''),
                role='traveler'
            )
            db.session.add(user)
        db.session.commit()
    
    if user.blocked:
        flash('Account blocked.', 'danger')
        return redirect(url_for('login'))
        
    session.clear()
    session['user_id'], session['username'], session['is_admin'] = user.id, user.username, user.is_admin
    return redirect(url_for('admin' if user.is_admin else 'index'))

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        if not is_allowed_email(email):
            flash('Please use a Gmail, Yahoo, or Outlook address.', 'danger')
            return redirect(url_for('signup'))
        if get_user_by_email(email): 
            flash('Email already registered!', 'danger')
            return redirect(url_for('signup'))
        if not is_strong_password(request.form['password']):
            flash('Password too weak! Use 8+ chars with Upper, Lower, Number, and Special Char.', 'danger')
            return redirect(url_for('signup'))
        u = User(username=request.form['username'], email=email, 
                 password=generate_password_hash(request.form['password']),
                 role=request.form.get('role', 'traveler'))
        db.session.add(u)
        db.session.commit()
        flash('Account created successfully!', 'success')
        return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = get_user_by_email(request.form['email'].strip().lower())
        if u and u.password and check_password_hash(u.password, request.form['password']):
            if u.blocked: 
                flash('Account blocked.', 'danger')
                return redirect(url_for('login'))
            session.clear()
            session['user_id'], session['username'], session['is_admin'] = u.id, u.username, u.is_admin
            return redirect(url_for('admin' if u.is_admin else 'index'))
        flash('Invalid email or password.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('index'))

@app.route('/profile')
def profile():
    u = get_user_by_id(session.get('user_id'))
    if not u: return redirect(url_for('login'))
    return render_template('profile.html', user=u, my_rooms=u.rooms, my_bookings=u.bookings)

@app.route('/update-profile', methods=['POST'])
def update_profile():
    u = get_user_by_id(session.get('user_id'))
    if not u: return jsonify({"success": False})
    for field in ['phone', 'location', 'profession', 'qualification', 'nid']:
        if field in request.form: setattr(u, field, request.form[field])
    img = save_uploaded_file(request.files.get('profile_pic'), 'profile')
    if img: u.profile_pic = img
    db.session.commit()
    return jsonify({"success": True, "message": "Profile updated successfully!"})

# ===================== ADMIN DASHBOARD =====================
@app.route('/admin')
def admin():
    if not session.get('is_admin'): return redirect(url_for('index'))
    return render_template('admin.html', users=User.query.all(), rooms=Room.query.all(),
                           bookings=Booking.query.order_by(Booking.booked_at.desc()).all(), 
                           payments=Payment.query.order_by(Payment.time.desc()).all())

@app.route('/admin/block-user/<int:uid>')
def block_user(uid):
    if not session.get('is_admin'): return redirect(url_for('index'))
    u = db.session.get(User, uid)
    if u: 
        u.blocked = not u.blocked
        db.session.commit()
    return redirect(url_for('admin'))

@app.route('/admin/verify-nid/<int:uid>')
def verify_nid(uid):
    if not session.get('is_admin'): return redirect(url_for('index'))
    u = db.session.get(User, uid)
    if u: 
        u.nid_verified = True
        db.session.commit()
    return redirect(url_for('admin'))

@app.route('/admin/confirm-payment/<int:pid>')
def confirm_payment(pid):
    if not session.get('is_admin'): return redirect(url_for('index'))
    p = db.session.get(Payment, pid)
    if p:
        p.status = 'CONFIRMED'
        b = db.session.get(Booking, p.booking_id)
        if b: b.payment_status, b.status = 'paid', 'confirmed'
        db.session.commit()
    return redirect(url_for('admin'))

@app.route('/admin/approve-cancellation/<int:bid>')
def admin_approve_cancellation(bid):
    if not session.get('is_admin'): return redirect(url_for('index'))
    b = db.session.get(Booking, bid)
    if b and b.status == 'cancel_requested':
        b.status = 'cancelled'
        db.session.commit()
    return redirect(url_for('admin'))

@app.route('/admin/coupons', methods=['GET', 'POST'])
def admin_coupons():
    if not session.get('is_admin'): return redirect(url_for('index'))
    if request.method == 'POST':
        c = Coupon(code=request.form['code'].upper(), discount_percent=int(request.form['discount']),
                   max_uses=int(request.form['max_uses']))
        db.session.add(c)
        db.session.commit()
    return render_template('admin_coupons.html', coupons=Coupon.query.all())

@app.route('/admin/export-users')
def export_users():
    if not session.get('is_admin'): return redirect(url_for('index'))
    def gen():
        yield 'ID,Username,Email,Phone,Location,NID,Verified,Blocked,Joined\n'
        for u in User.query.all():
            yield f"{u.id},{u.username},{u.email},{u.phone or ''},{u.location or ''},{u.nid or ''},{u.nid_verified},{u.blocked},{u.created_at}\n"
    return Response(gen(), mimetype='text/csv', headers={"Content-Disposition": "attachment; filename=ts_users.csv"})

# ===================== WEBHOOKS & STATIC PAGES =====================
@app.route("/sms-webhook", methods=["POST"])
def sms_webhook():
    d = request.json
    if not d or d.get("secret") != app.config['SECRET_KEY']: return jsonify({"status": "err"}), 401
    m = d.get("message", "")
    amt_match = re.search(r'(?:Tk|USDT|Amount|Sent)\s*[:=]?\s*(\d+(?:\.\d+)?)', m, re.I)
    trx_match = re.search(r'(?:TrxID|TxnID|TXID|ID)\s*[:=]?\s*([A-Za-z0-9]+)', m, re.I)
    if amt_match and trx_match:
        if not SMSPayment.query.filter_by(trxid=trx_match.group(1)).first():
            db.session.add(SMSPayment(amount=float(amt_match.group(1)), trxid=trx_match.group(1), raw=m))
            db.session.commit()
            socketio.emit('new_payment_broadcast', {'amount': amt_match.group(1)}, broadcast=True)
    return jsonify({"status": "ok"})

@app.route("/email-webhook", methods=["POST"])
def email_webhook():
    d = request.json
    if not d or d.get("secret") != app.config['SECRET_KEY']: return jsonify({"status": "err"}), 401
    c = d.get("content", "")
    amt_match = re.search(r'(?:Amount|Paid|Received|USDT)\s*[:=]?\s*(\d+(?:\.\d+)?)', c, re.I)
    trx_match = re.search(r'(?:TrxID|TxnID|TXID|Order ID|ID)\s*[:=]?\s*([A-Za-z0-9]+)', c, re.I)
    if amt_match and trx_match:
        if not EmailPayment.query.filter_by(trxid=trx_match.group(1)).first():
            db.session.add(EmailPayment(amount=float(amt_match.group(1)), trxid=trx_match.group(1), raw=c))
            db.session.commit()
            socketio.emit('new_payment_broadcast', {'amount': amt_match.group(1)}, broadcast=True)
    return jsonify({"status": "ok"})

@app.route('/about')
def about(): return render_template('about_us.html')
@app.route('/services')
def services(): return render_template('our_services.html')
@app.route('/contact')
def contact(): return render_template('contact_us.html')
@app.route('/help')
def help_center(): return render_template('help_center.html')
@app.route('/safety-tips')
def safety_tips(): return render_template('safe_tips.html')
@app.route('/terms')
def terms(): return render_template('terms_of_service.html')
@app.route('/privacy')
def privacy(): return render_template('privacy_policy.html')
@app.route('/cancellation-policy')
def cancellation_policy(): return render_template('cancellation_policy.html')

# ===================== INITIALIZATION & START =====================
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        # Initial Admin User
        if not User.query.filter_by(email='admin@travellerstop.com').first():
            db.session.add(User(username='admin', email='admin@travellerstop.com', 
                                password=generate_password_hash('admin123'), 
                                is_admin=True, role='admin', nid_verified=True))
            db.session.commit()
    # socketio.run supports eventlet for high-concurrency production usage
    socketio.run(app, host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=os.getenv('FLASK_DEBUG', 'true').lower() == 'true')
