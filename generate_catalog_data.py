#!/usr/bin/env python3
"""
Generate catalog-data.json for the public catalog page.

Merges products.json (metadata for ~2,900 products) with deals.json
(nightly Keepa prices for ~1,450 products) into a compact JSON file
that powers the catalog frontend.

Usage:
    python generate_catalog_data.py
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config
from generate_report import shorten_title, calculate_issue_number


def load_products() -> dict:
    """Load products.json catalog."""
    with open(config.CATALOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_deals() -> dict:
    """Load deals.json with nightly Keepa prices."""
    deals_file = config.CATALOG_DIR / "deals.json"
    if not deals_file.exists():
        return {}
    with open(deals_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("deals", {})


def get_source_info(product: dict) -> tuple[str, str, str]:
    """Extract source label, URL, and first_featured date from product issues.

    Returns (source_label, source_url, first_featured_date).
    Prioritizes Recomendo over Cool Tools.
    """
    issues = product.get("issues", [])
    if not issues:
        return "", "", product.get("first_featured", "")

    recomendo = [i for i in issues if i.get("source") != "cooltools"]
    cooltools = [i for i in issues if i.get("source") == "cooltools"]

    if recomendo:
        issue = recomendo[0]
        issue_num = calculate_issue_number(issue.get("date", ""))
        label = f"Recomendo #{issue_num}" if issue_num else "Recomendo"
        return label, issue.get("url", ""), product.get("first_featured", "")

    if cooltools:
        issue = cooltools[0]
        return "Cool Tools", issue.get("url", ""), product.get("first_featured", "")

    return "", "", product.get("first_featured", "")


def compute_pct_off(price: float | None, list_price: float | None,
                    avg_90: float | None, high_90: float | None) -> int | None:
    """Compute percentage off for display.

    Uses list_price (MSRP) or 90-day average. Does NOT use 90-day high
    because brief 3rd-party price spikes produce bogus discounts.
    Returns None if no meaningful discount.
    """
    if not price or price <= 0:
        return None

    # Prefer list price (MSRP)
    if list_price and list_price > price:
        pct = ((list_price - price) / list_price) * 100
        if pct >= 5:
            return round(pct)

    # Fall back to 90-day average
    if avg_90 and avg_90 > price:
        pct = ((avg_90 - price) / avg_90) * 100
        if pct >= 5:
            return round(pct)

    return None


def _is_product_title(title: str) -> bool:
    """Check if a title looks like an actual product name vs an article/episode title.

    Cool Tools articles have titles like "Norm Chan, Editor of Tested.com" or
    "What's in My Bag? — Nelson Dellis" which aren't product names.
    """
    import re
    t = title.strip()

    # Article patterns to reject
    article_patterns = [
        r"(?i)^what'?s (in )?(my|their) bag",
        r"(?i)^who uses",
        r"(?i), .*?(editor|creator|founder|co-?founder|chief|photographer|"
        r"designer|writer|author|director|professor|artist|architect|"
        r"engineer|filmmaker|musician|producer|journalist|blogger|"
        r"entrepreneur|ceo|cto|cfo)\b",
    ]
    for pat in article_patterns:
        if re.search(pat, t):
            return False

    # Too short and vague (e.g. "4 ounces", "FZ 300")
    if len(t) < 5:
        return False

    return True


def build_catalog_entry(asin: str, product: dict, deal: dict | None) -> dict | None:
    """Build a single catalog entry from product + deal data.

    Returns None if the product doesn't have minimum required data (title).
    """
    # Prefer Keepa's Amazon title (actual product name) over products.json title
    # (which may be an article/episode title for Cool Tools entries)
    keepa_title = deal.get("title") if deal else None
    catalog_title = product.get("title")

    if keepa_title:
        # We have a real Amazon product title from Keepa
        title = keepa_title
    elif catalog_title and _is_product_title(catalog_title):
        # Catalog title looks like a real product name
        title = catalog_title
    else:
        # Title is an article/episode name — skip this product
        return None

    # Image: prefer deal (Keepa fetched) over product
    image = (deal.get("image_url") if deal else None) or product.get("image_url", "")

    # Price data from deals.json (nightly Keepa)
    price = deal.get("current_price") if deal else None
    list_price = deal.get("list_price") if deal else None
    avg_90 = deal.get("avg_90_day") if deal else None
    high_90 = deal.get("high_90_day") if deal else None
    low_90 = deal.get("low_90_day") if deal else None
    rating = deal.get("rating") if deal else None
    review_count = deal.get("review_count") if deal else None
    score = deal.get("deal_score", 0) if deal else 0
    is_deal = deal.get("is_deal", False) if deal else False
    price_source = deal.get("price_source") if deal else None

    pct_off = compute_pct_off(price, list_price, avg_90, high_90)

    # Source info
    source, source_url, featured = get_source_info(product)

    # Affiliate URL
    affiliate_url = product.get("affiliate_url", "")
    if isinstance(affiliate_url, dict):
        code = affiliate_url.get("code", "")
        domain = affiliate_url.get("domain", "geni.us")
        affiliate_url = f"https://{domain}/{code}" if code else ""

    # Short title: only run shorten_title on Keepa titles (long Amazon names).
    # Catalog-only titles are already short/human-written.
    short_title = shorten_title(title) if keepa_title else title

    entry = {
        "asin": asin,
        "title": short_title,
        "full_title": title,
        "image": image,
    }

    # Only include price fields when available (saves JSON size)
    if price is not None:
        entry["price"] = round(price, 2)
    if list_price is not None:
        entry["list_price"] = round(list_price, 2)
    if pct_off is not None:
        entry["pct_off"] = pct_off
    if avg_90 is not None:
        entry["avg_90"] = round(avg_90, 2)
    if high_90 is not None:
        entry["high_90"] = round(high_90, 2)
    if low_90 is not None:
        entry["low_90"] = round(low_90, 2)
    if rating is not None:
        entry["rating"] = round(rating, 1)
    if review_count is not None:
        entry["reviews"] = review_count
    if score > 0:
        entry["score"] = score
    if is_deal:
        entry["is_deal"] = True
    if price_source and "prime" in price_source:
        entry["prime"] = True

    benefit = product.get("benefit_description", "")
    if benefit:
        entry["benefit"] = benefit

    if affiliate_url:
        entry["url"] = affiliate_url
    if source:
        entry["source"] = source
    if source_url:
        entry["source_url"] = source_url
    if featured:
        entry["featured"] = featured

    # Click count from GeniusLink (if imported)
    clicks = product.get("click_count")
    if clicks:
        entry["clicks"] = clicks

    return entry


def generate_catalog_data():
    """Generate public/catalog-data.json from products.json + deals.json."""
    products = load_products()
    deals = load_deals()

    print(f"Loaded {len(products)} products, {len(deals)} deals")

    entries = []
    skipped = 0

    for asin, product in products.items():
        deal = deals.get(asin)
        entry = build_catalog_entry(asin, product, deal)
        if entry:
            entries.append(entry)
        else:
            skipped += 1

    # Sort: deals first (by score desc), then non-deals (by featured date desc)
    def sort_key(e):
        if e.get("is_deal"):
            return (0, -e.get("score", 0), e.get("featured", ""))
        return (1, 0, e.get("featured", ""))

    entries.sort(key=sort_key)

    deal_count = sum(1 for e in entries if e.get("is_deal"))
    priced_count = sum(1 for e in entries if "price" in e)

    catalog = {
        "generated_at": datetime.now().isoformat(),
        "total": len(entries),
        "deals": deal_count,
        "priced": priced_count,
        "products": entries,
    }

    # Write output
    output_path = config.PROJECT_ROOT / "public" / "catalog-data.json"
    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, separators=(",", ":"))

    size_kb = output_path.stat().st_size / 1024
    print(f"Generated {output_path}")
    print(f"  {len(entries)} products ({skipped} skipped)")
    print(f"  {deal_count} active deals, {priced_count} with prices")
    print(f"  File size: {size_kb:.0f} KB")


if __name__ == "__main__":
    generate_catalog_data()
