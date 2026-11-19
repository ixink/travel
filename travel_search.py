import math
import re
from datetime import date


# District and city centres for approximate distances on older, text-only listings.
PLACES = (
    (23.81, 90.41, ('Dhaka', 'ঢাকা')),
    (23.89, 90.10, ('Savar', 'সাভার')),
    (24.00, 90.42, ('Gazipur', 'গাজীপুর')),
    (23.62, 90.50, ('Narayanganj', 'নারায়ণগঞ্জ', 'নারায়ণগঞ্জ')),
    (24.75, 90.40, ('Mymensingh', 'ময়মনসিংহ', 'ময়মনসিংহ')),
    (22.36, 91.78, ('Chattogram', 'Chittagong', 'চট্টগ্রাম')),
    (21.43, 92.01, ("Cox's Bazar", 'Coxs Bazar', 'Cox Bazar', 'কক্সবাজার', 'কক্স বাজার')),
    (21.82, 92.37, ('Bandarban', 'বান্দরবান')),
    (22.65, 92.17, ('Rangamati', 'রাঙামাটি', 'রাঙ্গামাটি', 'Kaptai', 'কাপ্তাই')),
    (23.12, 91.98, ('Khagrachhari', 'খাগড়াছড়ি', 'খাগড়াছড়ি')),
    (24.89, 91.87, ('Sylhet', 'সিলেট')),
    (24.31, 91.73, ('Sreemangal', 'Srimangal', 'শ্রীমঙ্গল')),
    (24.48, 91.78, ('Moulvibazar', 'Maulvibazar', 'মৌলভীবাজার')),
    (23.46, 91.18, ('Cumilla', 'Comilla', 'কুমিল্লা')),
    (22.70, 90.35, ('Barishal', 'Barisal', 'বরিশাল')),
    (21.82, 90.12, ('Kuakata', 'কুয়াকাটা', 'কুয়াকাটা')),
    (22.85, 89.54, ('Khulna', 'খুলনা')),
    (23.16, 89.22, ('Jashore', 'Jessore', 'যশোর')),
    (24.37, 88.60, ('Rajshahi', 'রাজশাহী')),
    (24.85, 89.37, ('Bogura', 'Bogra', 'বগুড়া', 'বগুড়া')),
    (25.75, 89.25, ('Rangpur', 'রংপুর')),
    (26.33, 88.55, ('Panchagarh', 'পঞ্চগড়', 'পঞ্চগড়')),
)


def normalize_place(text):
    return ' '.join(text.casefold().replace('’', "'").replace('-', ' ').split())


def place_aliases(text):
    normalized = normalize_place(text)
    for _, _, names in PLACES:
        aliases = tuple(map(normalize_place, names))
        if normalized in aliases:
            return names
    return (text,)


def district_centre(address):
    address = normalize_place(address)
    matches = [
        (len(name), latitude, longitude)
        for latitude, longitude, names in PLACES
        for name in map(normalize_place, names)
        if re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', address)
    ]
    if matches:
        _, latitude, longitude = max(matches)
        return latitude, longitude
    return None


def nearby_origin(latitude, longitude):
    try:
        latitude, longitude = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None
    if -90 <= latitude <= 90 and -180 <= longitude <= 180:
        return round(latitude, 1), round(longitude, 1)
    return None


def distance_km(origin, destination):
    lat1, lon1 = map(math.radians, origin)
    lat2, lon2 = map(math.radians, destination)
    value = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return round(12742 * math.asin(math.sqrt(min(1, value))))


def search_dates(checkin, checkout):
    try:
        start, end = date.fromisoformat(checkin), date.fromisoformat(checkout)
    except (TypeError, ValueError):
        return None
    if start.isoformat() == checkin and end.isoformat() == checkout and start < end:
        return checkin, checkout
    return None
