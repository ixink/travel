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
from sqlalchemy.exc import IntegrityError
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

# ===================== PRODUCTION SECURITY =====================
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
        return redirect(request.url.replace("http://", "https://", 1), code=301)

# ===================== DATABASE =====================
db_url = os.getenv('DATABASE_URL')
if not db_url: raise RuntimeError("DATABASE_URL not set.")
if db_url.startswith("postgres://"): db_url = db_url.replace("postgres://", "postgresql://", 1)

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

# ===================== SOCKETIO & HELPERS =====================
online_users_count = 0
counter_lock = threading.Lock()

@socketio.on('connect')
def handle_connect():
    global online_users_count
    with counter_lock: online_users_count += 1
    emit('update_online_users', {'count': max(1, online_users_count)}, broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    global online_users_count
    with counter_lock: online_users_count = max(0, online_users_count - 1)
    emit('update_online_users', {'count': max(1, online_users_count)}, broadcast=True)

def get_user_by_id(user_id):
    try: return db.session.get(User, int(user_id)) if user_id else None
    except: return None

def save_uploaded_file(file, folder_type):
    if not file or not file.filename: return None
    ext = file.filename.rsplit('.', 1)[1].lower()
    if ext not in {'png', 'jpg', 'jpeg', 'webp'}: return None
    try:
        unique_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{secure_filename(file.filename)}"
        sub = 'profile_pics' if folder_type == 'profile' else 'rooms'
        folder = os.path.join(BASE_DIR, 'static', 'uploads', sub)
        os.makedirs(folder, exist_ok=True)
        full_path = os.path.join(folder, unique_name)
        with Image.open(file) as img:
            if img.mode in ("RGBA", "P"): img = img.convert("RGB")
            img.thumbnail((800, 800) if folder_type == 'room' else (400, 400), Image.Resampling.LANCZOS)
            img.save(full_path, "JPEG", optimize=True, quality=85)
        return f"uploads/{sub}/{unique_name}"
    except Exception as e: logger.error(f"Save error: {e}"); return None

# ===================== TEMPLATES & CONTEXT =====================
@app.context_processor
def inject_global_data():
    u = get_user_by_id(session.get('user_id'))
    return {
        'current_user': u,
        'get_room_image': lambda img: url_for('static', filename=img) if img and not img.startswith('http') else (img or "https://images.unsplash.com/photo-1522771739844-649f6d175d97")
    }

@app.after_request
def add_security_headers(response):
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

# ===================== MAIN ROUTES =====================
@app.route('/')
def index():
    featured = Room.query.order_by(Room.created_at.desc()).limit(8).all()
    return render_template('index.html', featured_rooms=featured)

@app.route('/rooms')
def rooms_page():
    loc = request.args.get('location', '').strip()
    ci, co = request.args.get('checkin'), request.args.get('checkout')
    query = Room.query
    if loc: query = query.filter(Room.location.ilike(f"%{loc}%"))
    rooms = query.all()
    if ci and co:
        rooms = [r for r in rooms if not Booking.query.filter(Booking.room_id == r.id, Booking.status.notin_(['cancelled']), or_(and_(Booking.checkin < co, Booking.checkout > ci))).first()]
    return render_template('rooms.html', rooms=rooms, location=loc)

@app.route('/room/<int:room_id>')
def room_detail(room_id):
    room = db.session.get(Room, room_id)
    return render_template('room_detail.html', room=room) if room else redirect(url_for('rooms_page'))

@app.route('/post-room', methods=['GET', 'POST'])
def post_room():
    if 'user_id' not in session: return redirect(url_for('login'))
    u = get_user_by_id(session['user_id'])
    if not u.nid or not u.nid_verified:
        flash('NID verification required to host properties.', 'warning'); return redirect(url_for('profile'))
    if request.method == 'POST':
        try:
            img = save_uploaded_file(request.files.get('room_image'), 'room')
            room = Room(title=request.form['title'], description=request.form['description'], location=request.form['location'], price_per_night=float(request.form['price']), image_url=img, owner_id=u.id, amenities=request.form.getlist('amenities'))
            db.session.add(room); db.session.commit(); flash('Listing created!', 'success'); return redirect(url_for('profile'))
        except Exception as e: db.session.rollback(); flash(f'Error: {str(e)}', 'danger')
    return render_template('post_room.html')

@app.route('/edit-room/<int:room_id>', methods=['GET', 'POST'])
def edit_room(room_id):
    room = db.session.get(Room, room_id)
    if not room or room.owner_id != session.get('user_id'): return redirect(url_for('profile'))
    if request.method == 'POST':
        try:
            for f in ['title', 'description', 'location']: setattr(room, f, request.form.get(f))
            room.price_per_night = float(request.form['price'])
            img = save_uploaded_file(request.files.get('room_image'), 'room')
            if img: room.image_url = img
            db.session.commit(); flash('Updated!', 'success'); return redirect(url_for('profile'))
        except: db.session.rollback(); flash('Error updating room.', 'danger')
    return render_template('edit_room.html', room=room)

@app.route('/delete-room/<int:room_id>', methods=['POST'])
def delete_room(room_id):
    room = db.session.get(Room, room_id)
    if room and room.owner_id == session.get('user_id'):
        db.session.delete(room); db.session.commit(); flash('Deleted.', 'success')
    return redirect(url_for('profile'))

@app.route('/book/<int:room_id>', methods=['POST'])
def book_room(room_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    u = get_user_by_id(session['user_id'])
    if not u.nid or not u.nid_verified:
        flash('Please verify your NID to book stays.', 'warning'); return redirect(url_for('profile'))
    ci, co = request.form.get('checkin'), request.form.get('checkout')
    try:
        nights = (datetime.strptime(co, '%Y-%m-%d') - datetime.strptime(ci, '%Y-%m-%d')).days
        room = db.session.get(Room, room_id)
        if not room or nights <= 0: raise ValueError()
        total = room.price_per_night * nights
        booking = Booking(room_id=room_id, user_id=u.id, checkin=ci, checkout=co, original_amount=total, total_amount=total)
        db.session.add(booking); db.session.commit(); return redirect(url_for('payment_page', booking_id=booking.id))
    except: flash('Booking failed.', 'danger'); return redirect(url_for('room_detail', room_id=room_id))

# ===================== AUTH ROUTES =====================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = User.query.filter_by(email=request.form.get('email').lower()).first()
        if u and u.password and check_password_hash(u.password, request.form.get('password')):
            if u.blocked: flash('Account blocked.', 'danger'); return redirect(url_for('login'))
            session.clear(); session['user_id'], session['username'], session['is_admin'] = u.id, u.username, u.is_admin
            return redirect(url_for('profile'))
        flash('Invalid credentials.', 'danger')
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        try:
            email = request.form.get('email').strip().lower()
            if User.query.filter_by(email=email).first(): flash('Email exists.', 'danger'); return redirect(url_for('signup'))
            u = User(username=request.form.get('username').strip(), email=email, password=generate_password_hash(request.form.get('password')))
            db.session.add(u); db.session.commit(); flash('Account created!', 'success'); return redirect(url_for('login'))
        except: flash('Signup failed.', 'danger')
    return render_template('signup.html')

@app.route('/login/google')
def google_login(): return google.authorize_redirect(url_for('google_authorize', _external=True))

@app.route('/login/google/authorize')
def google_authorize():
    try:
        token = google.authorize_access_token()
        info = token.get('userinfo') or google.get('https://openidconnect.googleapis.com/v1/userinfo').json()
        u = User.query.filter_by(email=info['email']).first()
        if not u:
            u = User(username=info.get('name', info['email'].split('@')[0]), email=info['email'], google_id=info.get('sub'), profile_pic=info.get('picture', ''))
            db.session.add(u)
        else: u.google_id = info.get('sub')
        db.session.commit(); session.clear(); session['user_id'], session['username'], session['is_admin'] = u.id, u.username, u.is_admin
        return redirect(url_for('profile'))
    except: flash("Google login failed.", "warning"); return redirect(url_for('login'))

@app.route('/logout')
def logout(): session.clear(); flash('Logged out.', 'info'); return redirect(url_for('index'))

# ===================== PROFILE & API =====================
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
        for f in ['phone', 'location', 'profession', 'qualification', 'nid']:
            if f in request.form: setattr(u, f, request.form[f].strip() or None)
        file = request.files.get('profile_pic')
        if file:
            path = save_uploaded_file(file, 'profile')
            if path: u.profile_pic = path
        db.session.commit()
        return jsonify({"success": True, "message": "Profile updated!"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": "Save failed. Try again."})

# ===================== PAYMENT & CANCEL =====================
@app.route('/payment/<int:booking_id>')
def payment_page(booking_id):
    b = db.session.get(Booking, booking_id)
    if not b or b.user_id != session.get('user_id'): return redirect(url_for('profile'))
    return render_template('payment.html', booking=b)

@app.route('/pay', methods=['POST'])
def pay():
    if 'user_id' not in session: return jsonify({"status": "failed"}), 401
    try:
        p = Payment(booking_id=int(request.form.get('booking_id')), user_id=session['user_id'], amount=float(request.form.get('amount')), trxid=request.form.get('trxid'), method=request.form.get('method', 'manual'), status='PENDING')
        db.session.add(p); db.session.commit(); return jsonify({"status": "success"})
    except: return jsonify({"status": "failed"})

@app.route('/cancel-booking/<int:booking_id>', methods=['POST'])
def cancel_booking(booking_id):
    b = db.session.get(Booking, booking_id)
    if b and b.user_id == session.get('user_id'):
        b.status = 'cancel_requested' if b.payment_status == 'paid' else 'cancelled'
        db.session.commit(); return jsonify({"success": True})
    return jsonify({"success": False})

# ===================== ADMIN ROUTES =====================
@app.route('/admin')
def admin():
    if not session.get('is_admin'): return redirect(url_for('index'))
    return render_template('admin.html', users=User.query.all(), rooms=Room.query.all(), bookings=Booking.query.order_by(Booking.booked_at.desc()).all(), payments=Payment.query.order_by(Payment.time.desc()).all())

@app.route('/admin/block-user/<int:uid>')
def block_user(uid):
    if session.get('is_admin'):
        u = db.session.get(User, uid)
        if u: u.blocked = not u.blocked; db.session.commit()
    return redirect(url_for('admin'))

@app.route('/admin/verify-nid/<int:uid>')
def verify_nid(uid):
    if session.get('is_admin'):
        u = db.session.get(User, uid)
        if u: u.nid_verified = True; db.session.commit()
    return redirect(url_for('admin'))

@app.route('/admin/confirm-payment/<int:pid>')
def confirm_payment(pid):
    if not session.get('is_admin'): return redirect(url_for('index'))
    p = db.session.get(Payment, pid)
    if p:
        p.status = 'CONFIRMED'
        b = db.session.get(Booking, p.booking_id)
        if b: b.payment_status = 'paid'; b.status = 'confirmed'
        db.session.commit()
    return redirect(url_for('admin'))

@app.route('/admin/coupons', methods=['GET', 'POST'])
def admin_coupons():
    if not session.get('is_admin'): return redirect(url_for('index'))
    if request.method == 'POST':
        try:
            c = Coupon(code=request.form['code'].upper(), discount_percent=int(request.form['discount']), max_uses=int(request.form['max_uses']))
            db.session.add(c); db.session.commit()
        except Exception as e: db.session.rollback(); flash(f"Error: {str(e)}", "danger")
    return render_template('admin_coupons.html', coupons=Coupon.query.all())

# ===================== STATIC PAGES =====================
@app.route('/about')
def about(): return render_template('about_us.html')
@app.route('/contact')
def contact(): return render_template('contact_us.html')
@app.route('/terms')
def terms(): return render_template('terms_of_service.html')

# ===================== INIT =====================
def init_db():
    with app.app_context():
        db.create_all()
        admin_email = os.getenv('ADMIN_EMAIL', 'admin@travellerstop.com')
        if not User.query.filter_by(email=admin_email).first():
            db.session.add(User(username='Admin', email=admin_email, password=generate_password_hash('admin123'), is_admin=True, nid_verified=True))
            db.session.commit()

if __name__ == '__main__':
    init_db(); socketio.run(app, host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=False)
else: init_db()
