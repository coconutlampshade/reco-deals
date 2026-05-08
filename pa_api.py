#!/usr/bin/env python3
"""
Amazon Product Advertising API (PA API) 5.0 client.

Fetches real-time pricing and availability data for products.

Note: Supports both old AWS-style credentials and new Associates Central credentials.
"""

import hashlib
import hmac
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv

load_dotenv()

INVALID_ASINS_FILE = Path(__file__).parent / "catalog" / "invalid_asins.json"
_INVALID_ASIN_RE = re.compile(r"\bItemIds?\s+([A-Z0-9]{10})\b")

PA_API_CACHE_FILE = Path(__file__).parent / "catalog" / "pa_api_cache.json"
PA_API_CACHE_TTL_SECS = int(os.getenv("PA_API_CACHE_TTL_SECS", "14400"))  # 4h default


def _load_pa_cache() -> dict:
    if not PA_API_CACHE_FILE.exists():
        return {}
    try:
        return json.loads(PA_API_CACHE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_pa_cache(cache: dict):
    try:
        PA_API_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        PA_API_CACHE_FILE.write_text(json.dumps(cache, indent=2))
    except OSError:
        pass


def _is_cache_fresh(entry: dict, max_age_secs: int) -> bool:
    fetched_at = entry.get("fetched_at")
    if not fetched_at:
        return False
    try:
        ts = datetime.fromisoformat(fetched_at)
    except (ValueError, TypeError):
        return False
    age = (datetime.now() - ts).total_seconds()
    return age < max_age_secs


def _record_invalid_asins(error_messages):
    """Append/update entries in catalog/invalid_asins.json for any ASIN named in PA API errors.

    error_messages: iterable of (code, message) tuples from PA API "Errors" array.
    """
    if not error_messages:
        return
    flagged = {}
    for code, message in error_messages:
        for asin in _INVALID_ASIN_RE.findall(message or ""):
            flagged[asin] = (code or "", message or "")
    if not flagged:
        return

    try:
        existing = json.loads(INVALID_ASINS_FILE.read_text()) if INVALID_ASINS_FILE.exists() else {}
    except (json.JSONDecodeError, OSError):
        existing = {}

    now = datetime.now().isoformat(timespec="seconds")
    for asin, (code, message) in flagged.items():
        entry = existing.get(asin, {"first_seen": now, "count": 0})
        entry["last_seen"] = now
        entry["last_code"] = code
        entry["last_message"] = message
        entry["count"] = entry.get("count", 0) + 1
        existing[asin] = entry

    try:
        INVALID_ASINS_FILE.parent.mkdir(parents=True, exist_ok=True)
        INVALID_ASINS_FILE.write_text(json.dumps(existing, indent=2))
    except OSError:
        pass

# PA API Configuration
PA_API_ACCESS_KEY = os.getenv("PA_API_ACCESS_KEY")
PA_API_SECRET_KEY = os.getenv("PA_API_SECRET_KEY")
PA_API_PARTNER_TAG = os.getenv("PA_API_PARTNER_TAG", "recomendos-20")
PA_API_HOST = os.getenv("PA_API_HOST", "webservices.amazon.com")
PA_API_REGION = os.getenv("PA_API_REGION", "us-east-1")

# API endpoint
PA_API_ENDPOINT = f"https://{PA_API_HOST}/paapi5/getitems"

# Debug mode
DEBUG = os.getenv("PA_API_DEBUG", "").lower() == "true"


def sign_request(method, service, host, region, endpoint, headers, payload, access_key, secret_key):
    """
    Sign a request using AWS Signature Version 4.

    PA API 5.0 uses AWS SigV4 for authentication.
    """
    # Create canonical request
    t = datetime.now(timezone.utc)
    amz_date = t.strftime('%Y%m%dT%H%M%SZ')
    date_stamp = t.strftime('%Y%m%d')

    # Update headers with date
    headers['x-amz-date'] = amz_date
    headers['host'] = host

    # Create canonical headers string
    canonical_headers = '\n'.join([f"{k.lower()}:{v}" for k, v in sorted(headers.items())]) + '\n'
    signed_headers = ';'.join([k.lower() for k in sorted(headers.keys())])

    # Hash the payload
    payload_hash = hashlib.sha256(payload.encode('utf-8')).hexdigest()

    # Parse the endpoint path
    from urllib.parse import urlparse
    parsed = urlparse(endpoint)
    canonical_uri = parsed.path or '/'
    canonical_querystring = ''

    # Create canonical request
    canonical_request = f"{method}\n{canonical_uri}\n{canonical_querystring}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"

    # Create string to sign
    algorithm = 'AWS4-HMAC-SHA256'
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = f"{algorithm}\n{amz_date}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"

    # Create signing key
    def sign(key, msg):
        return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

    k_date = sign(('AWS4' + secret_key).encode('utf-8'), date_stamp)
    k_region = sign(k_date, region)
    k_service = sign(k_region, service)
    k_signing = sign(k_service, 'aws4_request')

    # Create signature
    signature = hmac.new(k_signing, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()

    # Create authorization header
    authorization_header = f"{algorithm} Credential={access_key}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"

    headers['Authorization'] = authorization_header

    return headers


def get_items(asins: list[str], resources: list[str] = None) -> dict:
    """
    Get item information from PA API.

    Args:
        asins: List of ASINs to look up (max 10 per request)
        resources: List of resources to retrieve (default: prices, images, titles)

    Returns:
        API response dict
    """
    if not PA_API_ACCESS_KEY or not PA_API_SECRET_KEY:
        raise ValueError("PA API credentials not configured. Set PA_API_ACCESS_KEY and PA_API_SECRET_KEY in .env")

    if len(asins) > 10:
        raise ValueError("PA API allows maximum 10 ASINs per request")

    # Default resources for pricing
    if resources is None:
        resources = [
            "ItemInfo.Title",
            "ItemInfo.Classifications",
            "ItemInfo.Features",
            "CustomerReviews.Count",
            "CustomerReviews.StarRating",
            "Offers.Listings.Price",
            "Offers.Listings.SavingBasis",
            "Offers.Listings.Condition",
            "Offers.Listings.Availability.Type",
            "Offers.Summaries.LowestPrice",
            "Images.Primary.Medium",
        ]

    # Build request payload
    payload = {
        "ItemIds": asins,
        "ItemIdType": "ASIN",
        "PartnerTag": PA_API_PARTNER_TAG,
        "PartnerType": "Associates",
        "Marketplace": "www.amazon.com",
        "Resources": resources,
    }

    payload_json = json.dumps(payload)

    # Build headers
    headers = {
        'content-type': 'application/json; charset=utf-8',
        'content-encoding': 'amz-1.0',
        'x-amz-target': 'com.amazon.paapi5.v1.ProductAdvertisingAPIv1.GetItems',
    }

    # Sign the request
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

    # Make request
    response = requests.post(
        PA_API_ENDPOINT,
        headers=signed_headers,
        data=payload_json,
        timeout=30,
    )

    if response.status_code != 200:
        error_msg = f"PA API error {response.status_code}: {response.text}"
        raise Exception(error_msg)

    return response.json()


def get_variations(asin: str, resources: list[str] = None) -> dict:
    """
    Get product variations (different colors, sizes, etc.) from PA API.

    Args:
        asin: Parent ASIN to get variations for
        resources: List of resources to retrieve

    Returns:
        API response dict with variation items
    """
    if not PA_API_ACCESS_KEY or not PA_API_SECRET_KEY:
        raise ValueError("PA API credentials not configured. Set PA_API_ACCESS_KEY and PA_API_SECRET_KEY in .env")

    if resources is None:
        resources = [
            "ItemInfo.Title",
            "CustomerReviews.Count",
            "CustomerReviews.StarRating",
            "Offers.Listings.Price",
            "Offers.Listings.Availability.Type",
            "Offers.Listings.Condition",
            "Images.Primary.Medium",
            "VariationSummary.VariationDimension",
            "VariationSummary.Price.HighestPrice",
            "VariationSummary.Price.LowestPrice",
        ]

    endpoint = f"https://{PA_API_HOST}/paapi5/getvariations"

    payload = {
        "ASIN": asin,
        "PartnerTag": PA_API_PARTNER_TAG,
        "PartnerType": "Associates",
        "Marketplace": "www.amazon.com",
        "Resources": resources,
    }

    payload_json = json.dumps(payload)

    headers = {
        'content-type': 'application/json; charset=utf-8',
        'content-encoding': 'amz-1.0',
        'x-amz-target': 'com.amazon.paapi5.v1.ProductAdvertisingAPIv1.GetVariations',
    }

    signed_headers = sign_request(
        method='POST',
        service='ProductAdvertisingAPI',
        host=PA_API_HOST,
        region=PA_API_REGION,
        endpoint=endpoint,
        headers=headers.copy(),
        payload=payload_json,
        access_key=PA_API_ACCESS_KEY,
        secret_key=PA_API_SECRET_KEY,
    )

    response = requests.post(
        endpoint,
        headers=signed_headers,
        data=payload_json,
        timeout=30,
    )

    if response.status_code != 200:
        error_msg = f"PA API GetVariations error {response.status_code}: {response.text}"
        raise Exception(error_msg)

    return response.json()


def extract_price_info(item: dict) -> dict:
    """
    Extract price information from a PA API item response.

    Returns dict with:
        - current_price: Current listing price
        - currency: Currency code
        - savings: Savings amount (if on sale)
        - savings_percent: Savings percentage
        - availability: Availability status
        - list_price: Original list price (if available)
    """
    result = {
        "current_price": None,
        "currency": "USD",
        "savings": None,
        "savings_percent": None,
        "availability": None,
        "list_price": None,
        "title": None,
        "image_url": None,
        "detail_page_url": None,
        "product_group": None,
        "binding": None,
        "review_count": None,
        "star_rating": None,
        "product_features": [],
    }

    # Get title
    if "ItemInfo" in item and "Title" in item["ItemInfo"]:
        result["title"] = item["ItemInfo"]["Title"].get("DisplayValue")

    # Get product features
    if "ItemInfo" in item and "Features" in item["ItemInfo"]:
        features = item["ItemInfo"]["Features"].get("DisplayValues", [])
        if features:
            result["product_features"] = features

    # Get classifications (product group, binding)
    if "ItemInfo" in item and "Classifications" in item["ItemInfo"]:
        classifications = item["ItemInfo"]["Classifications"]
        result["product_group"] = classifications.get("ProductGroup", {}).get("DisplayValue")
        result["binding"] = classifications.get("Binding", {}).get("DisplayValue")

    # Get customer reviews (popularity indicator)
    if "CustomerReviews" in item:
        reviews = item["CustomerReviews"]
        result["review_count"] = reviews.get("Count")
        if "StarRating" in reviews:
            result["star_rating"] = reviews["StarRating"].get("Value")

    # Get image
    if "Images" in item and "Primary" in item["Images"]:
        primary = item["Images"]["Primary"]
        if "Medium" in primary:
            result["image_url"] = primary["Medium"].get("URL")

    # Get detail page URL
    result["detail_page_url"] = item.get("DetailPageURL")

    # Get offers/listings
    if "Offers" not in item:
        return result

    offers = item["Offers"]

    # Get from Listings (actual current offers)
    if "Listings" in offers and offers["Listings"]:
        listing = offers["Listings"][0]  # Get first/best listing

        if "Price" in listing:
            price_info = listing["Price"]
            result["current_price"] = price_info.get("Amount")
            result["currency"] = price_info.get("Currency", "USD")

        # Get savings info
        if "SavingBasis" in listing:
            basis = listing["SavingBasis"]
            result["list_price"] = basis.get("Amount")

        # Get availability
        if "Availability" in listing:
            result["availability"] = listing["Availability"].get("Type")

    # Calculate savings if we have both prices
    if result["current_price"] and result["list_price"]:
        result["savings"] = result["list_price"] - result["current_price"]
        if result["list_price"] > 0:
            result["savings_percent"] = (result["savings"] / result["list_price"]) * 100

    # Also check Summaries for lowest price
    if "Summaries" in offers:
        for summary in offers["Summaries"]:
            if "LowestPrice" in summary:
                lowest = summary["LowestPrice"]
                # Use this if we don't have a current price
                if result["current_price"] is None:
                    result["current_price"] = lowest.get("Amount")
                    result["currency"] = lowest.get("Currency", "USD")

    return result


def get_prices_for_asins(asins: list[str], use_cache: bool = True) -> dict[str, dict]:
    """
    Get current prices for a list of ASINs.

    Handles batching (max 10 per request) automatically.
    Cached results (under PA_API_CACHE_TTL_SECS) skip the API entirely.
    Pass use_cache=False to force a fresh fetch.

    Returns dict of ASIN -> price_info
    """
    import time
    results = {}

    cache = _load_pa_cache() if use_cache else {}
    cache_hits = 0
    if use_cache:
        remaining = []
        for asin in asins:
            entry = cache.get(asin)
            if entry and _is_cache_fresh(entry, PA_API_CACHE_TTL_SECS):
                results[asin] = entry["data"]
                cache_hits += 1
            else:
                remaining.append(asin)
        asins_to_fetch = remaining
    else:
        asins_to_fetch = list(asins)

    if cache_hits:
        print(f"  PA API cache: {cache_hits}/{len(asins)} hits, fetching {len(asins_to_fetch)} fresh")

    # Process in batches of 10
    for i in range(0, len(asins_to_fetch), 10):
        batch = asins_to_fetch[i:i+10]

        # Brief delay between batches to avoid 429 throttling
        if i > 0:
            time.sleep(1)

        try:
            response = get_items(batch)

            # Process successful items
            if "ItemsResult" in response and "Items" in response["ItemsResult"]:
                now = datetime.now().isoformat(timespec="seconds")
                for item in response["ItemsResult"]["Items"]:
                    asin = item.get("ASIN")
                    if asin:
                        info = extract_price_info(item)
                        results[asin] = info
                        if use_cache:
                            cache[asin] = {"data": info, "fetched_at": now}

            # Note any errors
            if "Errors" in response:
                error_pairs = []
                for error in response["Errors"]:
                    msg = error.get("Message", "Unknown error")
                    code = error.get("Code", "")
                    print(f"  PA API warning: {msg}")
                    error_pairs.append((code, msg))
                _record_invalid_asins(error_pairs)

        except Exception as e:
            print(f"  Error fetching batch: {e}")
            # Mark batch as failed
            for asin in batch:
                results[asin] = {"error": str(e)}

    if use_cache and asins_to_fetch:
        _save_pa_cache(cache)

    return results


def format_price(amount: float, currency: str = "USD") -> str:
    """Format price for display."""
    if currency == "USD":
        return f"${amount:.2f}"
    return f"{amount:.2f} {currency}"


# Test function
if __name__ == "__main__":
    # Test with a sample ASIN
    test_asins = ["B09V3KXJPB"]  # Example ASIN

    print("Testing PA API connection...")
    print(f"Access Key: {PA_API_ACCESS_KEY[:8]}..." if PA_API_ACCESS_KEY else "Access Key: NOT SET")
    print(f"Partner Tag: {PA_API_PARTNER_TAG}")
    print()

    try:
        results = get_prices_for_asins(test_asins)
        for asin, info in results.items():
            print(f"ASIN: {asin}")
            if "error" in info:
                print(f"  Error: {info['error']}")
            else:
                print(f"  Title: {info.get('title', 'N/A')}")
                if info.get("current_price"):
                    print(f"  Price: {format_price(info['current_price'], info.get('currency', 'USD'))}")
                if info.get("list_price"):
                    print(f"  List Price: {format_price(info['list_price'], info.get('currency', 'USD'))}")
                if info.get("savings_percent"):
                    print(f"  Savings: {info['savings_percent']:.0f}%")
                print(f"  Availability: {info.get('availability', 'N/A')}")
    except Exception as e:
        print(f"Error: {e}")
