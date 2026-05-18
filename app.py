import os
import re
import logging
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, Response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from PIL import Image
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, and_
from flask_socketio import SocketIO, emit
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
import threading
import secrets

# ===================== CONFIGURATION =====================
load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__,
            static_folder=os.path.join(BASE_DIR, 'static'),
            template_folder=os.path.join(BASE_DIR, 'templates'))

# ===================== SECURITY CONFIG =====================
app.config['DOMAIN'] = os.getenv('DOMAIN', 'travellerstop.com')
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY') or secrets.token_hex(32)
app.config['DEBUG'] = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
app.config['SESSION_COOKIE_SECURE'] = not app.config['DEBUG']
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 604800
app.config['MAX_CONTENT_LENGTH'] = 15 * 1024 * 1024
app.config['PREFERRED_URL_SCHEME'] = os.getenv('PREFERRED_URL_SCHEME', 'https')

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

@app.before_request
def ensure_https():
    if not app.debug and request.headers.get('X-Forwarded-Proto') == 'http':
        url = request.url.replace("http://", "https://", 1)
        return redirect(url, code=301)

# ===================== DATABASE =====================
db_url = os.getenv('DATABASE_URL')
if not db_url:
    raise RuntimeError("DATABASE_URL not set. Production database (PostgreSQL) is required.")

if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {"pool_pre_ping": True}

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

# ===================== MODELS =====================
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255))
    google_id = db.Column(db.String(100), unique=True)
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

    rooms = db.relationship('Room', backref='owner', lazy=True, cascade="all, delete-orphan")
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

    bookings = db.relationship('Booking', backref='room', lazy=True, cascade="all, delete-orphan")

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

    payments = db.relationship('Payment', backref='booking', lazy=True)

class Payment(db.Model):
    __tablename__ = 'payments'
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(100))
    method = db.Column(db.String(50))
    amount = db.Column(db.Float)
    sender = db.Column(db.String(100))
    trxid = db.Column(db.String(100), unique=True)
    status = db.Column(db.String(20), default='PENDING')
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

# ===================== SOCKETIO =====================
online_users_count = 0
counter_lock = threading.Lock()

@socketio.on('connect')
def handle_connect():
    global online_users_count
    with counter_lock:
        online_users_count += 1
    emit('update_online_users', {'count': max(1, online_users_count)}, broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    global online_users_count
    with counter_lock:
        online_users_count = max(0, online_users_count - 1)
    emit('update_online_users', {'count': max(1, online_users_count)}, broadcast=True)

# ===================== HELPERS =====================
def get_user_by_email(email):
    if not email: return None
    return User.query.filter(User.email.ilike(email.strip())).first()

def get_user_by_id(user_id):
    if not user_id: return None
    try:
        return db.session.get(User, int(user_id))
    except:
        return None

def is_profile_complete(user):
    if not user: return False
    required = ['location', 'phone', 'profession', 'qualification', 'nid']
    return all(bool(getattr(user, field)) for field in required) and user.nid_verified

def save_uploaded_file(file, folder_type):
    if not file or not file.filename:
        return None

    ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
    if ext not in {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}:
        return None

    try:
        filename = secure_filename(file.filename)
        unique_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
        subfolder = 'profile_pics' if folder_type == 'profile' else 'rooms'
        folder = os.path.join(BASE_DIR, 'static', 'uploads', subfolder)
        os.makedirs(folder, exist_ok=True)

        full_path = os.path.join(folder, unique_name)
        url_path = f"uploads/{subfolder}/{unique_name}"

        with Image.open(file) as img:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            max_size = (1200, 800) if folder_type == 'room' else (500, 500)
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            img.save(full_path, "JPEG", optimize=True, quality=85)

        return url_path
    except Exception as e:
        logger.error(f"Image save error: {e}")
        return None

def is_strong_password(password):
    if len(password) < 8: return False
    return all(re.search(r, password) for r in [r"[a-z]", r"[A-Z]", r"[0-9]", r"[!@#$%^&*(),.?\":{}|<>]"])

def is_allowed_email(email):
    allowed = {'gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com', 'icloud.com', 'protonmail.com', 'me.com'}
    return email.split('@')[-1].lower() in allowed

# ===================== TEMPLATE FILTERS & CONTEXT =====================
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
        'online_users': max(1, online_users_count),
        'get_room_image': lambda img: url_for('static', filename=img) if img and not img.startswith('http') else (img or "https://images.unsplash.com/photo-1522771739844-649f6d175d97")
    }

@app.after_request
def add_security_headers(response):
    for k, v in {
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY',
        'X-XSS-Protection': '1; mode=block',
        'Strict-Transport-Security': 'max-age=31536000; includeSubDomains; preload',
        'Referrer-Policy': 'strict-origin-when-cross-origin'
    }.items():
        response.headers[k] = v

    csp = ("default-src 'self'; script-src 'self' https://cdn.jsdelivr.net https://kit.fontawesome.com https://cdnjs.cloudflare.com 'unsafe-inline'; "
           "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com https://use.fontawesome.com; "
           "img-src 'self' data: https:; font-src 'self' https://fonts.gstatic.com https://use.fontawesome.com; "
           "connect-src 'self' wss: https:; frame-src 'self' https://accounts.google.com; object-src 'none'; base-uri 'self'; upgrade-insecure-requests;")
    response.headers['Content-Security-Policy'] = csp
    return response

# ===================== ERROR HANDLERS =====================
@app.errorhandler(404)
def page_not_found(e):
    return render_template('index.html', error_msg="Page not found"), 404

@app.errorhandler(500)
def internal_server_error(e):
    db.session.rollback()
    return render_template('index.html', error_msg="Internal server error"), 500

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
    if loc:
        query = query.filter(Room.location.ilike(f"%{loc}%"))
    rooms = query.all()

    if ci and co:
        filtered = []
        for r in rooms:
            overlap = Booking.query.filter(
                Booking.room_id == r.id,
                Booking.status.notin_(['cancelled', 'cancel_requested']),
                or_(and_(Booking.checkin < co, Booking.checkout > ci))
            ).first()
            if not overlap:
                filtered.append(r)
        rooms = filtered

    return render_template('rooms.html', rooms=rooms, location=loc)

@app.route('/room/<int:room_id>')
def room_detail(room_id):
    room = db.session.get(Room, room_id)
    if not room:
        return redirect(url_for('rooms_page'))
    return render_template('room_detail.html', room=room)

@app.route('/post-room', methods=['GET', 'POST'])
def post_room():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = get_user_by_id(session['user_id'])
    if not user or not is_profile_complete(user):
        flash('Please complete your profile and NID verification first.', 'warning')
        return redirect(url_for('profile'))

    if request.method == 'POST':
        try:
            img = save_uploaded_file(request.files.get('room_image'), 'room')
            room = Room(
                title=request.form['title'],
                description=request.form['description'],
                location=request.form['location'],
                price_per_night=float(request.form['price']),
                image_url=img,
                owner_id=user.id,
                available_from=request.form.get('available_from'),
                available_to=request.form.get('available_to'),
                amenities=request.form.getlist('amenities')
            )
            db.session.add(room)
            db.session.commit()
            socketio.emit('new_room_posted', {'title': room.title, 'location': room.location}, broadcast=True)
            flash('Room posted successfully!', 'success')
            return redirect(url_for('profile'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error posting room: {str(e)}', 'danger')
    return render_template('post_room.html')

@app.route('/edit-room/<int:room_id>', methods=['GET', 'POST'])
def edit_room(room_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    room = db.session.get(Room, room_id)
    if not room or room.owner_id != session['user_id']:
        return redirect(url_for('profile'))
    if request.method == 'POST':
        try:
            for f in ['title', 'description', 'location', 'available_from', 'available_to']:
                setattr(room, f, request.form.get(f))
            room.price_per_night = float(request.form['price'])
            room.amenities = request.form.getlist('amenities')
            img = save_uploaded_file(request.files.get('room_image'), 'room')
            if img:
                room.image_url = img
            db.session.commit()
            flash('Room updated successfully!', 'success')
            return redirect(url_for('profile'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating room: {str(e)}', 'danger')
    return render_template('edit_room.html', room=room)

@app.route('/delete-room/<int:room_id>', methods=['POST'])
def delete_room(room_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    room = db.session.get(Room, room_id)
    if not room or room.owner_id != session['user_id']:
        return redirect(url_for('profile'))
    try:
        if room.image_url and not room.image_url.startswith('http'):
            try:
                os.remove(os.path.join(BASE_DIR, 'static', room.image_url))
            except: pass
        db.session.delete(room)
        db.session.commit()
        flash('Room deleted!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting room: {str(e)}', 'danger')
    return redirect(url_for('profile'))

@app.route('/book/<int:room_id>', methods=['POST'])
def book_room(room_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user = get_user_by_id(session['user_id'])
    if not user or not is_profile_complete(user):
        flash('Please complete your profile first.', 'warning')
        return redirect(url_for('profile'))

    ci, co = request.form.get('checkin'), request.form.get('checkout')
    try:
        nights = (datetime.strptime(co, '%Y-%m-%d') - datetime.strptime(ci, '%Y-%m-%d')).days
    except:
        flash('Invalid date format.', 'danger')
        return redirect(url_for('room_detail', room_id=room_id))

    room = db.session.get(Room, room_id)
    if not room or nights <= 0:
        flash('Invalid room or duration.', 'danger')
        return redirect(url_for('room_detail', room_id=room_id))

    total = room.price_per_night * nights
    coupon_code = request.form.get('coupon_code', '').strip().upper()
    discount = 0
    if coupon_code:
        cp = Coupon.query.filter_by(code=coupon_code, active=True).first()
        if cp and cp.used < cp.max_uses:
            discount = (total * cp.discount_percent / 100)
            cp.used += 1

    try:
        booking = Booking(
            room_id=room_id, user_id=user.id, checkin=ci, checkout=co,
            original_amount=total, total_amount=total - discount,
            discount=discount, coupon_used=coupon_code if discount > 0 else None
        )
        db.session.add(booking)
        db.session.commit()
        flash('Booking created successfully! Please complete payment.', 'success')
        return redirect(url_for('payment_page', booking_id=booking.id))
    except Exception as e:
        db.session.rollback()
        flash(f'Booking failed: {str(e)}', 'danger')
        return redirect(url_for('room_detail', room_id=room_id))

@app.route('/payment/<int:booking_id>')
def payment_page(booking_id):
    b = db.session.get(Booking, booking_id)
    if not b or b.user_id != session.get('user_id'):
        return redirect(url_for('profile'))
    return render_template('payment.html', booking=b)

@app.route('/pay', methods=['POST'])
def pay():
    if 'user_id' not in session:
        return jsonify({"status": "failed", "msg": "Session expired"}), 401
    try:
        bid = int(request.form.get('booking_id', 0))
        trx = request.form.get('trxid', '').strip()
        amt = float(request.form.get('amount', 0))
        method = request.form.get('method', 'manual')
        name = request.form.get('name', 'User')
        sender = request.form.get('sender', '')

        if not trx:
            return jsonify({"status": "failed", "msg": "Transaction ID is required"})
        if Payment.query.filter_by(trxid=trx).first():
            return jsonify({"status": "failed", "msg": "TRXID already used"})

        booking = db.session.get(Booking, bid)
        if not booking:
            return jsonify({"status": "failed", "msg": "Booking not found"})

        p = Payment(
            booking_id=bid, user_id=session['user_id'], name=name,
            method=method, amount=amt, sender=sender, trxid=trx, status='PENDING'
        )
        db.session.add(p)
        db.session.commit()
        socketio.emit('payment_status_update', {'booking_id': bid, 'status': 'PENDING'}, broadcast=True)
        return jsonify({"status": "success", "msg": "Payment submitted successfully. Waiting for admin confirmation."})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Payment error: {e}")
        return jsonify({"status": "failed", "msg": "Server error"})

@app.route('/cancel-booking/<int:booking_id>', methods=['POST'])
def cancel_booking(booking_id):
    if 'user_id' not in session:
        return jsonify({"success": False}), 401
    b = db.session.get(Booking, booking_id)
    if not b or b.user_id != session['user_id']:
        return jsonify({"success": False}), 403
    if b.status in ['cancelled', 'cancel_requested']:
        return jsonify({"success": False})
    try:
        b.status = 'cancel_requested' if b.payment_status == 'paid' else 'cancelled'
        db.session.commit()
        return jsonify({"success": True, "message": "Cancellation request submitted."})
    except:
        db.session.rollback()
        return jsonify({"success": False, "message": "Database error"})

# ===================== AUTH ROUTES =====================
@app.route('/login/google')
def google_login():
    return google.authorize_redirect(url_for('google_authorize', _external=True))

@app.route('/login/google/authorize')
def google_authorize():
    try:
        token = google.authorize_access_token()
        info = token.get('userinfo') or google.get('https://openidconnect.googleapis.com/v1/userinfo').json()
        email = info.get('email')
        if not email:
            flash("Could not retrieve email from Google.", "warning")
            return redirect(url_for('login'))

        user = User.query.filter_by(google_id=info.get('sub')).first()
        if not user:
            user = User.query.filter_by(email=email).first()
            if user:
                user.google_id = info.get('sub')
            else:
                user = User(
                    username=info.get('name', email.split('@')[0]),
                    email=email,
                    google_id=info.get('sub'),
                    profile_pic=info.get('picture', ''),
                    role='traveler'
                )
                db.session.add(user)
            db.session.commit()

        if user.blocked:
            flash('Account blocked.', 'danger')
            return redirect(url_for('login'))

        session.clear()
        session['user_id'] = user.id
        session['username'] = user.username
        session['is_admin'] = user.is_admin
        return redirect(url_for('admin' if user.is_admin else 'profile'))
    except Exception as e:
        logger.error(f"Google Auth Error: {e}")
        flash("Google authentication failed.", "warning")
        return redirect(url_for('login'))

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if not is_allowed_email(email):
            flash('Please use a valid email address.', 'danger')
            return redirect(url_for('signup'))
        if get_user_by_email(email):
            flash('Email already registered!', 'danger')
            return redirect(url_for('signup'))
        if not is_strong_password(password):
            flash('Password too weak!', 'danger')
            return redirect(url_for('signup'))

        try:
            u = User(username=username, email=email, password=generate_password_hash(password))
            db.session.add(u)
            db.session.commit()
            flash('Account created! Please login.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            flash(f'Signup failed: {str(e)}', 'danger')
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        u = get_user_by_email(email)
        if u and u.password and check_password_hash(u.password, password):
            if u.blocked:
                flash('Account blocked.', 'danger')
                return redirect(url_for('login'))
            session.clear()
            session['user_id'] = u.id
            session['username'] = u.username
            session['is_admin'] = u.is_admin
            return redirect(url_for('admin' if u.is_admin else 'profile'))
        flash('Invalid email or password.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('index'))

# ===================== PROFILE =====================
@app.route('/profile')
def profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = get_user_by_id(session.get('user_id'))
    if not user:
        session.clear()
        return redirect(url_for('login'))
    return render_template('profile.html', user=user, my_rooms=user.rooms, my_bookings=user.bookings)

@app.route('/update-profile', methods=['POST'])
def update_profile():
    if 'user_id' not in session:
        return jsonify({"success": False, "message": "Please login first."}), 401

    user = get_user_by_id(session.get('user_id'))
    if not user:
        return jsonify({"success": False, "message": "User not found."}), 404

    try:
        # Text fields
        for field in ['phone', 'location', 'profession', 'qualification', 'nid']:
            if field in request.form:
                val = request.form.get(field, '').strip()
                setattr(user, field, val if val else None)

        # Profile Picture
        file = request.files.get('profile_pic')
        if file and file.filename:
            uploaded = save_uploaded_file(file, 'profile')
            if uploaded:
                user.profile_pic = uploaded
            else:
                return jsonify({"success": False, "message": "Invalid image format."}), 400

        db.session.commit()
        return jsonify({
            "success": True,
            "message": "Profile updated successfully!",
            "profile_pic": url_for('static', filename=user.profile_pic) if user.profile_pic else None
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"UPDATE PROFILE ERROR: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500

# ===================== ADMIN ROUTES =====================
@app.route('/admin')
def admin():
    if not session.get('is_admin'):
        return redirect(url_for('index'))
    return render_template('admin.html',
                         users=User.query.all(),
                         rooms=Room.query.all(),
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
        if b:
            b.payment_status = 'paid'
            b.status = 'confirmed'
        db.session.commit()
    return redirect(url_for('admin'))

@app.route('/admin/manual-confirm-booking/<int:bid>')
def admin_manual_confirm(bid):
    if not session.get('is_admin'): return redirect(url_for('index'))
    b = db.session.get(Booking, bid)
    if b:
        b.payment_status = 'paid'
        b.status = 'confirmed'
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
        try:
            c = Coupon(
                code=request.form['code'].upper(),
                discount_percent=int(request.form['discount']),
                max_uses=int(request.form['max_uses'])
            )
            db.session.add(c)
            db.session.commit()
            flash('Coupon created successfully!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
    return render_template('admin_coupons.html', coupons=Coupon.query.all())

@app.route('/admin/export-users')
def export_users():
    if not session.get('is_admin'): return redirect(url_for('index'))
    def generate():
        yield 'ID,Username,Email,Phone,Location,NID,Verified,Blocked,Joined\n'
        for u in User.query.all():
            yield f"{u.id},{u.username},{u.email},{u.phone or ''},{u.location or ''},{u.nid or ''},{u.nid_verified},{u.blocked},{u.created_at}\n"
    return Response(generate(), mimetype='text/csv', headers={"Content-Disposition": "attachment; filename=users.csv"})

# ===================== STATIC PAGES =====================
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

# ===================== INIT =====================
def init_db():
    with app.app_context():
        db.create_all()
        admin_email = os.getenv('ADMIN_EMAIL', 'admin@travellerstop.com')
        admin_pass = os.getenv('ADMIN_PASSWORD', 'admin123')
        if not User.query.filter_by(email=admin_email).first():
            admin = User(
                username='Admin',
                email=admin_email,
                password=generate_password_hash(admin_pass),
                is_admin=True,
                role='admin',
                nid_verified=True
            )
            db.session.add(admin)
            db.session.commit()
            logger.info(f"✅ Admin account created: {admin_email}")

if __name__ == '__main__':
    init_db()
    socketio.run(app, host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=app.config['DEBUG'])
else:
    init_db()
