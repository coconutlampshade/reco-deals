#!/usr/bin/env python3
"""
Find substitutes for unavailable Amazon products using PA API GetVariations.

Identifies products that Keepa confirmed as unavailable (no current price,
not a rate-limit error) and looks for available variations (different color,
size, etc.) to replace them in the catalog.

Usage:
    python find_substitutes.py --dry-run   # Preview substitutions
    python find_substitutes.py             # Apply substitutions to products.json
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))
import config
from pa_api import get_variations, extract_price_info


def load_deals() -> dict:
    """Load deals.json from last Keepa run."""
    deals_file = config.CATALOG_DIR / "deals.json"
    if not deals_file.exists():
        print(f"Error: {deals_file} not found. Run check_deals.py first.")
        sys.exit(1)
    with open(deals_file, "r", encoding="utf-8") as f:
        return json.load(f)


def load_products() -> dict:
    """Load products.json catalog."""
    if not config.CATALOG_FILE.exists():
        print(f"Error: {config.CATALOG_FILE} not found.")
        sys.exit(1)
    with open(config.CATALOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def find_unavailable_asins(deals_data: dict) -> list[str]:
    """
    Identify ASINs that Keepa confirmed as unavailable.

    These are products in all_results that have no current price and
    no error (i.e., Keepa returned data but the product has no price —
    meaning it's genuinely unavailable, not a rate-limit failure).
    """
    all_results = deals_data.get("all_results", {})
    unavailable = []

    for asin, data in all_results.items():
        if data.get("current_price") is not None:
            continue
        # Skip products where the API call itself failed (e.g., HTTP 429)
        # These have short error strings without "No current price"
        error = data.get("error", "")
        if error and "No current price" not in error:
            continue
        # No current price = genuinely unavailable
        unavailable.append(asin)

    return unavailable


def pick_best_variation(items: list[dict]) -> dict | None:
    """
    Pick the best available variation from PA API results.

    Criteria: must be available with a price. Prefer highest review count,
    then highest rating, then lowest price.
    """
    candidates = []
    for item in items:
        info = extract_price_info(item)
        if info.get("current_price") is None:
            continue
        if info.get("availability") and info["availability"] not in ("Now", ""):
            # "Now" means available; skip others like "OutOfStock"
            # But empty string also means available in some cases
            pass

        candidates.append({
            "asin": item.get("ASIN"),
            "title": info.get("title"),
            "price": info["current_price"],
            "review_count": info.get("review_count") or 0,
            "star_rating": float(info.get("star_rating") or 0),
            "image_url": info.get("image_url"),
            "detail_page_url": info.get("detail_page_url"),
        })

    if not candidates:
        return None

    # Sort: highest reviews first, then highest rating, then lowest price
    candidates.sort(key=lambda c: (-c["review_count"], -c["star_rating"], c["price"]))
    return candidates[0]


def find_substitutes(unavailable_asins: list[str], products: dict,
                     dry_run: bool = False) -> list[dict]:
    """
    Find substitute variations for unavailable ASINs.

    Returns list of substitution records.
    """
    substitutions = []
    total = len(unavailable_asins)

    print(f"\nChecking {total} unavailable products for variations...")

    for idx, asin in enumerate(unavailable_asins, 1):
        product = products.get(asin, {})
        title = product.get("title", asin)[:60]
        print(f"\n[{idx}/{total}] {asin} — {title}")

        try:
            response = get_variations(asin)
        except Exception as e:
            error_msg = str(e)
            # Common: product has no variations
            if "ItemNotFound" in error_msg or "InvalidParameterValue" in error_msg:
                print(f"  No variations available")
            else:
                print(f"  Error: {error_msg[:100]}")
            continue

        # Extract variation items
        items = []
        if "VariationsResult" in response:
            items = response["VariationsResult"].get("Items", [])

        if not items:
            print(f"  No variation items returned")
            continue

        # Filter out the original ASIN itself
        other_items = [item for item in items if item.get("ASIN") != asin]
        if not other_items:
            print(f"  Only found the original ASIN, no alternatives")
            continue

        best = pick_best_variation(other_items)
        if not best:
            print(f"  No available variations with prices")
            continue

        sub = {
            "original_asin": asin,
            "original_title": product.get("title"),
            "new_asin": best["asin"],
            "new_title": best["title"],
            "new_price": best["price"],
            "new_reviews": best["review_count"],
            "new_rating": best["star_rating"],
            "new_image_url": best["image_url"],
            "found_at": datetime.now().isoformat(),
        }
        substitutions.append(sub)

        action = "WOULD SUBSTITUTE" if dry_run else "SUBSTITUTING"
        print(f"  {action}: {best['asin']} — ${best['price']:.2f} — {best['title'][:50]}")

        # PA API rate limit: 1 request/sec
        if idx < total:
            time.sleep(1)

    return substitutions


def apply_substitutions(products: dict, substitutions: list[dict]) -> int:
    """
    Apply substitutions to products.json in-place.

    Swaps the ASIN key, updates amazon_url, keeps original metadata
    (issues, affiliate_url, benefit, first_featured).
    Adds original_asin field for tracking.

    Returns count of applied substitutions.
    """
    applied = 0
    for sub in substitutions:
        old_asin = sub["original_asin"]
        new_asin = sub["new_asin"]

        if old_asin not in products:
            print(f"  Warning: {old_asin} no longer in products, skipping")
            continue
        if new_asin in products:
            print(f"  Warning: {new_asin} already in products, skipping")
            continue

        # Copy existing product data
        product = products.pop(old_asin)

        # Update fields
        product["original_asin"] = old_asin
        product["amazon_url"] = f"https://www.amazon.com/dp/{new_asin}"
        if sub.get("new_title"):
            product["title"] = sub["new_title"]

        # Insert under new ASIN
        products[new_asin] = product
        applied += 1

    return applied


def main():
    parser = argparse.ArgumentParser(description="Find substitutes for unavailable products")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview substitutions without applying")
    args = parser.parse_args()

    # Load data
    products = load_products()
    deals_data = load_deals()
    print(f"Loaded {len(products)} products, {len(deals_data.get('all_results', {}))} deal results")

    # Find unavailable ASINs
    unavailable = find_unavailable_asins(deals_data)
    # Only check ASINs that are still in our catalog
    unavailable = [a for a in unavailable if a in products]
    print(f"Found {len(unavailable)} unavailable products in catalog")

    if not unavailable:
        print("No unavailable products to substitute.")
        return

    # Find substitutes
    substitutions = find_substitutes(unavailable, products, dry_run=args.dry_run)

    print(f"\n{'='*50}")
    print(f"Found {len(substitutions)} substitutions for {len(unavailable)} unavailable products")

    if not substitutions:
        print("No substitutions found.")
        return

    # Save substitutions log
    subs_file = config.CATALOG_DIR / "substitutions.json"
    existing_subs = []
    if subs_file.exists():
        with open(subs_file, "r", encoding="utf-8") as f:
            existing_subs = json.load(f)

    from utils import atomic_json_write

    if args.dry_run:
        print(f"\nDry run — no changes applied.")
        print(f"Would substitute {len(substitutions)} products:")
        for sub in substitutions:
            print(f"  {sub['original_asin']} → {sub['new_asin']} (${sub['new_price']:.2f})")
        return

    # Apply substitutions
    applied = apply_substitutions(products, substitutions)
    print(f"\nApplied {applied} substitutions to products.json")

    # Save updated products
    atomic_json_write(config.CATALOG_FILE, products)
    print(f"Saved updated products.json ({len(products)} products)")

    # Append to substitutions log
    all_subs = existing_subs + substitutions
    atomic_json_write(subs_file, all_subs)
    print(f"Saved substitutions log ({len(all_subs)} total entries)")


if __name__ == "__main__":
    main()
