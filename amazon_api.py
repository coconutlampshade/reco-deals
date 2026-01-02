#!/usr/bin/env python3
"""
Amazon Creators API client for dynamic pricing.

Uses OAuth 2.0 client-credentials flow (different from legacy PA API 5.0).
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# Creators API Configuration
CREDENTIAL_ID = os.getenv("PA_API_ACCESS_KEY")  # Credential ID from CSV
CREDENTIAL_SECRET = os.getenv("PA_API_SECRET_KEY")  # Secret from CSV
PARTNER_TAG = os.getenv("PA_API_PARTNER_TAG", "recomendos-20")

# Creators API endpoints
AUTH_ENDPOINT = "https://api.amazon.com/auth/o2/token"
CREATORS_API_BASE = "https://na.creatorhub.amazon.com"  # North America endpoint

# Cache for OAuth token
_token_cache = {
    "access_token": None,
    "expires_at": 0,
}


def get_access_token() -> str:
    """
    Get OAuth 2.0 access token using client credentials flow.
    Caches token until expiry.
    """
    # Check cache
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["access_token"]

    if not CREDENTIAL_ID or not CREDENTIAL_SECRET:
        raise ValueError("Creators API credentials not configured. Set PA_API_ACCESS_KEY and PA_API_SECRET_KEY in .env")

    # Request new token
    response = requests.post(
        AUTH_ENDPOINT,
        data={
            "grant_type": "client_credentials",
            "client_id": CREDENTIAL_ID,
            "client_secret": CREDENTIAL_SECRET,
            "scope": "product_advertising",  # May need adjustment based on actual API
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=30,
    )

    if response.status_code != 200:
        raise Exception(f"OAuth token request failed: {response.status_code} - {response.text}")

    data = response.json()
    access_token = data.get("access_token")
    expires_in = data.get("expires_in", 3600)  # Default 1 hour

    # Cache token (with 5 min buffer)
    _token_cache["access_token"] = access_token
    _token_cache["expires_at"] = time.time() + expires_in - 300

    return access_token


def get_items(asins: list[str], resources: list[str] = None) -> dict:
    """
    Get item information from Creators API.

    Args:
        asins: List of ASINs to look up (max 10 per request)
        resources: List of resources to retrieve

    Returns:
        API response dict
    """
    if len(asins) > 10:
        raise ValueError("API allows maximum 10 ASINs per request")

    access_token = get_access_token()

    # Default resources for pricing (lowerCamelCase for Creators API)
    if resources is None:
        resources = [
            "itemInfo.title",
            "offers.listings.price",
            "offers.listings.savingBasis",
            "offers.listings.condition",
            "offers.listings.availability.type",
            "offers.summaries.lowestPrice",
            "images.primary.medium",
        ]

    # Build request payload (lowerCamelCase)
    payload = {
        "itemIds": asins,
        "itemIdType": "ASIN",
        "partnerTag": PARTNER_TAG,
        "partnerType": "Associates",
        "marketplace": "www.amazon.com",
        "resources": resources,
    }

    # Make request
    response = requests.post(
        f"{CREATORS_API_BASE}/paapi5/getitems",
        json=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )

    if response.status_code != 200:
        raise Exception(f"Creators API error {response.status_code}: {response.text}")

    return response.json()


def extract_price_info(item: dict) -> dict:
    """
    Extract price information from API response.
    Handles both PascalCase (PA API) and lowerCamelCase (Creators API) responses.
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
    }

    # Helper to get value from either case
    def get_val(obj, pascal_key, camel_key=None):
        if camel_key is None:
            camel_key = pascal_key[0].lower() + pascal_key[1:]
        return obj.get(pascal_key) or obj.get(camel_key)

    # Get title
    item_info = get_val(item, "ItemInfo", "itemInfo")
    if item_info:
        title_obj = get_val(item_info, "Title", "title")
        if title_obj:
            result["title"] = get_val(title_obj, "DisplayValue", "displayValue")

    # Get image
    images = get_val(item, "Images", "images")
    if images:
        primary = get_val(images, "Primary", "primary")
        if primary:
            medium = get_val(primary, "Medium", "medium")
            if medium:
                result["image_url"] = get_val(medium, "URL", "url")

    # Get detail page URL
    result["detail_page_url"] = get_val(item, "DetailPageURL", "detailPageUrl")

    # Get offers
    offers = get_val(item, "Offers", "offers")
    if not offers:
        return result

    # Get from Listings
    listings = get_val(offers, "Listings", "listings")
    if listings and len(listings) > 0:
        listing = listings[0]

        price_obj = get_val(listing, "Price", "price")
        if price_obj:
            result["current_price"] = get_val(price_obj, "Amount", "amount")
            result["currency"] = get_val(price_obj, "Currency", "currency") or "USD"

        # Get savings/list price
        saving_basis = get_val(listing, "SavingBasis", "savingBasis")
        if saving_basis:
            result["list_price"] = get_val(saving_basis, "Amount", "amount")

        # Get availability
        availability = get_val(listing, "Availability", "availability")
        if availability:
            result["availability"] = get_val(availability, "Type", "type")

    # Calculate savings
    if result["current_price"] and result["list_price"]:
        result["savings"] = result["list_price"] - result["current_price"]
        if result["list_price"] > 0:
            result["savings_percent"] = (result["savings"] / result["list_price"]) * 100

    # Check Summaries for lowest price
    summaries = get_val(offers, "Summaries", "summaries")
    if summaries:
        for summary in summaries:
            lowest = get_val(summary, "LowestPrice", "lowestPrice")
            if lowest and result["current_price"] is None:
                result["current_price"] = get_val(lowest, "Amount", "amount")
                result["currency"] = get_val(lowest, "Currency", "currency") or "USD"

    return result


def get_prices_for_asins(asins: list[str]) -> dict[str, dict]:
    """
    Get current prices for a list of ASINs.
    Handles batching (max 10 per request).

    Returns dict of ASIN -> price_info
    """
    results = {}

    for i in range(0, len(asins), 10):
        batch = asins[i:i+10]

        try:
            response = get_items(batch)

            # Handle both response formats
            items_result = response.get("ItemsResult") or response.get("itemsResult") or {}
            items = items_result.get("Items") or items_result.get("items") or []

            for item in items:
                asin = item.get("ASIN") or item.get("asin")
                if asin:
                    results[asin] = extract_price_info(item)

            # Note errors
            errors = response.get("Errors") or response.get("errors") or []
            for error in errors:
                msg = error.get("Message") or error.get("message") or "Unknown error"
                print(f"  API warning: {msg}")

        except Exception as e:
            print(f"  Error fetching batch: {e}")
            for asin in batch:
                results[asin] = {"error": str(e)}

    return results


def format_price(amount: float, currency: str = "USD") -> str:
    """Format price for display."""
    if currency == "USD":
        return f"${amount:.2f}"
    return f"{amount:.2f} {currency}"


# Test function
if __name__ == "__main__":
    print("Testing Amazon Creators API connection...")
    print(f"Credential ID: {CREDENTIAL_ID[:8]}..." if CREDENTIAL_ID else "Credential ID: NOT SET")
    print(f"Partner Tag: {PARTNER_TAG}")
    print()

    try:
        print("Step 1: Getting OAuth token...")
        token = get_access_token()
        print(f"  Token: {token[:20]}...")
        print()

        print("Step 2: Fetching test product...")
        test_asins = ["B09V3KXJPB"]
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
        import traceback
        traceback.print_exc()
