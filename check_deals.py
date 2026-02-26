#!/usr/bin/env python3
"""
Check prices for catalog products using Keepa API and identify deals.

Usage:
    python check_deals.py              # Check all products (skips long-unavailable)
    python check_deals.py --full-scan  # Check ALL products including long-unavailable
    python check_deals.py --limit 50   # Check first 50 products
    python check_deals.py --asin B09V3KXJPB  # Check specific ASIN
    python check_deals.py --random 1000  # Random sample of 1000 products (weighted toward unfeatured)
"""

import argparse
import json
import os
import random
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


def fetch_keepa_products(asins: list[str], api_key: str, include_offers: bool = False) -> dict:
    """
    Fetch product data from Keepa API.

    Args:
        asins: List of ASINs to look up (max 100)
        api_key: Keepa API key
        include_offers: If True, include offers=20 (costs 3 tokens/ASIN instead of 1)

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
    if include_offers:
        params["offers"] = 20  # Include seller offers for Buy Box / Prime-exclusive pricing

    from utils import api_request_with_retry

    def _do_request():
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    return api_request_with_retry(_do_request)


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
        "list_price": None,
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
        "deal_score": 0,
        "last_updated": datetime.now().isoformat(),
    }

    # Extract product image
    # Try new `images` array first (Keepa replacing imagesCSV on April 20, 2026)
    images = product_data.get("images")
    if images and len(images) > 0:
        # New format: array of Image objects with 'l' (large) and 'm' (medium) filenames
        image_code = images[0].get("l") or images[0].get("m", "")
        if image_code:
            if image_code.endswith(".jpg"):
                image_code = image_code[:-4]
            result["image_url"] = f"https://m.media-amazon.com/images/I/{image_code}._SL300_.jpg"
    else:
        # Legacy fallback (imagesCSV removed April 20, 2026)
        images_csv = product_data.get("imagesCSV")
        if images_csv:
            image_codes = images_csv.split(",")
            if image_codes and image_codes[0]:
                image_code = image_codes[0]
                if image_code.endswith(".jpg"):
                    image_code = image_code[:-4]
                result["image_url"] = f"https://m.media-amazon.com/images/I/{image_code}._SL300_.jpg"

    # Parse price, stats, rating, and deal metrics using shared utilities
    from keepa_utils import (
        parse_keepa_current_price, parse_keepa_buybox_price, parse_keepa_stats,
        parse_keepa_rating, calculate_deal_metrics, calculate_deal_score,
    )

    current_price, price_source = parse_keepa_current_price(product_data, stats)
    if current_price is None:
        result["error"] = "No current price available"
        return result

    # Override with Buy Box / Prime-exclusive price from offers data when available
    bb_price, bb_source = parse_keepa_buybox_price(product_data)
    if bb_price is not None and bb_price <= current_price:
        current_price = bb_price
        price_source = bb_source

    result["current_price"] = current_price
    result["price_source"] = price_source

    stat_values = parse_keepa_stats(stats, price_source)
    result.update(stat_values)

    rating, review_count = parse_keepa_rating(product_data)
    result["rating"] = rating
    result["review_count"] = review_count

    metrics = calculate_deal_metrics(current_price, result["avg_90_day"], result["high_90_day"])
    result.update(metrics)

    # Determine if this is a deal: must be 10%+ below 90-day average
    if result["percent_below_avg"] and result["percent_below_avg"] >= config.DEAL_PERCENT_BELOW_AVG:
        result["is_deal"] = True
        result["deal_reasons"] = [f"{result['percent_below_avg']:.0f}% below 90-day avg"]

    # Composite deal score for ranking
    result["deal_score"] = calculate_deal_score(
        current_price, result["percent_below_avg"], result["savings_dollars"],
        result["low_90_day"], rating, review_count,
    )

    return result


def _run_batches(asins: list[str], catalog: dict, api_key: str,
                  include_offers: bool = False, label: str = "Scan",
                  checkpoint_enabled: bool = False) -> dict:
    """
    Run Keepa batches for a list of ASINs.

    Args:
        asins: List of ASINs to check
        catalog: Product catalog dict
        api_key: Keepa API key
        include_offers: Whether to include offers data (3 tokens/ASIN vs 1)
        label: Label for log output
        checkpoint_enabled: If True, save checkpoint every 10 batches

    Returns:
        Dict of ASIN -> analysis results
    """
    results = {}
    batch_size = config.KEEPA_BATCH_SIZE
    tokens_per_product = 3 if include_offers else 1
    total_batches = (len(asins) + batch_size - 1) // batch_size

    print(f"\n{'='*50}")
    print(f"{label}: {len(asins)} products in {total_batches} batches "
          f"({tokens_per_product} token{'s' if tokens_per_product > 1 else ''}/product)")
    print(f"Rate limit: {config.KEEPA_TOKENS_PER_MINUTE} tokens/minute")

    for i in range(0, len(asins), batch_size):
        batch = asins[i:i + batch_size]
        batch_num = i // batch_size + 1

        print(f"\nBatch {batch_num}/{total_batches}: {len(batch)} products")

        try:
            response = fetch_keepa_products(batch, api_key, include_offers=include_offers)

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

            # Save checkpoint every 10 batches during Pass 1
            if checkpoint_enabled and batch_num % 10 == 0:
                remaining = asins[i + batch_size:]
                save_checkpoint(results, remaining)
                print(f"  Checkpoint saved ({len(results)} results, {len(remaining)} remaining)")

            # Rate limiting: calculate wait based on actual token cost
            tokens_used = len(batch) * tokens_per_product
            if tokens_left < tokens_used and i + batch_size < len(asins):
                wait_time = max(refill_in / 1000 + 1,
                                60 / config.KEEPA_TOKENS_PER_MINUTE * tokens_used)
                print(f"  Waiting {wait_time:.1f}s for token refill...")
                time.sleep(wait_time)

        except requests.exceptions.RequestException as e:
            print(f"  Error: {e}")
            # Mark batch as failed
            for asin in batch:
                results[asin] = {"error": str(e)}

    return results


def check_products(asins: list[str], catalog: dict) -> dict:
    """
    Check prices for a list of ASINs using a two-pass approach.

    Pass 1 (lightweight): Check all products at 1 token each (no offers data).
    Pass 2 (deep scan): Re-check deal candidates with offers=20 (3 tokens each)
                         to get accurate Buy Box / Prime-exclusive pricing.

    Args:
        asins: List of ASINs to check
        catalog: Product catalog dict

    Returns:
        Dict of ASIN -> analysis results
    """
    api_key = get_api_key()

    # --- Check for checkpoint from a previous interrupted run ---
    results = {}
    checkpoint = load_checkpoint()
    if checkpoint:
        results = checkpoint["results"]
        remaining = checkpoint["remaining_asins"]
        # Only resume if remaining ASINs are a subset of requested ASINs
        requested_set = set(asins)
        remaining = [a for a in remaining if a in requested_set]
        print(f"\nResuming from checkpoint: {len(results)} already done, "
              f"{len(remaining)} remaining")
        if remaining:
            resumed = _run_batches(remaining, catalog, api_key,
                                   include_offers=False,
                                   label="Pass 1 — Resumed lightweight scan",
                                   checkpoint_enabled=True)
            results.update(resumed)
        else:
            print("No remaining ASINs to check, using checkpoint results")
    else:
        # --- Pass 1: Lightweight scan of all products (1 token/ASIN) ---
        results = _run_batches(asins, catalog, api_key,
                               include_offers=False,
                               label="Pass 1 — Lightweight scan",
                               checkpoint_enabled=True)

    # Identify deal candidates for Pass 2
    # Include: flagged deals, near-deals (5%+ below avg), and products with good scores
    deal_candidates = []
    for asin, data in results.items():
        if data.get("error"):
            continue
        if data.get("is_deal"):
            deal_candidates.append(asin)
        elif (data.get("percent_below_avg") or 0) >= 5:
            deal_candidates.append(asin)
        elif (data.get("deal_score") or 0) >= 20:
            deal_candidates.append(asin)

    print(f"\n{'='*50}")
    print(f"Pass 1 complete: {len(results)} checked, {len(deal_candidates)} candidates for deep scan")

    if not deal_candidates:
        return results

    # --- Pass 2: Deep scan deal candidates with offers data (3 tokens/ASIN) ---
    deep_results = _run_batches(deal_candidates, catalog, api_key,
                                include_offers=True, label="Pass 2 — Deep scan (with offers)")

    # Merge: deep scan results override pass 1 for these ASINs
    for asin, data in deep_results.items():
        if not data.get("error"):
            results[asin] = data

    print(f"\n{'='*50}")
    print(f"Pass 2 complete: {len(deep_results)} products re-checked with offer data")

    return results


def save_deals(results: dict, output_path: Path):
    """Save deal analysis results to JSON file."""
    # Include all products with valid prices (gives review UI more options)
    # Products still have is_deal flag to indicate strict deals (20%+ below avg)
    products_with_prices = {
        asin: data for asin, data in results.items()
        if data.get("current_price") is not None
    }
    strict_deals = {asin: data for asin, data in products_with_prices.items() if data.get("is_deal")}

    output = {
        "generated_at": datetime.now().isoformat(),
        "total_checked": len(results),
        "deals_found": len(strict_deals),
        "products_with_prices": len(products_with_prices),
        "deals": products_with_prices,  # Include all priced products for review UI
        "all_results": results,
    }

    from utils import atomic_json_write
    atomic_json_write(output_path, output)

    print(f"\nResults saved to: {output_path}")
    print(f"Products with valid prices: {len(products_with_prices)}")
    return strict_deals


def load_catalog() -> dict:
    """Load product catalog from disk."""
    if not config.CATALOG_FILE.exists():
        print(f"Error: Catalog not found at {config.CATALOG_FILE}")
        print("Run import_substack.py first to build the catalog.")
        sys.exit(1)

    with open(config.CATALOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_featured_history() -> set:
    """Load set of ASINs that have been featured in newsletters."""
    history_file = config.PROJECT_ROOT / "catalog" / "featured_history.json"
    if history_file.exists():
        with open(history_file, "r", encoding="utf-8") as f:
            return set(json.load(f).keys())
    return set()


def load_unavailable_tracking() -> dict:
    """Load unavailable product tracking data."""
    if config.UNAVAILABLE_TRACKING_FILE.exists():
        with open(config.UNAVAILABLE_TRACKING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_unavailable_tracking(tracking: dict):
    """Save unavailable product tracking data."""
    from utils import atomic_json_write
    atomic_json_write(config.UNAVAILABLE_TRACKING_FILE, tracking)


def update_unavailable_tracking(tracking: dict, results: dict) -> dict:
    """
    Update tracking based on scan results.

    Products with no current price get their consecutive_days incremented.
    Products with a valid price are removed from tracking.
    """
    today = datetime.now().strftime("%Y-%m-%d")

    for asin, data in results.items():
        has_price = data.get("current_price") is not None

        if has_price:
            # Product is available — remove from tracking
            tracking.pop(asin, None)
        else:
            # Product is unavailable — update tracking
            if asin in tracking:
                tracking[asin]["consecutive_days"] += 1
                tracking[asin]["last_checked"] = today
            else:
                tracking[asin] = {
                    "first_unavailable": today,
                    "last_checked": today,
                    "consecutive_days": 1,
                }

    return tracking


def get_skippable_asins(tracking: dict) -> set:
    """Return ASINs that should be skipped (unavailable for too many consecutive days)."""
    return {
        asin for asin, info in tracking.items()
        if info.get("consecutive_days", 0) >= config.UNAVAILABLE_SKIP_AFTER_DAYS
    }


def save_checkpoint(results: dict, remaining_asins: list[str]):
    """Save checkpoint with partial results and remaining ASINs."""
    checkpoint = {
        "saved_at": datetime.now().isoformat(),
        "results": results,
        "remaining_asins": remaining_asins,
    }
    from utils import atomic_json_write
    atomic_json_write(config.CHECKPOINT_FILE, checkpoint)


def load_checkpoint() -> Optional[dict]:
    """Load checkpoint if it exists and is less than 6 hours old."""
    if not config.CHECKPOINT_FILE.exists():
        return None

    with open(config.CHECKPOINT_FILE, "r", encoding="utf-8") as f:
        checkpoint = json.load(f)

    saved_at = datetime.fromisoformat(checkpoint["saved_at"])
    if datetime.now() - saved_at > timedelta(hours=6):
        print("Checkpoint found but too old (>6 hours), starting fresh")
        clear_checkpoint()
        return None

    return checkpoint


def clear_checkpoint():
    """Remove checkpoint file after successful completion."""
    if config.CHECKPOINT_FILE.exists():
        config.CHECKPOINT_FILE.unlink()


def main():
    parser = argparse.ArgumentParser(description="Check prices and find deals")
    parser.add_argument("--limit", type=int, help="Limit number of products to check")
    parser.add_argument("--asin", type=str, help="Check a specific ASIN")
    parser.add_argument("--random", type=int, metavar="N",
                        help="Randomly sample N products (weighted toward unfeatured)")
    parser.add_argument("--full-scan", action="store_true",
                        help="Check all products including long-unavailable ones")
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
    elif args.random:
        # Weighted random sampling: 70% unfeatured, 30% featured
        featured = load_featured_history()
        all_asins = list(catalog.keys())
        unfeatured_asins = [a for a in all_asins if a not in featured]
        featured_asins = [a for a in all_asins if a in featured]

        n_unfeatured = min(int(args.random * 0.7), len(unfeatured_asins))
        n_featured = min(args.random - n_unfeatured, len(featured_asins))

        # If not enough unfeatured, take more featured
        if n_unfeatured + n_featured < args.random:
            n_featured = min(args.random - n_unfeatured, len(featured_asins))

        sampled = random.sample(unfeatured_asins, n_unfeatured) if n_unfeatured > 0 else []
        sampled += random.sample(featured_asins, n_featured) if n_featured > 0 else []
        random.shuffle(sampled)
        asins = sampled

        print(f"Random sample: {n_unfeatured} unfeatured + {n_featured} featured = {len(asins)} products")
    else:
        asins = list(catalog.keys())
        if args.limit:
            asins = asins[:args.limit]

    # Filter out long-unavailable products (unless --full-scan or --asin)
    skipped_count = 0
    if not args.asin and not args.full_scan:
        tracking = load_unavailable_tracking()
        skippable = get_skippable_asins(tracking)
        if skippable:
            before = len(asins)
            asins = [a for a in asins if a not in skippable]
            skipped_count = before - len(asins)
            print(f"Skipping {skipped_count} long-unavailable products "
                  f"({skipped_count + len(asins)} total, {len(asins)} to check)")

    # Check prices
    results = check_products(asins, catalog)

    # Save results
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = config.PROJECT_ROOT / output_path

    deals = save_deals(results, output_path)

    # Update unavailable tracking
    tracking = load_unavailable_tracking()
    tracking = update_unavailable_tracking(tracking, results)
    save_unavailable_tracking(tracking)
    skippable_after = len(get_skippable_asins(tracking))
    print(f"Unavailable tracking: {len(tracking)} products tracked, "
          f"{skippable_after} will be skipped next run")

    # Clear checkpoint on successful completion
    clear_checkpoint()

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
