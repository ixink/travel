import json
import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, Response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from PIL import Image
from werkzeug.middleware.proxy_fix import ProxyFix
import re
import mimetypes

# Set absolute paths for production
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# MIME Fix for Linux
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('application/javascript', '.js')

app = Flask(__name__, 
            static_folder=os.path.join(BASE_DIR, 'static'),
            template_folder=os.path.join(BASE_DIR, 'templates'))

@app.after_request
def add_header(response):
    """
    Force correct MIME types and disable caching for development/updates
    """
    if request.path.endswith('.css'):
        response.headers['Content-Type'] = 'text/css'
    elif request.path.endswith('.js'):
        response.headers['Content-Type'] = 'application/javascript'
    return response

# Only use ProxyFix if actually behind a proxy (like Nginx). 
if os.environ.get('USE_PROXY', 'false').lower() == 'true':
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'traveller-stop-premium-secret-key-2026-v6')
app.config['DEBUG'] = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'

# ===================== CONFIG =====================
app.config['PROFILE_UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'static/uploads/profile_pics')
app.config['ROOM_UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'static/uploads/rooms')
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}

os.makedirs(app.config['PROFILE_UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['ROOM_UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, 'data'), exist_ok=True)

# ===================== DATA STORAGE =====================
DATA_DIR = os.path.join(BASE_DIR, 'data')
USERS_FILE = os.path.join(DATA_DIR, 'users.json')
ROOMS_FILE = os.path.join(DATA_DIR, 'rooms.json')
BOOKINGS_FILE = os.path.join(DATA_DIR, 'bookings.json')
PAYMENTS_FILE = os.path.join(DATA_DIR, 'payments.json')
SMS_FILE = os.path.join(DATA_DIR, 'sms.json')
EMAIL_FILE = os.path.join(DATA_DIR, 'email.json')
COUPONS_FILE = os.path.join(DATA_DIR, 'coupons.json')

# ===================== JSON HELPERS =====================
def load_json(file_path, default=[]):
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return default
    return default

def save_json(file_path, data):
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, default=str)

# Load data
users = load_json(USERS_FILE)
rooms = load_json(ROOMS_FILE)
bookings = load_json(BOOKINGS_FILE)
payments = load_json(PAYMENTS_FILE)
sms_payments = load_json(SMS_FILE)
email_payments = load_json(EMAIL_FILE)
coupons = load_json(COUPONS_FILE, default=[])

def save_all():
    save_json(USERS_FILE, users)
    save_json(ROOMS_FILE, rooms)
    save_json(BOOKINGS_FILE, bookings)
    save_json(PAYMENTS_FILE, payments)
    save_json(SMS_FILE, sms_payments)
    save_json(EMAIL_FILE, email_payments)
    save_json(COUPONS_FILE, coupons)

def generate_id(data_list):
    if not data_list:
        return 1
    return max(item.get('id', 0) for item in data_list) + 1

def get_user_by_email(email):
    return next((u for u in users if u.get('email', '').lower() == email.lower()), None)

def get_user_by_id(user_id):
    return next((u for u in users if u.get('id') == user_id), None)

# ===================== CUSTOM JINJA FILTER =====================
def datetimeformat(value, fmt='%Y-%m-%d'):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except:
            return value
    if isinstance(value, datetime):
        return value.strftime(fmt)
    return str(value)

app.jinja_env.filters['datetimeformat'] = datetimeformat

# ===================== IMAGE HELPERS =====================
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_room_image(image_url):
    if not image_url:
        return "https://source.unsplash.com/random/800x600/?room,interior"
    if image_url.startswith('http'):
        return image_url
    return url_for('static', filename=image_url)

def save_uploaded_file(file, folder_type):
    if not file or not file.filename:
        return None
    if not allowed_file(file.filename):
        return None
    try:
        filename = secure_filename(file.filename)
        unique_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"

        if folder_type == 'profile':
            folder = app.config['PROFILE_UPLOAD_FOLDER']
            url_path = f"uploads/profile_pics/{unique_name}"
        else:
            folder = app.config['ROOM_UPLOAD_FOLDER']
            url_path = f"uploads/rooms/{unique_name}"

        full_path = os.path.join(folder, unique_name)
        file.save(full_path)

        with Image.open(full_path) as img:
            img.verify()

        return url_path
    except Exception as e:
        print(f"❌ Image save error: {e}")
        return None

# ===================== FIX OLD DATA =====================
def fix_old_data():
    for room in rooms:
        if isinstance(room, dict):
            room.setdefault('price_per_night', 1500)
            room.setdefault('title', "Untitled Room")
            room.setdefault('location', "Dhaka, Bangladesh")
            room.setdefault('description', "")
            room.setdefault('image_url', None)
            room.setdefault('amenities', [])

fix_old_data()

# ===================== DEFAULT ADMIN =====================
if not any(u.get('email') == 'admin@travellerstop.com' for u in users):
    admin = {
        'id': generate_id(users),
        'username': 'admin',
        'email': 'admin@travellerstop.com',
        'password': generate_password_hash('admin123'),
        'is_admin': True,
        'role': 'admin',
        'phone': '01712345678',
        'location': 'Dhaka, Bangladesh',
        'profession': 'Administrator',
        'qualification': 'MBA',
        'profile_pic': '',
        'nid': '1234567890123',
        'nid_verified': True,
        'blocked': False,
        'created_at': datetime.now().isoformat()
    }
    users.append(admin)
    save_all()

# ===================== PROFILE COMPLETENESS =====================
def is_profile_complete(user):
    if not user:
        return False
    required = ['location', 'phone', 'profession', 'qualification', 'nid']
    return all(bool(user.get(field)) for field in required) and user.get('nid_verified', False)

@app.context_processor
def inject_user():
    user = get_user_by_id(session.get('user_id'))
    return {
        'current_user': user,
        'profile_complete': is_profile_complete(user),
        'get_room_image': get_room_image
    }

# ===================== COUPON =====================
def get_coupon(code):
    if not code:
        return None
    code = code.strip().upper()
    for c in coupons:
        if c.get('code') == code and c.get('active', False) and c.get('used', 0) < c.get('max_uses', 0):
            return c
    return None

# ===================== PAYMENT =====================
SECRET_KEY = app.config['SECRET_KEY']

@app.route('/pay', methods=['POST'])
def pay():
    if 'user_id' not in session:
        return jsonify({"status": "failed", "msg": "Login required"})

    booking_id = request.form.get('booking_id')
    name = request.form.get('name')
    method = request.form.get('method')
    try:
        amount = float(request.form.get('amount', 0))
    except (ValueError, TypeError):
        return jsonify({"status": "failed", "msg": "Invalid amount format"})
    sender = request.form.get('sender')
    trxid = request.form.get('trxid')

    if not all([booking_id, name, method, amount, sender, trxid]):
        return jsonify({"status": "failed", "msg": "All fields are required"})

    if any(p.get('trxid', '').lower() == trxid.lower() for p in payments):
        return jsonify({"status": "failed", "msg": "TRXID already used"})

    # Check both SMS and Email payments for verification
    verified_sms = next((s for s in sms_payments if s.get('trxid', '').lower() == trxid.lower() and abs(float(s.get('amount', 0)) - amount) < 0.01), None)
    verified_email = next((e for e in email_payments if e.get('trxid', '').lower() == trxid.lower() and abs(float(e.get('amount', 0)) - amount) < 0.01), None)

    if not verified_sms and not verified_email:
        if method == 'binance':
            payments.append({
                'id': generate_id(payments),
                'booking_id': int(booking_id),
                'user_id': session['user_id'],
                'name': name,
                'method': method,
                'amount': amount,
                'sender': sender,
                'trxid': trxid,
                'status': 'PENDING',
                'time': datetime.now().isoformat()
            })
            save_all()
            return jsonify({"status": "success", "msg": "Payment submitted for manual review. Admin will confirm shortly."})

        return jsonify({
            "status": "failed", 
            "msg": "Payment verification failed. No matching transaction found. If you have already paid, please contact Traveller Stop Customer Service with your Transaction ID."
        })

    try:
        booking = next((b for b in bookings if b['id'] == int(booking_id)), None)
    except (ValueError, TypeError):
        return jsonify({"status": "failed", "msg": "Invalid booking ID"})

    if not booking:
        return jsonify({"status": "failed", "msg": "Booking not found"})

    if abs(booking.get('total_amount', 0) - amount) > 0.01:
        return jsonify({
            "status": "failed", 
            "msg": f"Verification mismatch: Paid amount (৳{amount}) does not match booking total (৳{booking.get('total_amount', 0)}). Please contact support."
        })

    payments.append({
        'id': generate_id(payments),
        'booking_id': int(booking_id),
        'user_id': session['user_id'],
        'name': name,
        'method': method,
        'amount': amount,
        'sender': sender,
        'trxid': trxid,
        'status': 'PAID',
        'time': datetime.now().isoformat()
    })

    booking['payment_status'] = 'paid'
    booking['status'] = 'confirmed'
    save_all()
    return jsonify({"status": "success", "msg": "Payment Verified Successfully! Your booking is now confirmed."})

@app.route('/cancel-booking/<int:booking_id>', methods=['POST'])
def cancel_booking(booking_id):
    if 'user_id' not in session:
        return jsonify({"success": False, "message": "Login required"}), 401
    
    booking = next((b for b in bookings if b.get('id') == booking_id), None)
    if not booking:
        return jsonify({"success": False, "message": "Booking not found"}), 404
        
    if booking.get('user_id') != session['user_id']:
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    if booking.get('status') in ['cancelled', 'cancel_requested']:
        return jsonify({"success": False, "message": f"Already {booking.get('status')}"})

    if booking.get('payment_status') == 'paid':
        booking['status'] = 'cancel_requested'
        msg = "Cancellation request sent. Admin will review and process your refund."
    else:
        booking['status'] = 'cancelled'
        msg = "Booking cancelled successfully."

    save_all()
    return jsonify({"success": True, "message": msg})

@app.route('/admin/approve-cancellation/<int:booking_id>')
def admin_approve_cancellation(booking_id):
    if not session.get('is_admin'):
        return redirect(url_for('index'))
    
    booking = next((b for b in bookings if b.get('id') == booking_id), None)
    if booking and booking.get('status') == 'cancel_requested':
        booking['status'] = 'cancelled'
        save_all()
        flash(f'Cancellation for Booking #{booking_id} approved!', 'success')
    else:
        flash('Booking not found or not requested for cancellation', 'danger')
    return redirect(url_for('admin'))

# ===================== SMS WEBHOOK (For Automatic Forward) =====================
@app.route("/sms-webhook", methods=["POST"])
def sms_webhook():
    data = request.json
    if not data or data.get("secret") != SECRET_KEY:
        return jsonify({"status": "unauthorized"}), 401

    msg = data.get("message", "")
    # Improved regex to handle decimals and different currencies/labels
    amount_match = re.search(r'(?:Tk|USDT|Amount|Sent)\s*[:=]?\s*(\d+(?:\.\d+)?)', msg, re.I)
    trx_match = re.search(r'(?:TrxID|TxnID|TXID|ID)\s*[:=]?\s*([A-Za-z0-9]+)', msg, re.I)

    if not amount_match or not trx_match:
        return jsonify({"status": "invalid sms", "msg": "Could not parse amount or TrxID"})

    try:
        amt = float(amount_match.group(1))
    except ValueError:
        return jsonify({"status": "invalid amount format"})
    
    trxid = trx_match.group(1)

    if any(s.get("trxid", "").lower() == trxid.lower() for s in sms_payments):
        return jsonify({"status": "duplicate"})

    sms_payments.append({
        "amount": amt,
        "trxid": trxid,
        "raw": msg,
        "time": datetime.now().isoformat()
    })
    save_json(SMS_FILE, sms_payments)
    return jsonify({"status": "saved", "trxid": trxid, "amount": amt})

# ===================== EMAIL WEBHOOK (For Binance Pay Forward) =====================
@app.route("/email-webhook", methods=["POST"])
def email_webhook():
    data = request.json
    if not data or data.get("secret") != SECRET_KEY:
        return jsonify({"status": "unauthorized"}), 401

    content = data.get("content", "")
    # Regex to parse Binance Pay or general email confirmations
    amount_match = re.search(r'(?:Amount|Paid|Received|USDT)\s*[:=]?\s*(\d+(?:\.\d+)?)', content, re.I)
    trx_match = re.search(r'(?:TrxID|TxnID|TXID|Order ID|ID)\s*[:=]?\s*([A-Za-z0-9]+)', content, re.I)

    if not amount_match or not trx_match:
        return jsonify({"status": "invalid email", "msg": "Could not parse amount or TrxID"})

    try:
        amt = float(amount_match.group(1))
    except ValueError:
        return jsonify({"status": "invalid amount format"})
    
    trxid = trx_match.group(1)

    if any(e.get("trxid", "").lower() == trxid.lower() for e in email_payments):
        return jsonify({"status": "duplicate"})

    email_payments.append({
        "amount": amt,
        "trxid": trxid,
        "raw": content,
        "time": datetime.now().isoformat()
    })
    save_json(EMAIL_FILE, email_payments)
    return jsonify({"status": "saved", "trxid": trxid, "amount": amt})

# ===================== MAIN ROUTES =====================
@app.route('/')
def index():
    safe_rooms = [room for room in rooms if isinstance(room, dict)]
    featured = sorted(safe_rooms, key=lambda x: x.get('created_at', ''), reverse=True)[:8]
    return render_template('index.html', featured_rooms=featured)

@app.route('/about')
def about():
    return render_template('about_us.html')

@app.route('/services')
def services():
    return render_template('our_services.html')

@app.route('/contact')
def contact():
    return render_template('contact_us.html')

@app.route('/help')
def help_center():
    return render_template('help_center.html')

@app.route('/safety-tips')
def safety_tips():
    return render_template('safe_tips.html')

@app.route('/terms')
def terms():
    return render_template('terms_of_service.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy_policy.html')

@app.route('/cancellation-policy')
def cancellation_policy():
    return render_template('cancellation_policy.html')

@app.route('/rooms')
def rooms_page():
    location = request.args.get('location', '').strip()
    checkin_str = request.args.get('checkin', '').strip()
    checkout_str = request.args.get('checkout', '').strip()
    
    filtered = [r for r in rooms if isinstance(r, dict)]
    
    if location:
        filtered = [r for r in filtered if location.lower() in str(r.get('location', '')).lower()]
    
    if checkin_str and checkout_str:
        try:
            target_checkin = datetime.strptime(checkin_str, '%Y-%m-%d')
            target_checkout = datetime.strptime(checkout_str, '%Y-%m-%d')
            
            final_filtered = []
            for room in filtered:
                room_id = room.get('id')
                # A room is available if it has no overlapping bookings for these dates
                is_available = True
                for b in bookings:
                    if b.get('room_id') == room_id and b.get('status') not in ['cancelled', 'cancel_requested']:
                        b_checkin = datetime.strptime(b['checkin'], '%Y-%m-%d')
                        b_checkout = datetime.strptime(b['checkout'], '%Y-%m-%d')
                        
                        # Overlap: (TargetStart < BookEnd) and (TargetEnd > BookStart)
                        if target_checkin < b_checkout and target_checkout > b_checkin:
                            is_available = False
                            break
                if is_available:
                    final_filtered.append(room)
            filtered = final_filtered
        except ValueError:
            pass # Ignore invalid dates

    return render_template('rooms.html', rooms=filtered, location=location)

@app.route('/room/<int:room_id>')
def room_detail(room_id):
    room = next((r for r in rooms if isinstance(r, dict) and r.get('id') == room_id), None)
    if not room:
        flash('Room not found', 'danger')
        return redirect(url_for('rooms_page'))
    return render_template('room_detail.html', room=room)

@app.route('/post-room', methods=['GET', 'POST'])
def post_room():
    if 'user_id' not in session:
        flash('Please login first', 'danger')
        return redirect(url_for('login'))
    if not is_profile_complete(get_user_by_id(session['user_id'])):
        flash('Please complete your profile before posting a room.', 'warning')
        return redirect(url_for('profile'))

    if request.method == 'POST':
        try:
            image_url = "https://source.unsplash.com/random/800x600/?room,interior"
            if 'room_image' in request.files:
                file = request.files['room_image']
                if file.filename:
                    saved_path = save_uploaded_file(file, 'room')
                    if saved_path:
                        image_url = saved_path

            new_room = {
                'id': generate_id(rooms),
                'title': request.form['title'],
                'description': request.form['description'],
                'location': request.form['location'],
                'price_per_night': float(request.form['price']),
                'image_url': image_url,
                'owner_id': session['user_id'],
                'available_from': request.form.get('available_from'),
                'available_to': request.form.get('available_to'),
                'amenities': request.form.getlist('amenities'),
                'created_at': datetime.now().isoformat()
            }
            rooms.append(new_room)
            save_all()
            flash('✅ Room posted successfully!', 'success')
            return redirect(url_for('profile'))
        except Exception as e:
            flash(f'Error posting room: {str(e)}', 'danger')
    return render_template('post_room.html')

@app.route('/edit-room/<int:room_id>', methods=['GET', 'POST'])
def edit_room(room_id):
    if 'user_id' not in session:
        flash('Please login first', 'danger')
        return redirect(url_for('login'))
    room = next((r for r in rooms if r.get('id') == room_id), None)
    if not room or room.get('owner_id') != session['user_id']:
        flash('You can only edit your own rooms!', 'danger')
        return redirect(url_for('profile'))

    if request.method == 'POST':
        try:
            room['title'] = request.form['title']
            room['description'] = request.form['description']
            room['location'] = request.form['location']
            room['price_per_night'] = float(request.form['price'])
            room['available_from'] = request.form.get('available_from')
            room['available_to'] = request.form.get('available_to')
            room['amenities'] = request.form.getlist('amenities')

            if 'room_image' in request.files:
                file = request.files['room_image']
                if file.filename:
                    saved_path = save_uploaded_file(file, 'room')
                    if saved_path:
                        room['image_url'] = saved_path

            save_all()
            flash('✅ Room updated successfully!', 'success')
            return redirect(url_for('profile'))
        except Exception as e:
            flash(f'Error updating room: {str(e)}', 'danger')
    return render_template('edit_room.html', room=room)

@app.route('/delete-room/<int:room_id>', methods=['POST'])
def delete_room(room_id):
    if 'user_id' not in session:
        flash('Please login first', 'danger')
        return redirect(url_for('login'))
    room = next((r for r in rooms if r.get('id') == room_id), None)
    if not room or room.get('owner_id') != session['user_id']:
        flash('You can only delete your own rooms!', 'danger')
        return redirect(url_for('profile'))

    if room.get('image_url') and not room['image_url'].startswith('http'):
        try:
            file_path = os.path.join(BASE_DIR, 'static', room['image_url'])
            if os.path.exists(file_path):
                os.remove(file_path)
        except:
            pass

    rooms.remove(room)
    save_all()
    flash('✅ Room deleted successfully!', 'success')
    return redirect(url_for('profile'))

@app.route('/book/<int:room_id>', methods=['POST'])
def book_room(room_id):
    if 'user_id' not in session:
        flash('Please login to book', 'danger')
        return redirect(url_for('login'))
    
    user = get_user_by_id(session['user_id'])
    if not is_profile_complete(user):
        flash('Please complete your profile (including NID verification) before booking.', 'warning')
        return redirect(url_for('profile'))

    try:
        checkin_str = request.form['checkin']
        checkout_str = request.form['checkout']
        coupon_code = request.form.get('coupon_code', '').strip().upper()

        checkin = datetime.strptime(checkin_str, '%Y-%m-%d')
        checkout = datetime.strptime(checkout_str, '%Y-%m-%d')
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        if checkin < today:
            flash('Check-in date cannot be in the past', 'danger')
            return redirect(url_for('room_detail', room_id=room_id))

        nights = (checkout - checkin).days
        if nights <= 0:
            flash('Check-out must be at least one day after check-in', 'danger')
            return redirect(url_for('room_detail', room_id=room_id))

        room = next((r for r in rooms if r.get('id') == room_id), None)
        if not room:
            flash('Room not found', 'danger')
            return redirect(url_for('rooms_page'))

        # Check for overlapping bookings
        for b in bookings:
            if b.get('room_id') == room_id and b.get('status') not in ['cancelled', 'cancel_requested']:
                b_checkin = datetime.strptime(b['checkin'], '%Y-%m-%d')
                b_checkout = datetime.strptime(b['checkout'], '%Y-%m-%d')
                
                # Overlap condition: (StartA < EndB) and (EndA > StartB)
                if checkin < b_checkout and checkout > b_checkin:
                    flash('This room is already booked for the selected dates.', 'danger')
                    return redirect(url_for('room_detail', room_id=room_id))

        original_total = float(room.get('price_per_night', 1500)) * nights
        total = original_total
        discount = 0
        coupon = get_coupon(coupon_code)

        if coupon:
            discount = (original_total * coupon['discount_percent']) / 100
            total -= discount
            coupon['used'] += 1

        new_booking = {
            'id': generate_id(bookings),
            'room_id': room_id,
            'user_id': session['user_id'],
            'checkin': checkin_str,
            'checkout': checkout_str,
            'original_amount': round(original_total, 2),
            'total_amount': round(total, 2),
            'discount': round(discount, 2),
            'coupon_used': coupon_code if coupon else None,
            'status': 'pending_payment',
            'payment_status': 'unpaid',
            'booked_at': datetime.now().isoformat()
        }
        bookings.append(new_booking)
        save_all()

        flash(f'✅ Booking created! Total: ৳{total:.2f}', 'success')
        return redirect(url_for('payment_page', booking_id=new_booking['id']))

    except ValueError:
        flash('Invalid date format. Please use the date picker.', 'danger')
        return redirect(url_for('room_detail', room_id=room_id))
    except Exception as e:
        flash(f'Booking error: {str(e)}', 'danger')
        return redirect(url_for('room_detail', room_id=room_id))

@app.route('/payment/<int:booking_id>')
def payment_page(booking_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    booking = next((b for b in bookings if b['id'] == booking_id and b['user_id'] == session['user_id']), None)
    if not booking:
        flash('Booking not found', 'danger')
        return redirect(url_for('profile'))
    return render_template('payment.html', booking=booking)

# ===================== AUTH =====================
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username'].strip()
        email = request.form['email'].strip().lower()
        password = request.form['password']

        if get_user_by_email(email):
            flash('Email already registered!', 'danger')
            return redirect(url_for('signup'))

        new_user = {
            'id': generate_id(users),
            'username': username,
            'email': email,
            'password': generate_password_hash(password),
            'is_admin': False,
            'role': 'traveler',
            'phone': '', 'location': '', 'profession': '', 'qualification': '',
            'profile_pic': '', 'nid': '', 'nid_verified': False,
            'blocked': False,
            'created_at': datetime.now().isoformat()
        }
        users.append(new_user)
        save_all()
        flash('Account created successfully!', 'success')
        return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        user = get_user_by_email(email)
        if user and check_password_hash(user['password'], password):
            if user.get('blocked', False):
                flash('Your account has been blocked', 'danger')
                return redirect(url_for('login'))
            session.clear()
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['is_admin'] = user.get('is_admin', False)
            flash('Login successful!', 'success')
            return redirect(url_for('admin') if session['is_admin'] else url_for('index'))
        flash('Invalid email or password', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@app.route('/profile')
def profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = get_user_by_id(session['user_id'])
    my_rooms = [r for r in rooms if isinstance(r, dict) and r.get('owner_id') == user['id']]
    my_bookings = [b for b in bookings if isinstance(b, dict) and b.get('user_id') == user['id']]
    return render_template('profile.html', user=user, my_rooms=my_rooms, my_bookings=my_bookings)

@app.route('/update-profile', methods=['POST'])
def update_profile():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Login required'}), 401
    user = get_user_by_id(session['user_id'])
    if not user:
        return jsonify({'success': False, 'message': 'User not found'}), 404

    user['phone'] = request.form.get('phone', user.get('phone', ''))
    user['location'] = request.form.get('location', user.get('location', ''))
    user['profession'] = request.form.get('profession', user.get('profession', ''))
    user['qualification'] = request.form.get('qualification', user.get('qualification', ''))
    user['nid'] = request.form.get('nid', user.get('nid', ''))

    if 'profile_pic' in request.files:
        file = request.files['profile_pic']
        if file.filename:
            path = save_uploaded_file(file, 'profile')
            if path:
                user['profile_pic'] = path

    save_all()
    return jsonify({'success': True, 'message': 'Profile updated successfully!'})

# ===================== ADMIN =====================
@app.route('/admin')
def admin():
    if not session.get('is_admin'):
        flash('Admin access required!', 'danger')
        return redirect(url_for('index'))
    return render_template('admin.html', users=users, rooms=rooms, bookings=bookings, payments=payments)

@app.route('/admin/block-user/<int:user_id>')
def block_user(user_id):
    if not session.get('is_admin'):
        return redirect(url_for('index'))
    user = get_user_by_id(user_id)
    if user:
        user['blocked'] = not user.get('blocked', False)
        save_all()
        flash(f"User {'blocked' if user['blocked'] else 'unblocked'} successfully", 'success')
    return redirect(url_for('admin'))

@app.route('/admin/verify-nid/<int:user_id>')
def verify_nid(user_id):
    if not session.get('is_admin'):
        return redirect(url_for('index'))
    user = get_user_by_id(user_id)
    if user:
        user['nid_verified'] = True
        save_all()
        flash('NID Verified successfully!', 'success')
    return redirect(url_for('admin'))

@app.route('/admin/confirm-payment/<int:payment_id>')
def confirm_payment(payment_id):
    if not session.get('is_admin'):
        return redirect(url_for('index'))
    payment = next((p for p in payments if p.get('id') == payment_id), None)
    if payment:
        payment['status'] = 'CONFIRMED'
        booking = next((b for b in bookings if b.get('id') == payment.get('booking_id')), None)
        if booking:
            booking['payment_status'] = 'paid'
            booking['status'] = 'confirmed'
        save_all()
        flash('Payment confirmed manually', 'success')
    return redirect(url_for('admin'))

@app.route('/admin/confirm-booking/<int:booking_id>')
def admin_manual_confirm(booking_id):
    if not session.get('is_admin'):
        return redirect(url_for('index'))
    
    booking = next((b for b in bookings if b.get('id') == booking_id), None)
    if booking:
        booking['payment_status'] = 'paid'
        booking['status'] = 'confirmed'
        
        # Create a manual payment record
        payments.append({
            'id': generate_id(payments),
            'booking_id': booking['id'],
            'user_id': booking['user_id'],
            'name': 'MANUAL',
            'method': 'ADMIN_OVERRIDE',
            'amount': booking['total_amount'],
            'sender': 'ADMIN',
            'trxid': f"MANUAL_{booking['id']}_{datetime.now().strftime('%H%M%S')}",
            'status': 'CONFIRMED',
            'time': datetime.now().isoformat()
        })
        
        save_all()
        flash(f'Booking #{booking_id} confirmed manually!', 'success')
    else:
        flash('Booking not found', 'danger')
    return redirect(url_for('admin'))

@app.route('/admin/export-users')
def export_users():
    if not session.get('is_admin'):
        return redirect(url_for('index'))

    def generate():
        yield 'ID,Username,Email,Phone,Location,Profession,Qualification,NID,NID_Verified,Blocked,Created_At\n'
        for user in users:
            yield f"{user.get('id')},{user.get('username')},{user.get('email')},{user.get('phone') or ''}," \
                  f"{user.get('location') or ''},{user.get('profession') or ''},{user.get('qualification') or ''}," \
                  f"{user.get('nid') or ''},{'Yes' if user.get('nid_verified') else 'No'},{'Yes' if user.get('blocked') else 'No'}," \
                  f"{user.get('created_at')}\n"

    return Response(generate(), mimetype='text/csv',
                    headers={"Content-Disposition": "attachment; filename=users_export.csv"})

@app.route('/admin/coupons', methods=['GET', 'POST'])
def admin_coupons():
    if not session.get('is_admin'):
        return redirect(url_for('index'))
    if request.method == 'POST':
        coupon = {
            'id': generate_id(coupons),
            'code': request.form['code'].upper(),
            'discount_percent': int(request.form['discount']),
            'max_uses': int(request.form['max_uses']),
            'used': 0,
            'active': True,
            'created_at': datetime.now().isoformat()
        }
        coupons.append(coupon)
        save_all()
        flash('Coupon created successfully!', 'success')
    return render_template('admin_coupons.html', coupons=coupons)

if __name__ == '__main__':
    # Binds to 0.0.0.0 to allow external access from IP 45.77.251.165
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
