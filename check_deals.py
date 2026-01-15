#!/usr/bin/env python3
"""
Check prices for catalog products using Keepa API and identify deals.

Usage:
    python check_deals.py              # Check all products
    python check_deals.py --limit 50   # Check first 50 products
    python check_deals.py --asin B09V3KXJPB  # Check specific ASIN
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))
import config

# Keepa time epoch: January 1, 2011
KEEPA_EPOCH = datetime(2011, 1, 1)


def keepa_time_to_datetime(keepa_minutes: int) -> datetime:
    """Convert Keepa time (minutes since Jan 1, 2011) to datetime."""
    return KEEPA_EPOCH + timedelta(minutes=keepa_minutes)


def get_api_key() -> str:
    """Get Keepa API key from environment."""
    key = os.getenv("KEEPA_API_KEY")
    if not key:
        print("Error: KEEPA_API_KEY not found in environment")
        print("Please add it to your .env file")
        sys.exit(1)
    return key


def fetch_keepa_products(asins: list[str], api_key: str) -> dict:
    """
    Fetch product data from Keepa API.

    Args:
        asins: List of ASINs to look up (max 100)
        api_key: Keepa API key

    Returns:
        API response dict with product data
    """
    url = f"{config.KEEPA_API_URL}/product"
    params = {
        "key": api_key,
        "domain": config.KEEPA_DOMAIN_ID,
        "asin": ",".join(asins),
        "stats": 90,  # Get 90-day statistics
    }

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def parse_price_history(csv_data: list, price_type: int = 0) -> list[tuple[datetime, float]]:
    """
    Parse Keepa price history CSV data.

    Keepa returns prices as [time1, price1, time2, price2, ...]
    Price type 0 = Amazon price, 1 = New 3rd party, etc.
    Prices are in cents, -1 means out of stock.

    Returns list of (datetime, price_in_dollars) tuples.
    """
    if not csv_data or len(csv_data) < 2:
        return []

    history = []
    for i in range(0, len(csv_data) - 1, 2):
        keepa_time = csv_data[i]
        price_cents = csv_data[i + 1]

        if keepa_time is None or price_cents is None:
            continue
        if price_cents < 0:  # Out of stock
            continue

        dt = keepa_time_to_datetime(keepa_time)
        price_dollars = price_cents / 100.0
        history.append((dt, price_dollars))

    return history


def analyze_product(product_data: dict, stats: dict) -> dict:
    """
    Analyze a single product's price data to determine if it's a deal.

    Args:
        product_data: Keepa product data
        stats: 90-day statistics from Keepa

    Returns:
        Analysis dict with current price, deal status, etc.
    """
    result = {
        "is_deal": False,
        "deal_reasons": [],
        "current_price": None,
        "avg_90_day": None,
        "high_90_day": None,
        "low_90_day": None,
        "all_time_low": None,
        "percent_below_avg": None,
        "percent_below_high": None,
        "savings_dollars": None,
        "title": product_data.get("title"),
        "image_url": None,
        "rating": None,
        "review_count": None,
        "last_updated": datetime.now().isoformat(),
    }

    # Extract product image
    # Keepa stores images as comma-separated list of image codes (e.g., "61k2kPFbeML.jpg")
    images_csv = product_data.get("imagesCSV")
    if images_csv:
        # Get first image code
        image_codes = images_csv.split(",")
        if image_codes and image_codes[0]:
            image_code = image_codes[0]
            # Remove .jpg extension if present (we'll add size suffix)
            if image_code.endswith(".jpg"):
                image_code = image_code[:-4]
            # Build Amazon image URL (300px size)
            result["image_url"] = f"https://m.media-amazon.com/images/I/{image_code}._SL300_.jpg"

    # Get current prices - try Amazon first, then new 3rd party
    # Price indices: 0=Amazon, 1=New, 2=Used, etc.
    csv = product_data.get("csv", [])

    current_price = None
    price_source = None

    # Try Amazon price first (index 0)
    if csv and len(csv) > 0 and csv[0]:
        amazon_csv = csv[0]
        if amazon_csv and len(amazon_csv) >= 2:
            last_price = amazon_csv[-1]
            if last_price is not None and last_price > 0:
                current_price = last_price / 100.0
                price_source = "amazon"

    # Fall back to New 3rd party price (index 1)
    if current_price is None and csv and len(csv) > 1 and csv[1]:
        new_csv = csv[1]
        if new_csv and len(new_csv) >= 2:
            last_price = new_csv[-1]
            if last_price is not None and last_price > 0:
                current_price = last_price / 100.0
                price_source = "new_3rd_party"

    if current_price is None:
        result["error"] = "No current price available"
        return result

    result["current_price"] = current_price
    result["price_source"] = price_source

    # Extract 90-day stats
    # Keepa stats format varies - can be list or nested structure
    # Price type index: 0=Amazon, 1=New 3rd party
    price_idx = 0 if price_source == "amazon" else 1

    def safe_get_stat(stat_data, idx):
        """Safely extract a stat value, handling various formats."""
        if not stat_data:
            return None
        if isinstance(stat_data, list):
            if len(stat_data) > idx:
                val = stat_data[idx]
                # Handle nested lists
                if isinstance(val, list):
                    return val[-1] if val and val[-1] and val[-1] > 0 else None
                return val if val and val > 0 else None
        return None

    if stats:
        # Get averages
        avg_val = safe_get_stat(stats.get("avg"), price_idx)
        if avg_val:
            result["avg_90_day"] = avg_val / 100.0

        # Get 90-day min (low)
        min_val = safe_get_stat(stats.get("min"), price_idx)
        if min_val:
            result["low_90_day"] = min_val / 100.0

        # Get 90-day max (high)
        max_val = safe_get_stat(stats.get("max"), price_idx)
        if max_val:
            result["high_90_day"] = max_val / 100.0

        # Get all-time low
        at_low_val = safe_get_stat(stats.get("atLow"), price_idx)
        if at_low_val:
            result["all_time_low"] = at_low_val / 100.0

    # Get rating and reviews
    if product_data.get("csv") and len(product_data["csv"]) > 16:
        # Rating is at index 16, reviews at 17
        rating_csv = product_data["csv"][16] if len(product_data["csv"]) > 16 else None
        if rating_csv and len(rating_csv) >= 2 and rating_csv[-1]:
            result["rating"] = rating_csv[-1] / 10.0  # Keepa stores as 45 for 4.5

        review_csv = product_data["csv"][17] if len(product_data["csv"]) > 17 else None
        if review_csv and len(review_csv) >= 2 and review_csv[-1]:
            result["review_count"] = review_csv[-1]

    # Calculate deal metrics
    if result["avg_90_day"] and result["avg_90_day"] > 0:
        result["percent_below_avg"] = ((result["avg_90_day"] - current_price) / result["avg_90_day"]) * 100
        result["savings_dollars"] = result["avg_90_day"] - current_price

    if result["high_90_day"] and result["high_90_day"] > 0:
        result["percent_below_high"] = ((result["high_90_day"] - current_price) / result["high_90_day"]) * 100

    # Determine if this is a deal
    deal_reasons = []

    # Check: Current price is X% below 90-day average (PRIMARY deal indicator)
    if result["percent_below_avg"] and result["percent_below_avg"] >= config.DEAL_PERCENT_BELOW_AVG:
        deal_reasons.append(f"{result['percent_below_avg']:.0f}% below 90-day avg")

    # Check: Current price is X% below 90-day high (ONLY if meaningfully below average)
    # Being below a high alone doesn't make it a deal - it could still be near typical price
    if result["percent_below_high"] and result["percent_below_high"] >= config.DEAL_PERCENT_BELOW_HIGH:
        # Only count this if price is at least 10% below average (not just marginally below)
        if result["percent_below_avg"] and result["percent_below_avg"] >= 10:
            deal_reasons.append(f"{result['percent_below_high']:.0f}% below 90-day high")

    # Check: Current price is near all-time low (only if meaningfully below average)
    if result["all_time_low"] and result["all_time_low"] > 0:
        percent_above_low = ((current_price - result["all_time_low"]) / result["all_time_low"]) * 100
        # Only count all-time low if price is at least 5% below average
        if percent_above_low <= config.DEAL_NEAR_LOW_PERCENT and (result["percent_below_avg"] or 0) >= 5:
            if current_price <= result["all_time_low"]:
                deal_reasons.append("At all-time low!")
            else:
                deal_reasons.append(f"Within {percent_above_low:.0f}% of all-time low")

    # Check: Minimum dollar savings (only if meaningfully below average - at least 10%)
    if result["savings_dollars"] and result["savings_dollars"] >= config.DEAL_MIN_DISCOUNT_DOLLARS:
        if (result["percent_below_avg"] or 0) >= 10:  # Must be at least 10% below avg
            if not deal_reasons:  # Only add if no other reason yet
                deal_reasons.append(f"${result['savings_dollars']:.2f} savings")

    if deal_reasons:
        result["is_deal"] = True
        result["deal_reasons"] = deal_reasons

    return result


def check_products(asins: list[str], catalog: dict) -> dict:
    """
    Check prices for a list of ASINs and identify deals.

    Args:
        asins: List of ASINs to check
        catalog: Product catalog dict

    Returns:
        Dict of ASIN -> analysis results
    """
    api_key = get_api_key()
    results = {}

    # Process in batches
    batch_size = config.KEEPA_BATCH_SIZE
    total_batches = (len(asins) + batch_size - 1) // batch_size

    print(f"Checking {len(asins)} products in {total_batches} batches...")
    print(f"Rate limit: {config.KEEPA_TOKENS_PER_MINUTE} tokens/minute")

    for i in range(0, len(asins), batch_size):
        batch = asins[i:i + batch_size]
        batch_num = i // batch_size + 1

        print(f"\nBatch {batch_num}/{total_batches}: {len(batch)} products")

        try:
            response = fetch_keepa_products(batch, api_key)

            # Check tokens remaining
            tokens_left = response.get("tokensLeft", 0)
            refill_in = response.get("refillIn", 0)
            print(f"  Tokens remaining: {tokens_left}, refill in {refill_in}ms")

            products = response.get("products", [])

            for product in products:
                if not product:
                    continue

                asin = product.get("asin")
                if not asin:
                    continue

                stats = product.get("stats", {})
                analysis = analyze_product(product, stats)

                # Add catalog info
                if asin in catalog:
                    analysis["catalog_title"] = catalog[asin].get("title")
                    analysis["affiliate_url"] = catalog[asin].get("affiliate_url")
                    analysis["amazon_url"] = catalog[asin].get("amazon_url")
                    analysis["issues"] = catalog[asin].get("issues", [])
                    analysis["first_featured"] = catalog[asin].get("first_featured")

                results[asin] = analysis

                status = "DEAL!" if analysis["is_deal"] else "no deal"
                price_str = f"${analysis['current_price']:.2f}" if analysis.get("current_price") else "N/A"
                print(f"  {asin}: {price_str} - {status}")

            # Rate limiting: wait if we need more tokens
            if tokens_left < batch_size and i + batch_size < len(asins):
                wait_time = max(refill_in / 1000 + 1, 60 / config.KEEPA_TOKENS_PER_MINUTE * batch_size)
                print(f"  Waiting {wait_time:.1f}s for token refill...")
                time.sleep(wait_time)

        except requests.exceptions.RequestException as e:
            print(f"  Error: {e}")
            # Mark batch as failed
            for asin in batch:
                results[asin] = {"error": str(e)}

    return results


def save_deals(results: dict, output_path: Path):
    """Save deal analysis results to JSON file."""
    # Separate deals from non-deals
    deals = {asin: data for asin, data in results.items() if data.get("is_deal")}

    output = {
        "generated_at": datetime.now().isoformat(),
        "total_checked": len(results),
        "deals_found": len(deals),
        "deals": deals,
        "all_results": results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nResults saved to: {output_path}")
    return deals


def load_catalog() -> dict:
    """Load product catalog from disk."""
    if not config.CATALOG_FILE.exists():
        print(f"Error: Catalog not found at {config.CATALOG_FILE}")
        print("Run import_substack.py first to build the catalog.")
        sys.exit(1)

    with open(config.CATALOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Check prices and find deals")
    parser.add_argument("--limit", type=int, help="Limit number of products to check")
    parser.add_argument("--asin", type=str, help="Check a specific ASIN")
    parser.add_argument("--output", type=str, default="catalog/deals.json",
                        help="Output file path")
    args = parser.parse_args()

    catalog = load_catalog()
    print(f"Loaded catalog with {len(catalog)} products")

    # Determine which ASINs to check
    if args.asin:
        asins = [args.asin]
        if args.asin not in catalog:
            print(f"Warning: ASIN {args.asin} not in catalog, checking anyway")
    else:
        asins = list(catalog.keys())
        if args.limit:
            asins = asins[:args.limit]

    # Check prices
    results = check_products(asins, catalog)

    # Save results
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = config.PROJECT_ROOT / output_path

    deals = save_deals(results, output_path)

    # Print summary
    print(f"\n{'='*50}")
    print(f"SUMMARY")
    print(f"{'='*50}")
    print(f"Products checked: {len(results)}")
    print(f"Deals found: {len(deals)}")

    if deals:
        print(f"\nTop deals:")
        # Sort deals by percent below average
        sorted_deals = sorted(
            deals.items(),
            key=lambda x: x[1].get("percent_below_avg") or 0,
            reverse=True
        )
        for asin, data in sorted_deals[:10]:
            title = (data.get("catalog_title") or data.get("title") or asin)[:50]
            price = data.get("current_price", 0)
            reasons = ", ".join(data.get("deal_reasons", []))
            print(f"  ${price:.2f} - {title}")
            print(f"    {reasons}")


if __name__ == "__main__":
    main()
