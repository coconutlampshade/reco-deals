"""
Vercel serverless function for fetching live Amazon prices.

Endpoint: GET /api/prices?asins=B01ABC,B02DEF
Returns: {"B01ABC": {"current_price": 15.99, "list_price": 29.99}, ...}
"""

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import requests

# Try to load .env for local development
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# PA API Configuration (from Vercel environment variables)
PA_API_ACCESS_KEY = os.getenv("PA_API_ACCESS_KEY")
PA_API_SECRET_KEY = os.getenv("PA_API_SECRET_KEY")
PA_API_PARTNER_TAG = os.getenv("PA_API_PARTNER_TAG", "recomendos-20")
PA_API_HOST = os.getenv("PA_API_HOST", "webservices.amazon.com")
PA_API_REGION = os.getenv("PA_API_REGION", "us-east-1")

PA_API_ENDPOINT = f"https://{PA_API_HOST}/paapi5/getitems"


def sign_request(method, service, host, region, endpoint, headers, payload, access_key, secret_key):
    """
    Sign a request using AWS Signature Version 4.
    """
    t = datetime.now(timezone.utc)
    amz_date = t.strftime('%Y%m%dT%H%M%SZ')
    date_stamp = t.strftime('%Y%m%d')

    headers['x-amz-date'] = amz_date
    headers['host'] = host

    canonical_headers = '\n'.join([f"{k.lower()}:{v}" for k, v in sorted(headers.items())]) + '\n'
    signed_headers = ';'.join([k.lower() for k in sorted(headers.keys())])

    payload_hash = hashlib.sha256(payload.encode('utf-8')).hexdigest()

    parsed = urlparse(endpoint)
    canonical_uri = parsed.path or '/'
    canonical_querystring = ''

    canonical_request = f"{method}\n{canonical_uri}\n{canonical_querystring}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"

    algorithm = 'AWS4-HMAC-SHA256'
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = f"{algorithm}\n{amz_date}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"

    def sign(key, msg):
        return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

    k_date = sign(('AWS4' + secret_key).encode('utf-8'), date_stamp)
    k_region = sign(k_date, region)
    k_service = sign(k_region, service)
    k_signing = sign(k_service, 'aws4_request')

    signature = hmac.new(k_signing, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()

    authorization_header = f"{algorithm} Credential={access_key}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"
    headers['Authorization'] = authorization_header

    return headers


def get_items(asins: list[str]) -> dict:
    """
    Get item information from PA API.
    """
    if not PA_API_ACCESS_KEY or not PA_API_SECRET_KEY:
        raise ValueError("PA API credentials not configured")

    if len(asins) > 10:
        raise ValueError("PA API allows maximum 10 ASINs per request")

    resources = [
        "Offers.Listings.Price",
        "Offers.Listings.SavingBasis",
    ]

    payload = {
        "ItemIds": asins,
        "ItemIdType": "ASIN",
        "PartnerTag": PA_API_PARTNER_TAG,
        "PartnerType": "Associates",
        "Marketplace": "www.amazon.com",
        "Resources": resources,
    }

    payload_json = json.dumps(payload)

    headers = {
        'content-type': 'application/json; charset=utf-8',
        'content-encoding': 'amz-1.0',
        'x-amz-target': 'com.amazon.paapi5.v1.ProductAdvertisingAPIv1.GetItems',
    }

    signed_headers = sign_request(
        method='POST',
        service='ProductAdvertisingAPI',
        host=PA_API_HOST,
        region=PA_API_REGION,
        endpoint=PA_API_ENDPOINT,
        headers=headers.copy(),
        payload=payload_json,
        access_key=PA_API_ACCESS_KEY,
        secret_key=PA_API_SECRET_KEY,
    )

    response = requests.post(
        PA_API_ENDPOINT,
        headers=signed_headers,
        data=payload_json,
        timeout=30,
    )

    if response.status_code != 200:
        raise Exception(f"PA API error {response.status_code}: {response.text}")

    return response.json()


def extract_prices(api_response: dict) -> dict:
    """
    Extract prices from PA API response.
    Returns dict of ASIN -> {current_price, list_price}
    """
    results = {}

    if "ItemsResult" not in api_response or "Items" not in api_response["ItemsResult"]:
        return results

    for item in api_response["ItemsResult"]["Items"]:
        asin = item.get("ASIN")
        if not asin:
            continue

        price_info = {"current_price": None, "list_price": None}

        if "Offers" in item and "Listings" in item["Offers"]:
            listings = item["Offers"]["Listings"]
            if listings:
                listing = listings[0]
                if "Price" in listing:
                    price_info["current_price"] = listing["Price"].get("Amount")
                if "SavingBasis" in listing:
                    price_info["list_price"] = listing["SavingBasis"].get("Amount")

        results[asin] = price_info

    return results


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Parse query string
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        # Get ASINs from query parameter
        asins_param = params.get('asins', [''])[0]
        if not asins_param:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Missing 'asins' parameter"}).encode())
            return

        asins = [a.strip() for a in asins_param.split(',') if a.strip()]
        if not asins:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": "No valid ASINs provided"}).encode())
            return

        if len(asins) > 10:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Maximum 10 ASINs per request"}).encode())
            return

        try:
            api_response = get_items(asins)
            prices = extract_prices(api_response)

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'public, max-age=300')  # 5-minute cache
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(prices).encode())

        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def do_OPTIONS(self):
        # Handle CORS preflight
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
