#!/usr/bin/env python3
"""
Convert Amazon URLs to GeniusLink (geni.us) short URLs.

This is a one-time script to populate the affiliate_url field for products
that don't already have one. Products with existing affiliate_url values
(amzn.to or geni.us) are preserved.

Usage:
    python convert_to_geniuslinks.py                 # Process all products without affiliate_url
    python convert_to_geniuslinks.py --limit 100    # Process at most 100 products
    python convert_to_geniuslinks.py --dry-run      # Show what would be processed
"""

import argparse
import json
import time
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

import config

# GeniusLink API credentials
GENIUSLINK_API_KEY = "b2c6d35528ba40eb84a10a49a1cb016f"
GENIUSLINK_API_SECRET = "d8465bb3509d48bdb12938674dfe74b4"
GENIUSLINK_BASE_URL = "https://api.geni.us"

# URL pattern to GeniusLink group name mapping
URL_TO_GROUP = {
    "recomendo.substack.com": "Recomendo",
    "kk.org/cooltools": "Recomendo",  # Use Recomendo for Cool Tools
    "bookfreak.substack.com": "Book Freak",
    "booksthatbelongonpaper.substack.com": "Books-on-Paper",
    "nomadico.substack.com": "Nomadico",
    "toolsforpossibilities.substack.com": "Possibilities-Tools",
    "garstips.substack.com": "Tips Tools Shoptales",
    "whatsinmynow.substack.com": "Whats in my NOW",
}

DEFAULT_GROUP = "Recomendo"


def load_catalog() -> dict:
    """Load the full product catalog."""
    with open(config.CATALOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_catalog(catalog: dict):
    """Save the product catalog."""
    with open(config.CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2)


def get_geniuslink_headers() -> dict:
    """Get headers for GeniusLink API requests."""
    return {
        "X-Api-Key": GENIUSLINK_API_KEY,
        "X-Api-Secret": GENIUSLINK_API_SECRET,
        "Content-Type": "application/json",
    }


def fetch_group_ids() -> dict:
    """Fetch group name to ID mapping from GeniusLink API."""
    url = f"{GENIUSLINK_BASE_URL}/v1/groups/list"

    try:
        resp = requests.get(url, headers=get_geniuslink_headers(), timeout=30)
        resp.raise_for_status()
        data = resp.json()

        # Build name -> id mapping (API uses capitalized keys)
        groups = {}
        for group in data.get("Groups", []):
            name = group.get("Name", "")
            group_id = group.get("Id")
            if name and group_id:
                groups[name] = group_id

        return groups

    except Exception as e:
        print(f"Error fetching groups: {e}")
        return {}


def determine_group_name(product: dict) -> str:
    """Determine the GeniusLink group name based on product's source article URL."""
    issues = product.get("issues", [])
    if not issues:
        return DEFAULT_GROUP

    # Check the first issue's URL against our patterns
    first_url = issues[0].get("url", "")

    for pattern, group_name in URL_TO_GROUP.items():
        if pattern in first_url:
            return group_name

    return DEFAULT_GROUP


def create_geniuslink(amazon_url: str, group_id: int) -> str:
    """Create a GeniusLink short URL for an Amazon URL."""
    url = f"{GENIUSLINK_BASE_URL}/v3/shorturls"

    payload = {
        "url": amazon_url,
        "groupId": group_id,
    }

    try:
        resp = requests.post(
            url,
            headers=get_geniuslink_headers(),
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()

        # The response should contain the short URL
        short_url = data.get("shortUrl") or data.get("url")
        return short_url or ""

    except requests.exceptions.HTTPError as e:
        # Try to get error details from response
        try:
            error_data = e.response.json()
            print(f"    API Error: {error_data}")
        except:
            print(f"    HTTP Error: {e}")
        return ""
    except Exception as e:
        print(f"    Error creating GeniusLink: {e}")
        return ""


def main():
    parser = argparse.ArgumentParser(description="Convert Amazon URLs to GeniusLink short URLs")
    parser.add_argument("--limit", type=int, help="Maximum number of products to process")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed without making changes")
    parser.add_argument("--save-interval", type=int, default=100, help="Save progress every N products (default: 100)")
    args = parser.parse_args()

    print("Loading catalog...")
    catalog = load_catalog()
    total_products = len(catalog)
    print(f"Total products in catalog: {total_products}")

    # Find products without affiliate_url
    products_without_affiliate = [
        (asin, product) for asin, product in catalog.items()
        if not product.get("affiliate_url")
    ]
    products_with_affiliate = total_products - len(products_without_affiliate)
    print(f"Products with existing affiliate_url: {products_with_affiliate} (will be preserved)")
    print(f"Products without affiliate_url: {len(products_without_affiliate)}")

    if args.dry_run:
        print("\nDry run - would process these products:")
        for i, (asin, product) in enumerate(products_without_affiliate[:10]):
            group_name = determine_group_name(product)
            print(f"  {i+1}. {asin}: {product.get('title', 'No title')[:40]} -> {group_name}")
        if len(products_without_affiliate) > 10:
            print(f"  ... and {len(products_without_affiliate) - 10} more")
        return

    # Fetch group IDs from API
    print("\nFetching GeniusLink group IDs...")
    group_ids = fetch_group_ids()
    if not group_ids:
        print("Error: Could not fetch group IDs from GeniusLink API")
        return

    print(f"Found {len(group_ids)} groups:")
    for name, gid in sorted(group_ids.items()):
        print(f"  - {name}: {gid}")

    # Verify all our groups exist
    missing_groups = set()
    for group_name in URL_TO_GROUP.values():
        if group_name not in group_ids:
            missing_groups.add(group_name)
    if DEFAULT_GROUP not in group_ids:
        missing_groups.add(DEFAULT_GROUP)

    if missing_groups:
        print(f"\nWarning: These groups don't exist in GeniusLink: {missing_groups}")
        print("Products mapping to these groups will be skipped.")

    # Limit if requested
    to_process = products_without_affiliate
    if args.limit:
        to_process = to_process[:args.limit]
        print(f"\nProcessing limited to {args.limit} products")

    print(f"\nProcessing {len(to_process)} products...")

    processed = 0
    success = 0
    failed = 0
    skipped = 0

    for i, (asin, product) in enumerate(to_process):
        processed += 1
        title = product.get("title", "No title")[:40]

        # Determine group
        group_name = determine_group_name(product)
        group_id = group_ids.get(group_name)

        if not group_id:
            print(f"[{i+1}/{len(to_process)}] {asin}: Skipping (group '{group_name}' not found)")
            skipped += 1
            continue

        # Get Amazon URL
        amazon_url = product.get("amazon_url", "")
        if not amazon_url:
            # Construct from ASIN
            amazon_url = f"https://www.amazon.com/dp/{asin}"

        print(f"[{i+1}/{len(to_process)}] {asin}: {title}... ({group_name})")

        # Rate limit - be conservative with API
        if i > 0:
            time.sleep(0.3)

        geniuslink = create_geniuslink(amazon_url, group_id)
        if geniuslink:
            catalog[asin]["affiliate_url"] = geniuslink
            success += 1
            print(f"    OK: {geniuslink}")
        else:
            failed += 1
            print(f"    FAILED")

        # Save progress incrementally
        if processed % args.save_interval == 0:
            print(f"\n  Saving progress ({processed} processed)...")
            save_catalog(catalog)
            print(f"  Saved. Success: {success}, Failed: {failed}, Skipped: {skipped}\n")

    # Final save
    print(f"\nSaving final results...")
    save_catalog(catalog)

    print(f"\nDone!")
    print(f"  Processed: {processed}")
    print(f"  Success: {success}")
    print(f"  Failed: {failed}")
    print(f"  Skipped: {skipped}")

    # Show updated stats
    final_with_affiliate = sum(1 for p in catalog.values() if p.get("affiliate_url"))
    print(f"\nProducts with affiliate_url: {final_with_affiliate}/{total_products} ({100*final_with_affiliate/total_products:.1f}%)")


if __name__ == "__main__":
    main()
