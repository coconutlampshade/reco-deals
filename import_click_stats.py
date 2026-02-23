#!/usr/bin/env python3
"""
Import GeniusLink click statistics into products.json.

One-time script that reads GeniusLink JSON exports, matches products
by short URL code or Amazon ASIN, and adds TotalClicks as click_count.

Usage:
    python import_click_stats.py
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config

# GeniusLink export directory
GENIUSLINK_DIR = Path.home() / "Desktop/claude-code-apps/cooltools/links"

# Pattern to extract ASIN from Amazon URLs
ASIN_PATTERN = re.compile(r'/(?:dp|gp/product)/([A-Z0-9]{10})')


def extract_asin(url: str) -> str | None:
    """Extract ASIN from an Amazon product URL."""
    if not url:
        return None
    match = ASIN_PATTERN.search(url)
    return match.group(1) if match else None


def load_geniuslink_data() -> dict:
    """Load all GeniusLink exports, aggregate clicks by ASIN and short code.

    Returns dict with:
        by_asin: {ASIN: total_clicks}
        by_code: {short_code: total_clicks}
    """
    by_asin = {}
    by_code = {}

    if not GENIUSLINK_DIR.exists():
        print(f"GeniusLink directory not found: {GENIUSLINK_DIR}")
        return {"by_asin": by_asin, "by_code": by_code}

    json_files = sorted(GENIUSLINK_DIR.glob("genius_links_*.json"))
    print(f"Found {len(json_files)} GeniusLink export files")

    for filepath in json_files:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"  Skipping {filepath.name}: {e}")
            continue

        results = data.get("Results", [])
        print(f"  {filepath.name}: {len(results)} links")

        for entry in results:
            clicks = entry.get("TotalClicks", 0)
            if clicks <= 0:
                continue

            # Map by short code
            code = entry.get("ShortUrlCode", "")
            if code:
                by_code[code] = by_code.get(code, 0) + clicks

            # Map by ASIN
            product_url = entry.get("ProductUrl", "")
            asin = extract_asin(product_url)
            if asin:
                by_asin[asin] = by_asin.get(asin, 0) + clicks

    return {"by_asin": by_asin, "by_code": by_code}


def extract_code_from_affiliate_url(url) -> str | None:
    """Extract the short code from a geni.us URL."""
    if not url:
        return None
    if isinstance(url, dict):
        return url.get("code")
    if isinstance(url, str) and "geni.us/" in url:
        # e.g. https://geni.us/abc123
        parts = url.rstrip("/").split("/")
        return parts[-1] if parts else None
    return None


def import_clicks():
    """Import GeniusLink click stats into products.json."""
    # Load GeniusLink data
    gl_data = load_geniuslink_data()
    by_asin = gl_data["by_asin"]
    by_code = gl_data["by_code"]
    print(f"\nAggregated: {len(by_asin)} ASINs, {len(by_code)} short codes")

    # Load products
    with open(config.CATALOG_FILE, "r", encoding="utf-8") as f:
        products = json.load(f)
    print(f"Loaded {len(products)} products")

    # Match and update
    matched = 0
    for asin, product in products.items():
        clicks = 0

        # Try ASIN match first
        if asin in by_asin:
            clicks = by_asin[asin]

        # Also try affiliate URL short code
        code = extract_code_from_affiliate_url(product.get("affiliate_url"))
        if code and code in by_code:
            clicks = max(clicks, by_code[code])

        if clicks > 0:
            product["click_count"] = clicks
            matched += 1

    print(f"Matched {matched}/{len(products)} products with click data")

    # Show top 10 by clicks
    top = sorted(
        [(a, p.get("click_count", 0), p.get("title", "")) for a, p in products.items()],
        key=lambda x: x[1], reverse=True
    )[:10]
    print("\nTop 10 by clicks:")
    for asin, clicks, title in top:
        print(f"  {clicks:>6,} clicks: {title[:60]}")

    # Save
    from utils import atomic_json_write
    atomic_json_write(config.CATALOG_FILE, products)
    print(f"\nSaved {config.CATALOG_FILE}")


if __name__ == "__main__":
    import_clicks()
