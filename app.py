import os
import re
import logging

import requests
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from time import time
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from xml.etree import ElementTree

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, abort, send_from_directory, make_response
from werkzeug.exceptions import HTTPException
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import false, func, insert, or_, select
from sqlalchemy.exc import IntegrityError
from flask_wtf.csrf import generate_csrf, validate_csrf
from wtforms.validators import ValidationError
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
import secrets

from account_recovery import (
    delivery_available, make_reset_token, read_reset_token, reset_token_matches, send_reset_email,
)
from uddoktapay import checkout_url, gateway_request, payment_amount
from travel_search import district_centre, distance_km, nearby_origin, place_aliases, search_dates

# ===================== CONFIGURATION =====================
load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__,
            static_folder=os.path.join(BASE_DIR, 'static'),
            template_folder=os.path.join(BASE_DIR, 'templates'))

# ===================== PRODUCTION SECURITY =====================
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY') or secrets.token_hex(32)
app.config['DEBUG'] = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 604800
app.config['MAX_CONTENT_LENGTH'] = 15 * 1024 * 1024
app.config['PREFERRED_URL_SCHEME'] = 'https'

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

app.config['UDDOKTAPAY_API_URL'] = os.getenv('UDDOKTAPAY_API_URL', 'https://travellerstop.paymently.io/api')
app.config['UDDOKTAPAY_API_KEY'] = os.getenv('UDDOKTAPAY_API_KEY')
app.config['UDDOKTAPAY_SITE_URL'] = os.getenv('UDDOKTAPAY_SITE_URL', 'https://travellerstop.com').rstrip('/')
app.config['PUBLIC_SITE_URL'] = os.getenv('PUBLIC_SITE_URL', 'https://travellerstop.com').rstrip('/')

# ===================== DATABASE =====================
db_url = os.getenv('DATABASE_URL')
if not db_url:
    raise RuntimeError("DATABASE_URL not set.")

if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {"pool_pre_ping": True}

db = SQLAlchemy(app)

# ===================== GOOGLE OAUTH =====================
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

class FirstBookingOffer(db.Model):
    __tablename__ = 'first_booking_offers'
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), primary_key=True)
    first_booking_id = db.Column(db.Integer, nullable=False)
    redeemed = db.Column(db.Boolean, nullable=False, default=False)


@app.cli.command('init-firsthere')
def init_firsthere():
    """Create the redemption table on an existing database."""
    FirstBookingOffer.__table__.create(db.engine, checkfirst=True)
    existing = select(FirstBookingOffer.user_id)
    earliest = (
        select(Booking.user_id, func.min(Booking.id), false())
        .where(~Booking.user_id.in_(existing))
        .group_by(Booking.user_id)
    )
    db.session.execute(insert(FirstBookingOffer).from_select(
        ['user_id', 'first_booking_id', 'redeemed'], earliest,
    ))
    db.session.commit()


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

def is_strong_password(password):
    if len(password) < 8: return False
    return all(re.search(r, password) for r in [r"[a-z]", r"[A-Z]", r"[0-9]", r"[!@#$%^&*(),.?\":{}|<>]"])

def is_allowed_email(email):
    allowed = {'gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com', 'icloud.com', 'protonmail.com', 'me.com'}
    return email.split('@')[-1].lower() in allowed

def save_uploaded_file(file, folder_type):
    if not file or not file.filename:
        return None
    ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
    if ext not in {'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'}:
        return None
    try:
        filename = secure_filename(file.filename)
        unique_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
        subfolder = 'profile_pics' if folder_type == 'profile' else 'rooms'
        folder = os.path.join(BASE_DIR, 'static', 'uploads', subfolder)
        os.makedirs(folder, exist_ok=True)
        full_path = os.path.join(folder, unique_name)
        file.save(full_path)
        return f"uploads/{subfolder}/{unique_name}"
    except Exception as e:
        logger.error(f"Image save error: {e}")
        return None

# ===================== TEMPLATE & SECURITY =====================
@app.template_filter('datetimeformat')
def datetimeformat(value, fmt='%Y-%m-%d'):
    if not value: return ""
    if isinstance(value, str):
        try: value = datetime.fromisoformat(value)
        except: return value
    return value.strftime(fmt)

def site_language():
    return 'bn' if request.cookies.get('travel_language') == 'bn' else 'en'


def tr(english, bangla):
    return bangla if site_language() == 'bn' else english


def local_return_path(target):
    if target and target.startswith('/') and not target.startswith('//') and not re.search(r'[\\\x00-\x20\x7f]', target):
        return target
    return None


@app.post('/language')
def set_language():
    try:
        validate_csrf(request.form.get('csrf_token'))
    except ValidationError:
        abort(400)
    language = request.form.get('language')
    if language not in ('en', 'bn'):
        abort(400)

    target = local_return_path(request.form.get('next'))
    response = redirect(target or url_for('index'), code=303)
    response.set_cookie('travel_language', language, max_age=31536000,
                        secure=request.is_secure, httponly=True, samesite='Lax')
    return response


THEME_FILES = {
    'style.css',
    'script.js',
    'logo.png',
    'fonts/dm-sans.woff2',
    'images/google.svg',
    'images/social/facebook.svg',
    'images/social/instagram.svg',
    'images/social/linkedin.svg',
    'images/social/reddit.svg',
    'images/social/trustpilot.svg',
    'images/bandarban.webp',
    'images/sylhet.webp',
    'images/coxs-bazar.webp',
    'images/rangamati.webp',
    'fonts/noto-sans-bengali.woff2',
    'vendor/bootstrap/bootstrap.min.css',
    'vendor/bootstrap/bootstrap.bundle.min.js',
    'vendor/fontawesome/css/fontawesome.min.css',
    'vendor/fontawesome/css/solid.min.css',
    'vendor/fontawesome/webfonts/fa-solid-900.woff2',
    'vendor/fontawesome/webfonts/fa-solid-900.ttf',
}


@app.get('/theme-assets/<path:filename>')
def theme_asset(filename):
    if filename not in THEME_FILES:
        abort(404)
    return send_from_directory(app.static_folder, filename)


@lru_cache(maxsize=len(THEME_FILES))
def theme_version(filename):
    return sha256((Path(app.static_folder) / filename).read_bytes()).hexdigest()[:12]


def theme_url(filename):
    return url_for('theme_asset', filename=filename, v=theme_version(filename))


@app.context_processor
def inject_global_data():
    user = get_user_by_id(session.get('user_id'))
    return {
        'current_user': user,
        'site_language': site_language(),
        'tr': tr,
        'theme_url': theme_url,
        'language_token': generate_csrf,
        'profile_complete': is_profile_complete(user),
        'get_room_image': lambda img: url_for('static', filename=img) if img and not img.startswith('http') else (img or theme_url('logo.png'))
    }

@app.after_request
def add_security_headers(response):
    headers = {
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY',
        'X-XSS-Protection': '1; mode=block',
        'Strict-Transport-Security': 'max-age=31536000; includeSubDomains; preload',
        'Referrer-Policy': 'strict-origin-when-cross-origin'
    }
    for k, v in headers.items():
        response.headers.setdefault(k, v)
    return response

# ===================== GOOGLE AUTH =====================
@app.route('/login/google')
def google_login():
    session['login_destination'] = local_return_path(request.args.get('next'))
    return google.authorize_redirect(url_for('google_callback', _external=True))

@app.route('/login/google/callback')
def google_callback():
    destination = session.pop('login_destination', None)
    try:
        token = google.authorize_access_token()
        info = token.get('userinfo') or google.get('https://openidconnect.googleapis.com/v1/userinfo').json()
        
        email = info.get('email')
        if not email:
            flash(tr('Could not retrieve email from Google.', 'Google থেকে ইমেইল ঠিকানা পাওয়া যায়নি।'), "danger")
            return redirect(url_for('login'))

        user = User.query.filter_by(google_id=info.get('sub')).first()

        if not user:
            user = get_user_by_email(email)
            if user:
                user.google_id = info.get('sub')
            else:
                user = User(
                    username=info.get('name', email.split('@')[0]),
                    email=email,
                    google_id=info.get('sub'),
                    role='traveler'
                )
                db.session.add(user)
            db.session.commit()

        if user.blocked:
            flash(tr('Your account has been blocked.', 'আপনার অ্যাকাউন্ট বন্ধ করা হয়েছে।'), 'danger')
            return redirect(url_for('login'))

        session.clear()
        session['user_id'] = user.id
        session['username'] = user.username
        session['is_admin'] = user.is_admin

        flash(tr('Successfully logged in with Google!', 'Google দিয়ে সাইন ইন হয়েছে।'), 'success')
        return redirect(destination or url_for('admin' if user.is_admin else 'profile'))

    except Exception as e:
        logger.error(f"Google Callback Error: {e}")
        flash(tr('Google login failed. Please try again.', 'Google দিয়ে সাইন ইন করা যায়নি। আবার চেষ্টা করুন।'), "danger")
        return redirect(url_for('login'))

# ===================== AUTH ROUTES =====================
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    destination = local_return_path(request.values.get('next'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if not is_allowed_email(email):
            flash(tr('Please use a valid email address.', 'একটি সঠিক ইমেইল ঠিকানা দিন।'), 'danger')
            return redirect(url_for('signup', next=destination))
        if get_user_by_email(email):
            flash(tr('Email already registered!', 'এই ইমেইল ঠিকানায় ইতিমধ্যে অ্যাকাউন্ট আছে।'), 'danger')
            return redirect(url_for('signup', next=destination))
        if not is_strong_password(password):
            flash(tr('Password too weak!', 'আরও শক্তিশালী পাসওয়ার্ড ব্যবহার করুন।'), 'danger')
            return redirect(url_for('signup', next=destination))

        try:
            u = User(username=username, email=email, password=generate_password_hash(password))
            db.session.add(u)
            db.session.commit()
            flash(tr('Account created! Please login.', 'অ্যাকাউন্ট তৈরি হয়েছে। সাইন ইন করুন।'), 'success')
            return redirect(url_for('login', next=destination))
        except Exception:
            db.session.rollback()
            logger.exception('Signup failed')
            flash(tr('Could not create your account.', 'অ্যাকাউন্ট তৈরি করা যায়নি।'), 'danger')
    return render_template('signup.html', destination=destination)

@app.route('/login', methods=['GET', 'POST'])
def login():
    destination = local_return_path(request.values.get('next'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        u = get_user_by_email(email)
        if u and u.password and check_password_hash(u.password, password):
            if u.blocked:
                flash(tr('Account blocked.', 'অ্যাকাউন্ট বন্ধ করা হয়েছে।'), 'danger')
                return redirect(url_for('login', next=destination))
            session.clear()
            session['user_id'] = u.id
            session['username'] = u.username
            session['is_admin'] = u.is_admin
            return redirect(destination or url_for('admin' if u.is_admin else 'profile'))
        flash(tr('Invalid email or password.', 'ইমেইল বা পাসওয়ার্ড সঠিক নয়।'), 'danger')
    return render_template('login.html', destination=destination)

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    configured = delivery_available()
    if request.method == 'POST':
        try:
            validate_csrf(request.form.get('csrf_token'))
        except ValidationError:
            abort(400)

        if configured and time() - session.get('last_reset_request', 0) >= 60:
            session['last_reset_request'] = time()
            email = request.form.get('email', '').strip().lower()[:120]
            user = User.query.filter(func.lower(User.email) == email).first()
            if user and not user.blocked and user.password:
                token = make_reset_token(app.secret_key, user.id, user.password)
                link = app.config['UDDOKTAPAY_SITE_URL'] + url_for('reset_password', token=token)
                try:
                    send_reset_email(
                        user.email, link,
                        tr('Reset your Traveller Stop password', 'ট্রাভেলার স্টপের পাসওয়ার্ড বদলান'),
                        tr('This link expires in 30 minutes. Ignore this message if you did not ask for a reset.',
                           'লিংকটি ৩০ মিনিটের মধ্যে মেয়াদ শেষ হবে। আপনি অনুরোধ না করলে বার্তাটি উপেক্ষা করুন।'),
                    )
                except (OSError, ValueError, KeyError):
                    logger.warning('Password reset email could not be delivered')

        flash(tr('If the address is eligible, you will receive a password reset link.',
                 'ইমেইলটি যোগ্য হলে পাসওয়ার্ড বদলানোর লিংক পাবেন।'), 'info')
        return redirect(url_for('forgot_password'), code=303)
    return render_template('password_recovery.html', configured=configured)


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    claims = read_reset_token(app.secret_key, token)
    user = db.session.get(User, claims[0], with_for_update=request.method == 'POST') if claims else None
    valid = bool(
        user and not user.blocked and user.password
        and reset_token_matches(app.secret_key, claims[1], user.password)
    )
    if request.method == 'POST' and valid:
        try:
            validate_csrf(request.form.get('csrf_token'))
        except ValidationError:
            abort(400)

        password = request.form.get('password', '')
        if len(password) > 128 or not is_strong_password(password) or password != request.form.get('confirm_password'):
            flash(tr('Use a strong password and make sure both entries match.',
                     'শক্তিশালী পাসওয়ার্ড দিন এবং দুবার একই পাসওয়ার্ড লিখুন।'), 'warning')
        else:
            user.password = generate_password_hash(password)
            db.session.commit()
            session.clear()
            flash(tr('Password updated. Sign in with your new password.',
                     'পাসওয়ার্ড বদলানো হয়েছে। নতুন পাসওয়ার্ড দিয়ে সাইন ইন করুন।'), 'success')
            return redirect(url_for('login'), code=303)
    response = make_response(render_template('password_recovery.html', reset=True, valid=valid, token=token))
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.route('/logout')
def logout():
    session.clear()
    flash(tr('Logged out successfully.', 'সাইন আউট হয়েছে।'), 'info')
    return redirect(url_for('index'))

# ===================== MAIN ROUTES =====================
@app.get('/robots.txt')
def robots():
    sitemap_url = app.config['PUBLIC_SITE_URL'] + url_for('sitemap')
    return f'User-agent: *\nSitemap: {sitemap_url}\n', 200, {'Content-Type': 'text/plain; charset=utf-8'}


@app.get('/sitemap.xml')
def sitemap():
    origin = app.config['PUBLIC_SITE_URL']
    root = ElementTree.Element('urlset', xmlns='http://www.sitemaps.org/schemas/sitemap/0.9')
    pages = ['index', 'rooms_page', 'about', 'services', 'contact', 'help_center', 'safety_tips']
    paths = [url_for(endpoint) for endpoint in pages]
    paths.extend(url_for('room_detail', room_id=room_id) for room_id in db.session.scalars(select(Room.id).order_by(Room.id)))
    for path in paths:
        entry = ElementTree.SubElement(root, 'url')
        ElementTree.SubElement(entry, 'loc').text = origin + path
    document = ElementTree.tostring(root, encoding='utf-8', xml_declaration=True)
    return document, 200, {'Content-Type': 'application/xml; charset=utf-8'}


@app.route('/')
def index():
    featured = Room.query.order_by(Room.created_at.desc()).limit(8).all()
    return render_template('index.html', featured_rooms=featured)

def available_rooms(query, checkin, checkout):
    return query.filter(
        or_(Room.available_from.is_(None), Room.available_from == '', Room.available_from <= checkin),
        or_(Room.available_to.is_(None), Room.available_to == '', Room.available_to >= checkout),
        ~Room.bookings.any(
            Booking.status.in_(('confirmed', 'cancel_requested'))
            & (Booking.checkin < checkout) & (Booking.checkout > checkin)
        ),
    )


@app.route('/rooms')
def rooms_page():
    location = request.args.get('location', '').strip()[:100]
    checkin = request.args.get('checkin', '')
    checkout = request.args.get('checkout', '')
    dates = search_dates(checkin, checkout)
    nearby = request.args.get('nearby') == '1'
    origin = nearby_origin(request.args.get('lat'), request.args.get('lng')) if nearby else None
    saved = request.args.get('saved') == '1'
    query = Room.query.order_by(Room.created_at.desc(), Room.id.desc())

    if location:
        query = query.filter(or_(
            *(Room.location.icontains(alias, autoescape=True) for alias in place_aliases(location)),
            Room.title.icontains(location, autoescape=True),
        ))
    if dates:
        query = available_rooms(query, *dates)

    rooms = query.all()
    distances = {}
    if origin:
        for room in rooms:
            centre = district_centre(room.location)
            if centre:
                distances[room.id] = distance_km(origin, centre)
        rooms = sorted(
            (room for room in rooms if room.id in distances and distances[room.id] <= 100),
            key=lambda room: distances[room.id],
        )

    return render_template(
        'rooms.html', rooms=rooms, location=location, checkin=checkin, checkout=checkout,
        invalid_dates=bool(checkin or checkout) and not dates, nearby=nearby,
        origin=origin, distances=distances, saved=saved,
    )


@app.route('/room/<int:room_id>')
def room_detail(room_id):
    room = db.session.get(Room, room_id)
    if not room: return redirect(url_for('rooms_page'))
    return render_template(
        'room_detail.html', room=room,
        dates=search_dates(request.args.get('checkin'), request.args.get('checkout')),
    )

@app.route('/post-room', methods=['GET', 'POST'])
def post_room():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = get_user_by_id(session['user_id'])
    if not user or not is_profile_complete(user):
        flash(tr('Please complete your profile first.', 'আগে আপনার প্রোফাইল পূরণ করুন।'), 'warning')
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
            flash(tr('Room posted successfully!', 'কক্ষ যোগ করা হয়েছে।'), 'success')
            return redirect(url_for('profile'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {str(e)}', 'danger')
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
            if img: room.image_url = img
            db.session.commit()
            flash(tr('Room updated successfully!', 'কক্ষের তথ্য হালনাগাদ হয়েছে।'), 'success')
            return redirect(url_for('profile'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {str(e)}', 'danger')
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
        flash(tr('Room deleted!', 'কক্ষ মুছে ফেলা হয়েছে।'), 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}', 'danger')
    return redirect(url_for('profile'))

FIRST_BOOKING_CODE = 'FIRSTHERE'


def first_booking_offer(user_id):
    offer = db.session.get(FirstBookingOffer, user_id)
    if offer is None:
        first = Booking.query.filter_by(user_id=user_id).order_by(Booking.id).first()
        offer = FirstBookingOffer(
            user_id=user_id, first_booking_id=first.id, redeemed=first.coupon_used == FIRST_BOOKING_CODE,
        )
        db.session.add(offer)
    return offer


def apply_booking_promo(booking, code, offer=None):
    original = payment_amount(booking.original_amount)
    if not original or booking.coupon_used:
        return False

    if code == FIRST_BOOKING_CODE:
        if offer is None or offer.first_booking_id != booking.id or offer.redeemed:
            return False
        discount = min(Decimal('200'), original * Decimal('.15'))
    else:
        coupon = Coupon.query.filter_by(code=code, active=True).with_for_update().first()
        if not coupon or coupon.used >= coupon.max_uses:
            return False
        discount = original * Decimal(coupon.discount_percent) / 100

    discount = discount.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    if not 0 < discount < original:
        return False

    if code == FIRST_BOOKING_CODE:
        offer.redeemed = True
    else:
        coupon.used += 1
    booking.discount = float(discount)
    booking.total_amount = float(original - discount)
    booking.coupon_used = code
    return True


@app.route('/book/<int:room_id>', methods=['POST'])
def book_room(room_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user = get_user_by_id(session['user_id'])
    if not user or not is_profile_complete(user):
        flash(tr('Please complete your profile first.', 'আগে আপনার প্রোফাইল পূরণ করুন।'), 'warning')
        return redirect(url_for('profile'))

    dates = search_dates(request.form.get('checkin'), request.form.get('checkout'))
    if not dates:
        flash(tr('Choose a check-out date after check-in.', 'চেক-ইনের পরে চেক-আউটের তারিখ বেছে নিন।'), 'warning')
        return redirect(url_for('room_detail', room_id=room_id))

    ci, co = dates
    nights = (datetime.fromisoformat(co) - datetime.fromisoformat(ci)).days
    room = available_rooms(Room.query.filter_by(id=room_id), ci, co).first()
    if not room:
        flash(tr('This stay is unavailable for those dates.', 'এই তারিখগুলোতে এই আবাসনটি খালি নেই।'), 'warning')
        return redirect(url_for('room_detail', room_id=room_id))

    total = room.price_per_night * nights
    coupon_code = request.form.get('coupon_code', '').strip().upper()[:50]
    try:
        booking = Booking(
            room_id=room_id, user_id=user.id, checkin=ci, checkout=co,
            original_amount=total, total_amount=total,
        )
        db.session.get(User, user.id, with_for_update=True)
        db.session.add(booking)
        db.session.flush()
        offer = first_booking_offer(user.id)
        if coupon_code and not apply_booking_promo(booking, coupon_code, offer):
            flash(tr('This promo code is unavailable for your booking.',
                     'এই বুকিংয়ে প্রোমো কোডটি ব্যবহার করা যাচ্ছে না।'), 'warning')
        db.session.commit()
        flash(tr('Booking created successfully!', 'বুকিং তৈরি হয়েছে।'), 'success')
        return redirect(url_for('payment_page', booking_id=booking.id))
    except Exception:
        db.session.rollback()
        logger.exception('Booking failed')
        flash(tr('Could not create your booking.', 'বুকিং তৈরি করা যায়নি।'), 'danger')
        return redirect(url_for('room_detail', room_id=room_id))

def payment_under_review(booking_id):
    return Payment.query.filter_by(
        booking_id=booking_id, method='uddoktapay', status='REVIEW_REQUIRED',
    ).first() is not None


@app.route('/payment/<int:booking_id>')
def payment_page(booking_id):
    b = db.session.get(Booking, booking_id)
    if not b or b.user_id != session.get('user_id'):
        return redirect(url_for('profile'))
    return render_template(
        'payment.html', booking=b, payment_token=generate_csrf(),
        uddoktapay_enabled=bool(app.config['UDDOKTAPAY_API_KEY']),
        under_review=payment_under_review(booking_id),
    )

@app.post('/payment/<int:booking_id>/promo')
def payment_promo(booking_id):
    try:
        validate_csrf(request.form.get('csrf_token'))
    except ValidationError:
        abort(400)

    booking = db.session.get(Booking, booking_id, with_for_update=True)
    if not booking or booking.user_id != session.get('user_id'):
        abort(404)
    code = request.form.get('coupon_code', '').strip().upper()[:50]
    try:
        eligible = booking.status == 'pending_payment' and booking.payment_status == 'unpaid' and not payment_under_review(booking.id)
        offer = None
        if eligible and code == FIRST_BOOKING_CODE:
            db.session.get(User, booking.user_id, with_for_update=True)
            offer = first_booking_offer(booking.user_id)
        if eligible and apply_booking_promo(booking, code, offer):
            db.session.commit()
            flash(tr('Promo code applied.', 'প্রোমো কোড প্রয়োগ হয়েছে।'), 'success')
        else:
            flash(tr('This promo code is unavailable for your booking.',
                     'এই বুকিংয়ে প্রোমো কোডটি ব্যবহার করা যাচ্ছে না।'), 'warning')
    except IntegrityError:
        db.session.rollback()
        flash(tr('This promo code is unavailable for your booking.',
                 'এই বুকিংয়ে প্রোমো কোডটি ব্যবহার করা যাচ্ছে না।'), 'warning')
    return redirect(url_for('payment_page', booking_id=booking_id), code=303)


def uddoktapay_request(endpoint, payload):
    return gateway_request(
        app.config['UDDOKTAPAY_API_URL'], app.config['UDDOKTAPAY_API_KEY'], endpoint, payload
    )


@app.post('/payment/<int:booking_id>/uddoktapay')
def uddoktapay_checkout(booking_id):
    booking = db.session.get(Booking, booking_id, with_for_update=True)
    if not booking or booking.user_id != session.get('user_id'):
        abort(404)
    try:
        validate_csrf(request.form.get('csrf_token'))
    except ValidationError:
        abort(400)

    amount = payment_amount(booking.total_amount)
    if not app.config['UDDOKTAPAY_API_KEY'] or amount is None or (
        booking.payment_status == 'paid' or booking.status != 'pending_payment'
    ):
        flash(tr('Online checkout is unavailable for this booking.', 'এই বুকিংয়ের জন্য অনলাইন পেমেন্ট পাওয়া যাচ্ছে না।'), 'warning')
        return redirect(url_for('payment_page', booking_id=booking_id), code=303)

    if payment_under_review(booking_id):
        return redirect(url_for('payment_page', booking_id=booking_id), code=303)
    eligible_room = available_rooms(
        Room.query.filter_by(id=booking.room_id), booking.checkin, booking.checkout
    ).first()
    if not eligible_room:
        flash(tr('This stay is no longer available for those dates.', 'এই তারিখগুলোতে আবাসনটি আর খালি নেই।'), 'warning')
        return redirect(url_for('payment_page', booking_id=booking_id), code=303)

    site = app.config['UDDOKTAPAY_SITE_URL']
    payload = {
        'full_name': booking.user.username,
        'email': booking.user.email,
        'amount': format(amount, '.2f'),
        'metadata': {'travellerstop_booking_id': str(booking.id)},
        'redirect_url': site + url_for('uddoktapay_return'),
        'cancel_url': site + url_for('payment_page', booking_id=booking.id),
        'webhook_url': site + url_for('uddoktapay_webhook'),
        'return_type': 'GET',
    }
    # Keep the amount stable once a hosted invoice can exist.
    booking.payment_status = 'checkout_started'
    db.session.commit()
    try:
        gateway = uddoktapay_request('checkout-v2', payload)
        return redirect(checkout_url(gateway, app.config['UDDOKTAPAY_API_URL']), code=303)
    except (requests.RequestException, ValueError):
        logger.warning('UddoktaPay checkout could not be started for booking %s', booking_id)
        flash(tr('Could not open UddoktaPay. Please try again in a moment.', 'উদ্যোক্তাপে খোলা যায়নি। একটু পরে আবার চেষ্টা করুন।'), 'warning')
        return redirect(url_for('payment_page', booking_id=booking_id), code=303)


def verify_uddoktapay_invoice(invoice_id):
    if not isinstance(invoice_id, str) or not invoice_id.strip() or len(invoice_id) > 80:
        abort(400)
    if not app.config['UDDOKTAPAY_API_KEY']:
        abort(503)
    try:
        payment = uddoktapay_request('verify-payment', {'invoice_id': invoice_id})
    except (requests.RequestException, ValueError):
        abort(502)

    if payment.get('status') != 'COMPLETED':
        abort(409)
    metadata = payment.get('metadata')
    if not isinstance(metadata, dict):
        abort(422)
    try:
        booking_id = int(metadata['travellerstop_booking_id'])
    except (KeyError, TypeError, ValueError, OverflowError):
        abort(422)
    paid_amount = payment_amount(payment.get('amount'))
    if paid_amount is None:
        abort(422)

    booking = db.session.get(Booking, booking_id, with_for_update=True)
    if not booking:
        abort(422)
    amount_changed = payment_amount(booking.total_amount) != paid_amount
    if amount_changed and payment_amount(booking.original_amount) != paid_amount:
        abort(422)

    reference = f'uddoktapay:{invoice_id}'
    existing = Payment.query.filter_by(trxid=reference).first()
    if existing:
        if existing.booking_id != booking.id or existing.method != 'uddoktapay':
            abort(409)
        return booking

    # Serialize confirmations for the same room, including invoices for overlapping dates.
    db.session.get(Room, booking.room_id, with_for_update=True)
    eligible_room = available_rooms(
        Room.query.filter_by(id=booking.room_id), booking.checkin, booking.checkout
    ).first()
    requires_review = (
        amount_changed or booking.payment_status == 'paid' or booking.status != 'pending_payment' or eligible_room is None
    )
    db.session.add(Payment(
        booking_id=booking.id, user_id=booking.user_id, name=booking.user.username,
        method='uddoktapay', amount=float(paid_amount), trxid=reference,
        status='REVIEW_REQUIRED' if requires_review else 'CONFIRMED',
    ))
    if not requires_review:
        booking.payment_status = 'paid'
        booking.status = 'confirmed'
    db.session.commit()
    return booking


@app.get('/payment/uddoktapay/return')
def uddoktapay_return():
    try:
        booking = verify_uddoktapay_invoice(request.args.get('invoice_id'))
    except HTTPException:
        flash(tr('Payment verification is pending or unavailable. Check your bookings before retrying.', 'পেমেন্ট যাচাই অপেক্ষমাণ অথবা করা যাচ্ছে না। আবার চেষ্টা করার আগে আপনার বুকিং দেখুন।'), 'warning')
        return redirect(url_for('profile'))
    if booking.user_id != session.get('user_id'):
        return redirect(url_for('profile'))
    if booking.payment_status == 'paid':
        flash(tr('Payment received. Check your booking status below.', 'পেমেন্ট পাওয়া গেছে। নিচে বুকিংয়ের অবস্থা দেখুন।'), 'success')
    else:
        flash(tr('Payment received and awaiting review.', 'পেমেন্ট পাওয়া গেছে এবং পর্যালোচনার অপেক্ষায় আছে।'), 'info')
    return redirect(url_for('profile'))


@app.post('/payment/uddoktapay/webhook')
def uddoktapay_webhook():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = request.form
    verify_uddoktapay_invoice(payload.get('invoice_id'))
    return jsonify({'status': 'success'})


@app.post('/pay')
def pay():
    return jsonify({
        'status': 'failed',
        'msg': tr('Continue to UddoktaPay from your booking.', 'বুকিং থেকে উদ্যোক্তাপে-তে পেমেন্ট করুন।'),
    }), 410


@app.route('/cancel-booking/<int:booking_id>', methods=['POST'])
def cancel_booking(booking_id):
    if 'user_id' not in session: return jsonify({"success": False}), 401
    b = db.session.get(Booking, booking_id, with_for_update=True)
    if not b or b.user_id != session['user_id']: return jsonify({"success": False}), 403
    try:
        b.status = 'cancel_requested' if b.payment_status == 'paid' else 'cancelled'
        db.session.commit()
        return jsonify({"success": True, "message": "Cancellation request submitted."})
    except:
        db.session.rollback()
        return jsonify({"success": False, "message": "Database error"})

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
        return jsonify({"success": False, "message": tr('Please login first.', 'আগে সাইন ইন করুন।')}), 401

    user = get_user_by_id(session.get('user_id'))
    if not user:
        return jsonify({"success": False, "message": tr('User not found.', 'ব্যবহারকারীকে পাওয়া যায়নি।')}), 404

    try:
        for field in ['phone', 'location', 'profession', 'qualification', 'nid']:
            if field in request.form:
                value = request.form.get(field, '').strip()
                setattr(user, field, value if value else None)

        file = request.files.get('profile_pic')
        if file and file.filename:
            uploaded_image = save_uploaded_file(file, 'profile')
            if uploaded_image:
                user.profile_pic = uploaded_image

        db.session.commit()
        return jsonify({
            "success": True,
            "message": tr('Profile updated successfully!', 'প্রোফাইল হালনাগাদ হয়েছে।'),
            "profile_pic": url_for('static', filename=user.profile_pic) if user.profile_pic else None
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"Profile Update Error: {e}")
        return jsonify({"success": False, "message": tr('Server error. Please try again.', 'সার্ভারে সমস্যা হয়েছে। আবার চেষ্টা করুন।')}), 500

# ===================== ADMIN ROUTES =====================
@app.route('/admin')
def admin():
    if not session.get('is_admin'): return redirect(url_for('index'))
    return render_template('admin.html',
                         users=User.query.all(),
                         rooms=Room.query.all(),
                         coupons=Coupon.query.all(),
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
    if p and p.status == 'PENDING' and p.method != 'uddoktapay':
        b = db.session.get(Booking, p.booking_id, with_for_update=True)
        if b and b.payment_status != 'paid' and b.status == 'pending_payment':
            p.status = 'CONFIRMED'
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
            c = Coupon(code=request.form['code'].upper(),
                      discount_percent=int(request.form['discount']),
                      max_uses=int(request.form['max_uses']))
            db.session.add(c)
            db.session.commit()
            flash('Coupon created!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {str(e)}', 'danger')
    return render_template('admin_coupons.html', coupons=Coupon.query.all())

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
            admin = User(username='Admin', email=admin_email,
                        password=generate_password_hash(admin_pass),
                        is_admin=True, role='admin', nid_verified=True)
            db.session.add(admin)
            db.session.commit()
            logger.info(f"✅ Admin created: {admin_email}")

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=False)
