from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlsplit

import requests


def payment_amount(value):
    try:
        amount = Decimal(str(value))
        if amount.is_finite() and amount > 0:
            rounded = amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            return rounded if rounded > 0 else None
    except InvalidOperation:
        pass
    return None


def gateway_request(base_url, api_key, endpoint, payload):
    response = requests.post(
        f'{base_url.rstrip("/")}/{endpoint}',
        json=payload,
        headers={'RT-UDDOKTAPAY-API-KEY': api_key, 'Accept': 'application/json'},
        timeout=(5, 15),
        allow_redirects=False,
    )
    response.raise_for_status()
    if response.status_code != 200:
        raise ValueError('Unexpected gateway response status')
    result = response.json()
    if not isinstance(result, dict):
        raise ValueError('Unexpected gateway response format')
    return result


def checkout_url(result, base_url):
    url = result.get('payment_url')
    if result.get('status') is not True or not isinstance(url, str):
        raise ValueError('Gateway did not return a checkout URL')
    target = urlsplit(url)
    gateway = urlsplit(base_url)
    if target.scheme != 'https' or target.netloc != gateway.netloc:
        raise ValueError('Gateway returned an unexpected checkout host')
    return url
