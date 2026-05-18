import os
import re
import json
import mimetypes
import logging
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, Response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from PIL import Image
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, and_, text
from sqlalchemy.exc import IntegrityError
from flask_socketio import SocketIO, emit
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
import threading

# Load configuration from .env
load_dotenv()

# Set absolute paths
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Logging Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# OAuth insecure transport for local dev
if os.getenv('FLASK_DEBUG', 'true').lower() == 'true':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# MIME Fix for various environments
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('application/javascript', '.js')

app = Flask(__name__, 
            static_folder=os.path.join(BASE_DIR, 'static'),
            template_folder=os.path.join(BASE_DIR, 'templates'))

# Handle proxy headers (required for Google Auth over HTTPS)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

# ===================== CONFIGURATION =====================
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'traveller-stop-v7-2026-premium')

# Security Headers & Cookies
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=604800, # 1 week
)

@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    # Content Security Policy - Allow Google Auth & common CDNs
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://kit.fontawesome.com https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com https://use.fontawesome.com; "
        "img-src 'self' data: https:; "
        "font-src 'self' https://fonts.gstatic.com https://use.fontawesome.com; "
        "connect-src 'self' https://ka-f.fontawesome.com; "
        "frame-src 'self' https://accounts.google.com; "
        "object-src 'none';"
    )
    response.headers['Content-Security-Policy'] = csp
    return response

# Strictly use PostgreSQL
db_url = os.getenv('DATABASE_URL')
if not db_url:
    # Fallback to SQLite for local development if PostgreSQL is missing
    logger.warning("DATABASE_URL is not set. Falling back to SQLite for local development.")
    db_url = "sqlite:///" + os.path.join(BASE_DIR, 'instance', 'travellerstop.db')
elif db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
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
    password = db.Column(db.String(255), nullable=True) # Increased size for better hash support
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
counter_lock = threading.Lock()

@socketio.on('connect')
def handle_connect():
    global online_users_count
    with counter_lock:
        online_users_count += 1
    emit('update_online_users', {'count': online_users_count}, broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    global online_users_count
    with counter_lock:
        if online_users_count > 0:
            online_users_count -= 1
    emit('update_online_users', {'count': online_users_count}, broadcast=True)

# ===================== HELPER FUNCTIONS =====================
def get_user_by_email(email):
    if not email: return None
    return User.query.filter(User.email.ilike(email.strip())).first()

def get_user_by_id(user_id):
    if not user_id: return None
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None

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
        # Prevent overly long filenames
        if len(filename) > 100:
            filename = filename[-100:]
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
        logger.error(f"❌ Image save error: {e}")
        return None

def is_strong_password(password):
    if len(password) < 8: return False
    if not re.search(r"[a-z]", password): return False
    if not re.search(r"[A-Z]", password): return False
    if not re.search(r"[0-9]", password): return False
    # More inclusive special character check
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password): return False
    return True

def is_allowed_email(email):
    # Expanded list of common domains
    allowed_domains = ['gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com', 'icloud.com', 'protonmail.com', 'me.com']
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
    if not user: return redirect(url_for('login'))
    
    if not is_profile_complete(user):
        flash('Please complete your profile and NID verification first.', 'warning')
        return redirect(url_for('profile'))
        
    if request.method == 'POST':
        try:
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
        except Exception as e:
            db.session.rollback()
            flash(f'Error posting room: {str(e)}', 'danger')
            
    return render_template('post_room.html')

@app.route('/edit-room/<int:room_id>', methods=['GET', 'POST'])
def edit_room(room_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    room = db.session.get(Room, room_id)
    if not room or room.owner_id != session['user_id']: return redirect(url_for('profile'))
    
    if request.method == 'POST':
        try:
            room.title = request.form['title']
            room.description = request.form['description']
            room.location = request.form['location']
            room.price_per_night = float(request.form['price'])
            room.available_from = request.form.get('available_from')
            room.available_to = request.form.get('available_to')
            room.amenities = request.form.getlist('amenities')
            
            img = save_uploaded_file(request.files.get('room_image'), 'room')
            if img: room.image_url = img
            
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
    if not room or room.owner_id != session['user_id']: return redirect(url_for('profile'))
    
    try:
        if room.image_url and not room.image_url.startswith('http'):
            try: os.remove(os.path.join(BASE_DIR, 'static', room.image_url))
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
    if not user: return redirect(url_for('login'))
    if not is_profile_complete(user): 
        flash('Please complete your profile first.', 'warning')
        return redirect(url_for('profile'))
        
    ci, co = request.form.get('checkin'), request.form.get('checkout')
    if not ci or not co:
        flash('Invalid dates selected.', 'danger')
        return redirect(url_for('room_detail', room_id=room_id))
        
    try:
        nights = (datetime.strptime(co, '%Y-%m-%d') - datetime.strptime(ci, '%Y-%m-%d')).days
    except Exception:
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
            original_amount=total, total_amount=total-discount, discount=discount,
            coupon_used=coupon_code if discount > 0 else None
        )
        db.session.add(booking)
        db.session.commit()
        # Real-time alert for admin dashboard
        socketio.emit('new_booking', {'booking_id': booking.id, 'user': user.username}, broadcast=True)
        return redirect(url_for('payment_page', booking_id=booking.id))
    except Exception as e:
        db.session.rollback()
        flash(f'Booking failed: {str(e)}', 'danger')
        return redirect(url_for('room_detail', room_id=room_id))

@app.route('/payment/<int:booking_id>')
def payment_page(booking_id):
    b = db.session.get(Booking, booking_id)
    if not b or b.user_id != session.get('user_id'): return redirect(url_for('profile'))
    return render_template('payment.html', booking=b)

@app.route('/pay', methods=['POST'])
def pay():
    if 'user_id' not in session: return jsonify({"status": "failed", "msg": "Session expired"}), 401
    
    try:
        bid = int(request.form.get('booking_id', 0))
        trx = request.form.get('trxid', '').strip()
        amt_str = request.form.get('amount', '0')
        amt = float(amt_str)
        method = request.form.get('method', 'unknown')
        name = request.form.get('name', 'Anonymous')
        sender = request.form.get('sender', 'Unknown')
        
        if not trx or not bid:
            return jsonify({"status": "failed", "msg": "Missing payment details"})

        if Payment.query.filter_by(trxid=trx).first(): 
            return jsonify({"status": "failed", "msg": "TRXID already used"})
        
        booking = db.session.get(Booking, bid)
        if not booking:
            return jsonify({"status": "failed", "msg": "Booking not found"})

        # Auto-verify against webhooks
        verified = SMSPayment.query.filter_by(trxid=trx).first() or EmailPayment.query.filter_by(trxid=trx).first()
        
        status = 'PAID' if (verified and abs(verified.amount - amt) < 0.1) else 'PENDING'
        if method == 'binance': status = 'PENDING' # Manual verification for crypto
        
        p = Payment(booking_id=bid, user_id=session['user_id'], name=name,
                    method=method, amount=amt, sender=sender,
                    trxid=trx, status=status)
        db.session.add(p)
        
        if status == 'PAID':
            booking.payment_status, booking.status = 'paid', 'confirmed'
            
        db.session.commit()
        # Real-time status update for the user
        socketio.emit('payment_status_update', {'booking_id': bid, 'status': status}, broadcast=True)
        return jsonify({"status": "success", "msg": f"Payment {status} submitted."})
    except (ValueError, TypeError) as e:
        return jsonify({"status": "failed", "msg": "Invalid data format"})
    except IntegrityError:
        db.session.rollback()
        return jsonify({"status": "failed", "msg": "TRXID duplicate error"})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Payment error: {e}")
        return jsonify({"status": "failed", "msg": "Server error processing payment"})

@app.route('/cancel-booking/<int:booking_id>', methods=['POST'])
def cancel_booking(booking_id):
    if 'user_id' not in session: return jsonify({"success": False}), 401
    b = db.session.get(Booking, booking_id)
    if not b or b.user_id != session['user_id']: return jsonify({"success": False}), 403
    if b.status in ['cancelled', 'cancel_requested']: return jsonify({"success": False})
    
    try:
        if b.payment_status == 'paid': b.status = 'cancel_requested'
        else: b.status = 'cancelled'
        db.session.commit()
        return jsonify({"success": True, "message": "Booking cancellation processed."})
    except Exception:
        db.session.rollback()
        return jsonify({"success": False, "message": "Database error"})

# ===================== AUTHENTICATION ROUTES =====================
@app.route('/login/google')
def google_login():
    redirect_uri = url_for('google_authorize', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/login/google/authorize')
def google_authorize():
    try:
        token = google.authorize_access_token()
        user_info = token.get('userinfo')
        if not user_info:
            resp = google.get('https://openidconnect.googleapis.com/v1/userinfo')
            if resp.ok:
                user_info = resp.json()
            else:
                flash("Could not retrieve user info from Google.", "danger")
                return redirect(url_for('login'))
    except Exception as e:
        logger.error(f"Google Auth Error: {str(e)}")
        flash(f"Google Auth Error: {str(e)}", "danger")
        return redirect(url_for('login'))

    if not user_info:
        flash("Could not retrieve user info from Google.", "danger")
        return redirect(url_for('login'))
    
    try:
        user = User.query.filter_by(google_id=user_info['sub']).first()
        if not user:
            user = User.query.filter_by(email=user_info['email']).first()
            if user:
                user.google_id = user_info['sub']
            else:
                user = User(
                    username=user_info.get('name', user_info.get('email').split('@')[0]),
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
    except Exception as e:
        db.session.rollback()
        logger.error(f"DB Error during Google Auth: {e}")
        flash("Database error during login.", "danger")
        return redirect(url_for('login'))

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        if not is_allowed_email(email):
            flash('Please use a valid Gmail, Yahoo, Outlook, or common email address.', 'danger')
            return redirect(url_for('signup'))
        if get_user_by_email(email): 
            flash('Email already registered!', 'danger')
            return redirect(url_for('signup'))
        if not is_strong_password(password):
            flash('Password too weak! Min 8 chars: Upper, Lower, Number & Special Char.', 'danger')
            return redirect(url_for('signup'))
            
        try:
            u = User(username=username, email=email, 
                     password=generate_password_hash(password),
                     role=request.form.get('role', 'traveler'))
            db.session.add(u)
            db.session.commit()
            flash('Account created successfully! Please login.', 'success')
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
    if not u: return jsonify({"success": False, "message": "Unauthorized"}), 401
    
    try:
        for field in ['phone', 'location', 'profession', 'qualification', 'nid']:
            if field in request.form: 
                val = request.form[field].strip()
                # Simple length checks
                if field == 'location' and len(val) > 200: val = val[:200]
                if field in ['profession', 'qualification'] and len(val) > 100: val = val[:100]
                if field == 'nid' and len(val) > 50: val = val[:50]
                setattr(u, field, val)
                
        img = save_uploaded_file(request.files.get('profile_pic'), 'profile')
        if img: u.profile_pic = img
        db.session.commit()
        return jsonify({"success": True, "message": "Profile updated successfully!"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)})

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

@app.route('/admin/manual-confirm-booking/<int:bid>')
def admin_manual_confirm(bid):
    if not session.get('is_admin'): return redirect(url_for('index'))
    b = db.session.get(Booking, bid)
    if b:
        b.payment_status, b.status = 'paid', 'confirmed'
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
            c = Coupon(code=request.form['code'].upper(), discount_percent=int(request.form['discount']),
                       max_uses=int(request.form['max_uses']))
            db.session.add(c)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash(f"Error creating coupon: {str(e)}", "danger")
            
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
        try:
            trx_val = trx_match.group(1)
            if not SMSPayment.query.filter_by(trxid=trx_val).first():
                db.session.add(SMSPayment(amount=float(amt_match.group(1)), trxid=trx_val, raw=m))
                db.session.commit()
                socketio.emit('new_payment_broadcast', {'amount': amt_match.group(1)}, broadcast=True)
        except IntegrityError:
            db.session.rollback()
    return jsonify({"status": "ok"})

@app.route("/email-webhook", methods=["POST"])
def email_webhook():
    d = request.json
    if not d or d.get("secret") != app.config['SECRET_KEY']: return jsonify({"status": "err"}), 401
    c = d.get("content", "")
    amt_match = re.search(r'(?:Amount|Paid|Received|USDT)\s*[:=]?\s*(\d+(?:\.\d+)?)', c, re.I)
    trx_match = re.search(r'(?:TrxID|TxnID|TXID|Order ID|ID)\s*[:=]?\s*([A-Za-z0-9]+)', c, re.I)
    if amt_match and trx_match:
        try:
            trx_val = trx_match.group(1)
            if not EmailPayment.query.filter_by(trxid=trx_val).first():
                db.session.add(EmailPayment(amount=float(amt_match.group(1)), trxid=trx_val, raw=c))
                db.session.commit()
                socketio.emit('new_payment_broadcast', {'amount': amt_match.group(1)}, broadcast=True)
        except IntegrityError:
            db.session.rollback()
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
def init_db():
    with app.app_context():
        try:
            db.create_all()
            # Initial Admin User
            if not User.query.filter_by(email='admin@travellerstop.com').first():
                db.session.add(User(username='admin', email='admin@travellerstop.com', 
                                    password=generate_password_hash('admin123'), 
                                    is_admin=True, role='admin', nid_verified=True))
                db.session.commit()
                logger.info("Admin user created.")
        except Exception as e:
            logger.error(f"Database initialization error: {e}")
            db.session.rollback()

if __name__ == '__main__':
    init_db()
    # socketio.run supports eventlet for high-concurrency production usage
    socketio.run(app, host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=os.getenv('FLASK_DEBUG', 'true').lower() == 'true')
else:
    # This block is for WSGI servers like Gunicorn
    init_db()
