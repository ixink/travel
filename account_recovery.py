import os
import smtplib
import ssl
from email.message import EmailMessage
from hashlib import sha256
from hmac import compare_digest, digest

from itsdangerous import BadData, URLSafeTimedSerializer


def password_proof(secret, password_hash):
    return digest(secret.encode(), (password_hash or '').encode(), sha256).hex()


def make_reset_token(secret, user_id, password_hash):
    serializer = URLSafeTimedSerializer(secret, salt='traveller-stop-password-reset')
    return serializer.dumps([user_id, password_proof(secret, password_hash)])


def read_reset_token(secret, token):
    serializer = URLSafeTimedSerializer(secret, salt='traveller-stop-password-reset')
    try:
        user_id, proof = serializer.loads(token, max_age=1800)
    except (BadData, ValueError, TypeError):
        return None
    if isinstance(user_id, int) and isinstance(proof, str):
        return user_id, proof
    return None


def reset_token_matches(secret, proof, password_hash):
    return compare_digest(proof, password_proof(secret, password_hash))


def delivery_available():
    return bool(os.getenv('SMTP_HOST') and os.getenv('SMTP_FROM'))


def send_reset_email(email, link, subject, instructions):
    message = EmailMessage()
    message['From'] = os.environ['SMTP_FROM']
    message['To'] = email
    message['Subject'] = subject
    message.set_content(f'{instructions}\n\n{link}\n')
    port = int(os.getenv('SMTP_PORT', '587'))
    host = os.environ['SMTP_HOST']
    tls = ssl.create_default_context()
    connection = (
        smtplib.SMTP_SSL(host, port, timeout=8, context=tls)
        if port == 465 else smtplib.SMTP(host, port, timeout=8)
    )
    with connection as server:
        if port != 465:
            server.starttls(context=tls)
        username = os.getenv('SMTP_USERNAME')
        if username:
            server.login(username, os.environ['SMTP_PASSWORD'])
        server.send_message(message)
