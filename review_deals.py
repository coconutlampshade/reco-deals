#!/usr/bin/env python3
"""
Interactive deal review interface.

Opens a local web page showing top deals from Keepa.
User selects which deals to include, then confirms to generate newsletter.

Usage:
    python review_deals.py                    # Review top deals (with PA API verification)
    python review_deals.py --cached           # Use cached Keepa data only (no PA API)
    python review_deals.py --top 100          # Review top 100 deals
    python review_deals.py --fresh 200        # Fresh Keepa check on 200 random products
"""

import argparse
import json
import math
import random
import subprocess
import webbrowser
import threading
import time
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs
import sys
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))
import config
from pa_api import get_prices_for_asins
from generate_report import (
    load_deals, filter_and_sort_deals, load_featured_history,
    COOLDOWN_DAYS, get_media_category, calculate_issue_number,
    shorten_title,
)

# URL pattern to GeniusLink group name mapping
URL_TO_GROUP = {
    "recomendo.substack.com": "Recomendo",
    "kk.org/cooltools": "Recomendo",
    "bookfreak.substack.com": "Book Freak",
    "booksthatbelongonpaper.substack.com": "Books-on-Paper",
    "nomadico.substack.com": "Nomadico",
    "toolsforpossibilities.substack.com": "Possibilities-Tools",
    "garstips.substack.com": "Tips Tools Shoptales",
    "whatsinmynow.substack.com": "Whats in my NOW",
}


def get_affiliate_group(deal: dict) -> str:
    """Determine the GeniusLink group name based on product's source article URL."""
    issues = deal.get("issues", [])
    if not issues:
        return "Recomendo"

    first_url = issues[0].get("url", "")
    for pattern, group_name in URL_TO_GROUP.items():
        if pattern in first_url:
            return group_name

    return "Recomendo"

# Anthropic client for benefit generation
try:
    import anthropic
    ANTHROPIC_CLIENT = anthropic.Anthropic()
except Exception:
    ANTHROPIC_CLIENT = None

# Server state
selected_asins = []
server_should_stop = False
live_prices = {}


def load_full_catalog() -> dict:
    """Load the full product catalog."""
    catalog_file = config.CATALOG_DIR / "products.json"
    with open(catalog_file, "r", encoding="utf-8") as f:
        return json.load(f)


def save_catalog(catalog: dict):
    """Save the product catalog."""
    from utils import atomic_json_write
    catalog_file = config.CATALOG_DIR / "products.json"
    atomic_json_write(catalog_file, catalog)


def fetch_article_html(url: str) -> str:
    """Fetch article HTML with proper headers."""
    import requests

    headers = {
        "User-Agent": config.SHORTLINK_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"    Warning: Failed to fetch article: {e}")
        return ""


def extract_product_context(html: str, asin: str, product_title: str) -> str:
    """Extract text around the Amazon product link from article HTML."""
    import re
    from html.parser import HTMLParser

    if not html:
        return ""

    # Simple HTML to text conversion
    class HTMLTextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text_parts = []
            self.in_script = False
            self.in_style = False

        def handle_starttag(self, tag, attrs):
            if tag in ('script', 'style'):
                self.in_script = True
            elif tag in ('p', 'br', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'):
                self.text_parts.append('\n')

        def handle_endtag(self, tag):
            if tag in ('script', 'style'):
                self.in_script = False
            elif tag in ('p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'):
                self.text_parts.append('\n')

        def handle_data(self, data):
            if not self.in_script:
                self.text_parts.append(data)

        def get_text(self):
            return ''.join(self.text_parts)

    try:
        extractor = HTMLTextExtractor()
        extractor.feed(html)
        text = extractor.get_text()
    except Exception:
        # Fallback: just strip tags
        text = re.sub(r'<[^>]+>', ' ', html)

    # Clean up whitespace
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\n\s*\n', '\n\n', text)

    # Strategy 1: Find ASIN link in HTML (most reliable for Amazon affiliate links)
    link_match = re.search(rf'<a[^>]*href=["\'][^"\']*{asin}[^"\']*["\'][^>]*>([^<]+)</a>', html, re.IGNORECASE)
    link_text = link_match.group(1).strip() if link_match else ""

    best_pos = -1
    if link_text:
        match = re.search(re.escape(link_text[:40]), text, re.IGNORECASE)
        if match:
            best_pos = match.start()

    # Strategy 2: Search plain text for ASIN URLs or product title words
    if best_pos < 0:
        patterns = [
            rf'amazon\.com/dp/{asin}',
            rf'amazon\.com.*?{asin}',
        ]
        if product_title:
            title_words = [w for w in product_title.split()[:4] if len(w) > 3]
            if title_words:
                patterns.append(r'\b' + r'\b.*?\b'.join(re.escape(w) for w in title_words) + r'\b')

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                best_pos = match.start()
                break

    # Extract surrounding context (narrower window to avoid adjacent product bleed)
    if best_pos >= 0:
        start = max(0, best_pos - 300)
        end = min(len(text), best_pos + 400)

        # Try to start/end at sentence boundaries
        if start > 0:
            sentence_start = text.rfind('.', start - 100, start)
            if sentence_start > 0:
                start = sentence_start + 1
        if end < len(text):
            sentence_end = text.find('.', end, end + 100)
            if sentence_end > 0:
                end = sentence_end + 1

        return text[start:end].strip()

    # Fallback: return first ~1000 chars of article body
    # Try to find start of article content
    body_markers = ['<article', '<main', 'class="post"', 'class="content"', '<body']
    for marker in body_markers:
        pos = html.lower().find(marker)
        if pos >= 0:
            return text[:1500].strip()

    return text[:1500].strip()


def generate_benefit_description(asin: str, deal: dict, catalog: dict) -> str:
    """
    Generate a one-sentence benefit description for a product.

    Uses cached description if available, otherwise fetches source article
    and uses Claude API to generate description.

    Returns empty string if generation fails.
    """
    # Check cache first
    if asin in catalog and catalog[asin].get("benefit_description"):
        return catalog[asin]["benefit_description"]

    if not ANTHROPIC_CLIENT:
        print(f"    Warning: Anthropic client not available for {asin}")
        return ""

    # Get source article URL
    issues = deal.get("issues", [])
    if not issues:
        # Try catalog
        if asin in catalog:
            issues = catalog[asin].get("issues", [])

    if not issues:
        print(f"    Warning: No source article for {asin}")
        return ""

    # Prefer Recomendo over Cool Tools
    recomendo_issues = [i for i in issues if i.get("source") != "cooltools"]
    source_issue = recomendo_issues[0] if recomendo_issues else issues[0]
    article_url = source_issue.get("url", "")

    if not article_url:
        return ""

    # Get product title
    product_title = deal.get("live_title") or deal.get("catalog_title") or catalog.get(asin, {}).get("title", "")

    print(f"    Fetching article for {asin}: {product_title[:40]}...")

    # Fetch article HTML
    html = fetch_article_html(article_url)
    if not html:
        return ""

    # Extract context around product mention
    context = extract_product_context(html, asin, product_title)
    if not context:
        print(f"    Warning: Could not extract context for {asin}")
        return ""

    # Collect product features if available
    features = deal.get("product_features") or []

    # Generate benefit description using Claude
    try:
        features_text = ""
        if features:
            top_features = features[:5]
            features_text = "\n\nAmazon product features:\n" + "\n".join(f"- {f}" for f in top_features)

        prompt = f"""Given this excerpt from a product review page, write ONE sentence describing the key benefit of "{product_title}". The page may review multiple products — ONLY describe "{product_title}", ignore any other products mentioned.

Rules:
- Do NOT mention the product name or brand
- Do NOT mention the price
- Start directly with what the product does or why it's useful
- Be specific and concrete

Product: {product_title}
Review excerpt: {context}{features_text}

Write only the benefit sentence, no preamble."""

        response = ANTHROPIC_CLIENT.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )

        benefit = response.content[0].text.strip()

        # Reject non-descriptions (Claude couldn't match the product)
        if benefit.lower().startswith("i cannot") or benefit.lower().startswith("i'm unable"):
            print(f"    Warning: Claude couldn't match product in context")
            return ""

        # Cache the result
        if asin in catalog:
            catalog[asin]["benefit_description"] = benefit
            save_catalog(catalog)

        print(f"    Generated: {benefit[:60]}...")
        return benefit

    except Exception as e:
        print(f"    Warning: Claude API error for {asin}: {e}")
        return ""


def generate_benefits_for_deals(candidates: list, catalog: dict) -> dict:
    """
    Load pre-generated benefit descriptions for a list of deal candidates.

    Benefits are pre-populated in the catalog by generate_all_benefits.py.
    Returns dict mapping ASIN to benefit description.
    """
    benefits = {}

    for asin, deal in candidates:
        if asin in catalog and catalog[asin].get("benefit_description"):
            benefits[asin] = catalog[asin]["benefit_description"]

    print(f"Loaded {len(benefits)}/{len(candidates)} benefit descriptions from catalog")

    return benefits


def check_keepa_prices(asins: list) -> dict:
    """Check current prices via Keepa API.

    Returns dict of asin -> {current_price, avg_price, avg_price_90, min_price,
    max_price, percent_below_avg, savings_dollars, price_source, title}.
    """
    import os
    import requests
    from keepa_utils import (
        parse_keepa_current_price, parse_keepa_stats, calculate_deal_metrics,
        parse_keepa_buybox_price,
    )

    api_key = os.getenv("KEEPA_API_KEY")
    if not api_key:
        raise ValueError("KEEPA_API_KEY not set")

    results = {}
    batch_size = 20  # Keepa limit

    for i in range(0, len(asins), batch_size):
        batch = asins[i:i + batch_size]
        print(f"  Keepa batch {i // batch_size + 1}/{(len(asins) + batch_size - 1) // batch_size}: {len(batch)} products...")

        url = "https://api.keepa.com/product"
        params = {
            "key": api_key,
            "domain": 1,  # Amazon.com
            "asin": ",".join(batch),
            "stats": 90,  # 90-day stats
            "offers": 20,  # Include seller offers for Buy Box price
        }

        from utils import api_request_with_retry

        def _do_request():
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()

        data = api_request_with_retry(_do_request)

        if "products" not in data:
            print(f"    Warning: No products in response")
            continue

        for product in data["products"]:
            asin = product.get("asin")
            if not asin:
                continue

            stats = product.get("stats", {})

            current, price_source = parse_keepa_current_price(product, stats)
            stat_values = parse_keepa_stats(stats, price_source)

            # Try Buy Box winner price (may include Prime-exclusive pricing)
            bb_price, bb_source = parse_keepa_buybox_price(product)
            if bb_price is not None:
                current = bb_price
                price_source = bb_source

            metrics = calculate_deal_metrics(
                current, stat_values["avg_90_day"], stat_values["high_90_day"]
            ) if current else {}

            results[asin] = {
                "current_price": current,
                "avg_price": stat_values["avg_90_day"],
                "avg_price_90": stat_values["avg_90_day"],
                "min_price": stat_values["low_90_day"],
                "max_price": stat_values["high_90_day"],
                "list_price": stat_values.get("list_price"),
                "percent_below_avg": metrics.get("percent_below_avg"),
                "savings_dollars": metrics.get("savings_dollars"),
                "price_source": price_source,
                "title": product.get("title"),
            }

        # Rate limiting - Keepa allows ~20 tokens/minute
        tokens_used = data.get("tokensConsumed", len(batch))
        tokens_left = data.get("tokensLeft", 0)
        print(f"    Tokens: used {tokens_used}, remaining {tokens_left}")

        if tokens_left < 20 and i + batch_size < len(asins):
            wait_time = 60
            print(f"    Rate limit - waiting {wait_time}s...")
            time.sleep(wait_time)

    return results


def fetch_fresh_candidates(sample_size: int = 200, top_n: int = 50) -> list:
    """
    Fresh approach: Sample random products from catalog, check Keepa, then PA API.
    """
    global live_prices

    # Load full catalog
    catalog = load_full_catalog()
    all_asins = list(catalog.keys())
    print(f"Loaded {len(all_asins)} products from catalog")

    # Random sample
    sample_asins = random.sample(all_asins, min(sample_size, len(all_asins)))
    print(f"Selected {len(sample_asins)} random products to check")

    # Check Keepa for current prices
    print(f"\nChecking Keepa for fresh prices...")
    keepa_data = check_keepa_prices(sample_asins)
    print(f"Got Keepa data for {len(keepa_data)} products")

    # Rank ALL products by deal rating (current vs average)
    potential_deals = []
    for asin, kdata in keepa_data.items():
        current = kdata.get("current_price")
        avg = kdata.get("avg_price_90") or kdata.get("avg_price")

        if not current:
            continue

        # Calculate savings (negative means price is above average)
        if avg and avg > 0:
            savings_pct = ((avg - current) / avg) * 100
        else:
            savings_pct = 0

        potential_deals.append({
            "asin": asin,
            "keepa_current": current,
            "keepa_avg": avg or current,
            "keepa_savings_pct": savings_pct,
            "catalog": catalog.get(asin, {}),
        })

    # Sort by savings (best deals first)
    potential_deals.sort(key=lambda x: x["keepa_savings_pct"], reverse=True)
    deals_count = sum(1 for d in potential_deals if d["keepa_savings_pct"] > 0)
    print(f"Found {len(potential_deals)} products ({deals_count} are deals, rest ranked by price vs avg)")

    # Take all candidates and enrich with PA API
    candidates_to_verify = potential_deals[:top_n]
    asins_to_verify = [d["asin"] for d in candidates_to_verify]

    print(f"\nEnriching {len(asins_to_verify)} products with PA API...")
    pa_prices = get_prices_for_asins(asins_to_verify)
    live_prices.update(pa_prices)

    # Build final results - include ALL products, use Keepa data as fallback
    result = []
    for deal_info in candidates_to_verify:
        asin = deal_info["asin"]
        catalog_entry = deal_info["catalog"]
        pa_info = pa_prices.get(asin, {})

        # Use PA API price if available, otherwise Keepa price
        current_price = pa_info.get("current_price") or deal_info["keepa_current"]

        deal = {
            "asin": asin,
            "live_price": current_price,
            "live_title": pa_info.get("title") or catalog_entry.get("title", asin),
            "live_image": pa_info.get("image_url") or catalog_entry.get("image_url", ""),
            "review_count": pa_info.get("review_count", 0),
            "star_rating": pa_info.get("star_rating", 0),
            "product_group": pa_info.get("product_group", ""),
            "binding": pa_info.get("binding", ""),
            "issues": catalog_entry.get("issues", []),
            "keepa_avg": deal_info["keepa_avg"],
            "affiliate_url": catalog_entry.get("affiliate_url"),
            "amazon_url": catalog_entry.get("amazon_url"),
        }

        # Calculate savings - prefer PA API list price, fall back to Keepa avg
        if pa_info.get("list_price") and pa_info["list_price"] > current_price:
            deal["live_list_price"] = pa_info["list_price"]
            savings_pct = ((pa_info["list_price"] - current_price) / pa_info["list_price"]) * 100
        else:
            deal["live_list_price"] = deal_info["keepa_avg"]
            savings_pct = deal_info["keepa_savings_pct"]

        deal["savings_percent"] = savings_pct
        result.append((asin, deal))

    result.sort(key=lambda x: x[1].get("savings_percent", 0), reverse=True)
    print(f"Final: {len(result)} products ranked by deal quality")

    return result


def fetch_candidates_cached(top_n: int = 50) -> list:
    """Fetch top deal candidates using cached Keepa data only (no PA API)."""
    global live_prices

    data = load_deals()
    deals_dict = data.get("deals", {})
    print(f"Loaded {len(deals_dict)} products from Keepa cache")

    # Filter to only deals (is_deal=true means it passed Keepa's deal criteria)
    candidates = filter_and_sort_deals(deals_dict, top_n=top_n * 2)
    print(f"Found {len(candidates)} deal candidates")

    result = []
    for asin, deal in candidates:
        # Skip if no price data
        if not deal.get("current_price"):
            continue

        # Only include if it's actually a deal (percent_below_avg > 15%)
        pct_below_avg = deal.get("percent_below_avg") or 0
        if pct_below_avg < 15:
            continue

        # Use Keepa cached data directly
        deal["live_price"] = deal["current_price"]
        deal["live_title"] = deal.get("title") or deal.get("catalog_title", asin)
        deal["live_image"] = deal.get("image_url", "")
        deal["review_count"] = deal.get("review_count", 0)
        deal["star_rating"] = deal.get("rating", 0)
        deal["product_group"] = ""
        deal["binding"] = ""

        # Use Keepa's 90-day average as the comparison price (NOT high_90_day)
        avg_price = deal.get("avg_90_day") or deal.get("avg_price", 0)
        if avg_price and avg_price > deal["current_price"]:
            deal["live_list_price"] = avg_price
            deal["savings_percent"] = pct_below_avg
        else:
            # No valid comparison price - skip this item
            continue

        # Store in live_prices for later use
        live_prices[asin] = {
            "current_price": deal["live_price"],
            "title": deal["live_title"],
            "image_url": deal["live_image"],
        }

        result.append((asin, deal))

        if len(result) >= top_n:
            break

    # Sort by savings percentage
    result.sort(key=lambda x: x[1].get("savings_percent", 0), reverse=True)
    print(f"Returning {len(result)} deals for review")

    return result[:top_n]


def fetch_candidates(top_n: int = 50) -> list:
    """Fetch top deal candidates with live prices from cached deals.json."""
    global live_prices

    data = load_deals()
    deals_dict = data.get("deals", {})
    print(f"Loaded {len(deals_dict)} deals from Keepa cache")

    # Get more candidates than needed to account for unavailable items
    candidates = filter_and_sort_deals(deals_dict, top_n=top_n * 5)

    # Fetch live prices in batches until we have enough
    result = []
    batch_size = 50
    fetched = 0

    while len(result) < top_n and fetched < len(candidates):
        batch = candidates[fetched:fetched + batch_size]
        asins = [asin for asin, _ in batch]
        print(f"Fetching live prices for batch {fetched // batch_size + 1} ({len(asins)} products)...")

        batch_prices = get_prices_for_asins(asins)
        live_prices.update(batch_prices)

        for asin, deal in batch:
            if asin not in batch_prices:
                continue
            price_info = batch_prices[asin]

            # Only require current price (item is available)
            if not price_info.get("current_price"):
                continue

            # Merge live price data into deal
            deal["live_price"] = price_info["current_price"]
            deal["live_title"] = price_info.get("title") or deal.get("catalog_title", "")
            deal["live_image"] = price_info.get("image_url") or deal.get("image_url", "")
            deal["review_count"] = price_info.get("review_count", 0)
            deal["star_rating"] = price_info.get("star_rating", 0)
            deal["product_group"] = price_info.get("product_group", "")
            deal["binding"] = price_info.get("binding", "")

            # Use PA API list_price if available, otherwise use Keepa's average price
            if price_info.get("list_price") and price_info["list_price"] > price_info["current_price"]:
                deal["live_list_price"] = price_info["list_price"]
                savings_pct = ((price_info["list_price"] - price_info["current_price"])
                              / price_info["list_price"]) * 100
            elif deal.get("avg_price") and deal["avg_price"] > price_info["current_price"]:
                # Fall back to Keepa's average price
                deal["live_list_price"] = deal["avg_price"]
                savings_pct = ((deal["avg_price"] - price_info["current_price"])
                              / deal["avg_price"]) * 100
            else:
                # No reliable discount data - skip this item
                # (percent_below_high alone doesn't mean it's a deal)
                continue

            deal["savings_percent"] = savings_pct
            result.append((asin, deal))

            if len(result) >= top_n:
                break

        fetched += batch_size
        print(f"  Found {len(result)} valid deals so far")

    # Sort by savings percentage
    result.sort(key=lambda x: x[1].get("savings_percent", 0), reverse=True)

    return result[:top_n]


def generate_review_html(candidates: list, benefits: dict = None) -> str:
    """Generate the review HTML page."""
    if benefits is None:
        benefits = {}
    history = load_featured_history()
    catalog = load_full_catalog()
    today = datetime.now()

    html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Review Deals - Recomendo Deals</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }
        h1 { color: #333; margin-bottom: 5px; }
        .subtitle { color: #666; margin-bottom: 20px; }
        .controls {
            position: sticky;
            top: 0;
            background: #4384F3;
            color: white;
            padding: 15px 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            z-index: 100;
            box-shadow: 0 2px 10px rgba(0,0,0,0.2);
        }
        .controls button {
            background: white;
            color: #4384F3;
            border: none;
            padding: 12px 30px;
            font-size: 16px;
            font-weight: 600;
            border-radius: 5px;
            cursor: pointer;
        }
        .controls button:hover { background: #f0f0f0; }
        .controls button:disabled { background: #ccc; color: #666; cursor: not-allowed; }
        .selected-count { font-size: 18px; font-weight: 600; }
        .filters {
            background: white;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
        }
        .filters label { display: flex; align-items: center; gap: 5px; cursor: pointer; }
        .deal {
            background: white;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 15px;
            display: flex;
            gap: 20px;
            align-items: flex-start;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            transition: all 0.2s;
        }
        .deal:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.15); }
        .deal.selected { background: #e8f4e8; border: 2px solid #27ae60; }
        .deal.cooldown { opacity: 0.6; }
        .deal.dragging { opacity: 0.5; transform: scale(1.02); }
        .deal.drag-over { border-top: 3px solid #4384F3; }
        .drag-handle {
            cursor: grab;
            color: #ccc;
            font-size: 20px;
            padding: 5px;
            flex-shrink: 0;
            user-select: none;
        }
        .drag-handle:hover { color: #999; }
        .drag-handle:active { cursor: grabbing; }
        .deal-checkbox {
            width: 24px;
            height: 24px;
            cursor: pointer;
            flex-shrink: 0;
        }
        .deal-image {
            width: 100px;
            height: 100px;
            object-fit: contain;
            border-radius: 4px;
            flex-shrink: 0;
        }
        .deal-content { flex: 1; min-width: 0; }
        .deal-title {
            font-size: 16px;
            font-weight: 600;
            margin-bottom: 8px;
            color: #333;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .title-edit {
            flex: 1;
            font-size: 16px;
            font-weight: 600;
            color: #333;
            border: 1px solid transparent;
            border-radius: 4px;
            padding: 4px 8px;
            background: transparent;
            min-width: 0;
        }
        .title-edit:hover { border-color: #ddd; background: #fafafa; }
        .title-edit:focus { border-color: #4384F3; background: white; outline: none; }
        .title-link {
            color: #999;
            text-decoration: none;
            font-size: 14px;
            flex-shrink: 0;
        }
        .title-link:hover { color: #4384F3; }
        .deal-price {
            font-size: 20px;
            font-weight: 700;
            color: #27ae60;
        }
        .deal-price .was {
            font-size: 14px;
            color: #999;
            text-decoration: line-through;
            font-weight: normal;
            margin-left: 8px;
        }
        .deal-savings {
            display: inline-block;
            background: #27ae60;
            color: white;
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 12px;
            font-weight: 600;
            margin-left: 10px;
        }
        .deal-meta {
            font-size: 13px;
            color: #666;
            margin-top: 8px;
        }
        .deal-meta a { color: #4384F3; }
        .benefits-edit {
            width: 100%;
            font-size: 13px;
            color: #666;
            border: 1px solid #ddd;
            border-radius: 4px;
            padding: 8px;
            margin-top: 8px;
            resize: vertical;
            min-height: 60px;
            font-family: inherit;
            line-height: 1.4;
        }
        .benefits-edit:focus { border-color: #4384F3; outline: none; }
        .benefits-edit::placeholder { color: #999; }
        .affiliate-row {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-top: 8px;
            font-size: 12px;
        }
        .affiliate-label {
            color: #999;
            flex-shrink: 0;
        }
        .affiliate-edit {
            flex: 1;
            font-size: 12px;
            font-family: monospace;
            color: #666;
            border: 1px solid #ddd;
            border-radius: 4px;
            padding: 4px 8px;
            min-width: 0;
        }
        .affiliate-edit:hover { border-color: #ccc; }
        .affiliate-edit:focus { border-color: #4384F3; outline: none; }
        .affiliate-group {
            background: #f0f0f0;
            color: #666;
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 11px;
            flex-shrink: 0;
        }
        .deal-tags { margin-top: 8px; }
        .tag {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 11px;
            margin-right: 5px;
        }
        .tag.media { background: #f0e6ff; color: #7c3aed; }
        .tag.cooldown { background: #fee2e2; color: #dc2626; }
        .tag.popular { background: #dbeafe; color: #2563eb; }
        .tag.rating { background: #fef3c7; color: #d97706; }
        .loading {
            text-align: center;
            padding: 50px;
            font-size: 18px;
            color: #666;
        }
    </style>
</head>
<body>
    <h1>Review Deals</h1>
    <p class="subtitle">Select which deals to include in today's newsletter</p>

    <div class="controls">
        <span class="selected-count"><span id="count">0</span> deals selected</span>
        <div>
            <button onclick="selectAll()">Select All</button>
            <button onclick="selectNone()">Select None</button>
            <button id="confirmBtn" onclick="confirmSelection()" disabled>Confirm & Send</button>
        </div>
    </div>

    <div class="filters">
        <label><input type="checkbox" id="hideCooldown" onchange="applyFilters()"> Hide recently featured</label>
        <label><input type="checkbox" id="hideMedia" onchange="applyFilters()"> Hide books/movies/TV</label>
    </div>

    <div id="deals">
"""

    for asin, deal in candidates:
        full_title = deal.get("live_title") or deal.get("catalog_title") or asin
        title = shorten_title(full_title)
        image = deal.get("live_image") or ""
        price = deal.get("live_price", 0)
        list_price = deal.get("live_list_price", 0)
        savings_pct = deal.get("savings_percent", 0)
        review_count = deal.get("review_count", 0)
        star_rating = deal.get("star_rating", 0)

        # Check cooldown
        last_featured = history.get(asin)
        in_cooldown = False
        days_since = None
        if last_featured:
            last_date = datetime.fromisoformat(last_featured)
            days_since = (today - last_date).days
            in_cooldown = days_since < COOLDOWN_DAYS

        # Check media type
        media_type = get_media_category(deal)

        # Get source info
        issues = deal.get("issues", [])
        source_html = ""
        if issues:
            recomendo = [i for i in issues if i.get("source") != "cooltools"]
            cooltools = [i for i in issues if i.get("source") == "cooltools"]
            if recomendo:
                issue = recomendo[0]
                issue_num = calculate_issue_number(issue.get("date", ""))
                if issue_num:
                    source_html = f'<a href="{issue.get("url", "")}" target="_blank">Recomendo #{issue_num}</a>'
            elif cooltools:
                issue = cooltools[0]
                source_html = f'<a href="{issue.get("url", "")}" target="_blank">Cool Tools</a>'

        # Build tags
        tags_html = ""
        if media_type:
            tags_html += f'<span class="tag media">{media_type.title()}</span>'
        if in_cooldown:
            tags_html += f'<span class="tag cooldown">Featured {days_since}d ago</span>'
        if review_count and review_count > 1000:
            tags_html += f'<span class="tag popular">{review_count:,} reviews</span>'
        if star_rating and star_rating >= 4.5:
            tags_html += f'<span class="tag rating">★ {star_rating}</span>'

        amazon_url = f"https://amazon.com/dp/{asin}"

        # Get affiliate URL and group (check deal first, then catalog)
        catalog_entry = catalog.get(asin, {})
        affiliate_url = deal.get("affiliate_url") or catalog_entry.get("affiliate_url") or ""
        # Handle case where affiliate_url is a dict (GeniusLink API response)
        if isinstance(affiliate_url, dict):
            # Extract URL from GeniusLink response: https://geni.us/{code}
            code = affiliate_url.get("code", "")
            domain = affiliate_url.get("domain", "geni.us")
            if code:
                affiliate_url = f"https://{domain}/{code}"
            else:
                affiliate_url = ""
        elif not isinstance(affiliate_url, str):
            affiliate_url = ""
        # Use catalog for group since it has the issues array
        affiliate_group = get_affiliate_group(catalog_entry if catalog_entry else deal)

        classes = "deal"
        if in_cooldown:
            classes += " cooldown"
        data_attrs = f'data-asin="{asin}" data-media="{media_type or ""}" data-cooldown="{str(in_cooldown).lower()}"'

        # Escape title for HTML attribute
        title_escaped = title.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
        affiliate_escaped = affiliate_url.replace('"', '&quot;')

        html += f"""
        <div class="{classes}" {data_attrs} draggable="true">
            <div class="drag-handle" title="Drag to reorder">⋮⋮</div>
            <input type="checkbox" class="deal-checkbox" value="{asin}" onchange="updateCount()">
            <img src="{image}" alt="" class="deal-image" onerror="this.style.display='none'">
            <div class="deal-content">
                <div class="deal-title">
                    <input type="text" class="title-edit" data-asin="{asin}" value="{title_escaped}">
                    <a href="{amazon_url}" target="_blank" class="title-link" title="View on Amazon">↗</a>
                </div>
                <div class="deal-price">
                    ${price:.2f}
                    <span class="was">${list_price:.2f}</span>
                    <span class="deal-savings">{savings_pct:.0f}% off</span>
                </div>
                <div class="deal-tags">{tags_html}</div>
                <div class="deal-meta">{source_html}</div>
                <div class="affiliate-row">
                    <span class="affiliate-label">Link:</span>
                    <input type="text" class="affiliate-edit" data-asin="{asin}" value="{affiliate_escaped}" placeholder="geni.us or amzn.to URL">
                    <span class="affiliate-group">{affiliate_group}</span>
                </div>
                <textarea class="benefits-edit" data-asin="{asin}" placeholder="One sentence describing the product's benefits from the original review...">{benefits.get(asin, "")}</textarea>
            </div>
        </div>
"""

    html += """
    </div>

    <script>
        function updateCount() {
            const checked = document.querySelectorAll('.deal-checkbox:checked').length;
            document.getElementById('count').textContent = checked;
            document.getElementById('confirmBtn').disabled = checked === 0;

            // Update selected styling
            document.querySelectorAll('.deal').forEach(deal => {
                const cb = deal.querySelector('.deal-checkbox');
                deal.classList.toggle('selected', cb.checked);
            });
        }

        function selectAll() {
            document.querySelectorAll('.deal:not([style*="display: none"]) .deal-checkbox').forEach(cb => cb.checked = true);
            updateCount();
        }

        function selectNone() {
            document.querySelectorAll('.deal-checkbox').forEach(cb => cb.checked = false);
            updateCount();
        }

        function applyFilters() {
            const hideCooldown = document.getElementById('hideCooldown').checked;
            const hideMedia = document.getElementById('hideMedia').checked;

            document.querySelectorAll('.deal').forEach(deal => {
                const isCooldown = deal.dataset.cooldown === 'true';
                const isMedia = deal.dataset.media !== '';

                let hide = false;
                if (hideCooldown && isCooldown) hide = true;
                if (hideMedia && isMedia) hide = true;

                deal.style.display = hide ? 'none' : 'flex';
            });
        }

        // Drag and drop reordering
        let draggedElement = null;

        document.addEventListener('dragstart', function(e) {
            if (e.target.classList.contains('deal')) {
                draggedElement = e.target;
                e.target.classList.add('dragging');
                e.dataTransfer.effectAllowed = 'move';
            }
        });

        document.addEventListener('dragend', function(e) {
            if (e.target.classList.contains('deal')) {
                e.target.classList.remove('dragging');
                document.querySelectorAll('.deal').forEach(d => d.classList.remove('drag-over'));
                draggedElement = null;
            }
        });

        document.addEventListener('dragover', function(e) {
            e.preventDefault();
            const deal = e.target.closest('.deal');
            if (deal && deal !== draggedElement) {
                document.querySelectorAll('.deal').forEach(d => d.classList.remove('drag-over'));
                deal.classList.add('drag-over');
            }
        });

        document.addEventListener('drop', function(e) {
            e.preventDefault();
            const targetDeal = e.target.closest('.deal');
            if (targetDeal && draggedElement && targetDeal !== draggedElement) {
                const container = document.getElementById('deals');
                const deals = Array.from(container.querySelectorAll('.deal'));
                const draggedIdx = deals.indexOf(draggedElement);
                const targetIdx = deals.indexOf(targetDeal);

                if (draggedIdx < targetIdx) {
                    targetDeal.parentNode.insertBefore(draggedElement, targetDeal.nextSibling);
                } else {
                    targetDeal.parentNode.insertBefore(draggedElement, targetDeal);
                }
            }
            document.querySelectorAll('.deal').forEach(d => d.classList.remove('drag-over'));
        });

        function showSuccessModal(campaignUrl) {
            const modal = document.createElement('div');
            modal.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:1000;';
            modal.innerHTML = `
                <div style="background:white;padding:30px;border-radius:12px;max-width:500px;text-align:center;box-shadow:0 4px 20px rgba(0,0,0,0.3);">
                    <div style="font-size:48px;margin-bottom:15px;">✅</div>
                    <h2 style="margin:0 0 15px;color:#333;">Newsletter Created!</h2>
                    <p style="color:#666;margin-bottom:20px;">Your Mailchimp draft is ready for review.</p>
                    <a href="${campaignUrl}" target="_blank" style="display:inline-block;background:#4384F3;color:white;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:600;margin-bottom:15px;">Open in Mailchimp →</a>
                    <br><br>
                    <button onclick="window.close()" style="background:#eee;border:none;padding:10px 20px;border-radius:6px;cursor:pointer;color:#666;">Close Window</button>
                </div>
            `;
            document.body.appendChild(modal);
        }

        function confirmSelection() {
            // Get deals in current DOM order (respects drag reordering)
            const allDeals = Array.from(document.querySelectorAll('#deals .deal'));
            const selectedDeals = allDeals.filter(deal => deal.querySelector('.deal-checkbox').checked);
            const selected = selectedDeals.map(deal => deal.querySelector('.deal-checkbox').value);

            if (selected.length === 0) {
                alert('Please select at least one deal');
                return;
            }

            // Collect custom titles, benefits, and affiliate URLs for selected items
            const titles = {};
            const benefits = {};
            const affiliateUrls = {};
            selectedDeals.forEach(deal => {
                const asin = deal.querySelector('.deal-checkbox').value;
                const titleInput = deal.querySelector('.title-edit');
                const benefitsInput = deal.querySelector('.benefits-edit');
                const affiliateInput = deal.querySelector('.affiliate-edit');
                if (titleInput) {
                    titles[asin] = titleInput.value;
                }
                if (benefitsInput && benefitsInput.value.trim()) {
                    benefits[asin] = benefitsInput.value.trim();
                }
                if (affiliateInput && affiliateInput.value.trim()) {
                    affiliateUrls[asin] = affiliateInput.value.trim();
                }
            });

            document.getElementById('confirmBtn').disabled = true;
            document.getElementById('confirmBtn').textContent = 'Sending...';

            fetch('/confirm', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({asins: selected, titles: titles, benefits: benefits, affiliateUrls: affiliateUrls})
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    showSuccessModal(data.campaign_url);
                } else {
                    alert('Error: ' + data.error);
                    document.getElementById('confirmBtn').disabled = false;
                    document.getElementById('confirmBtn').textContent = 'Confirm & Send';
                }
            })
            .catch(err => {
                alert('Error: ' + err);
                document.getElementById('confirmBtn').disabled = false;
                document.getElementById('confirmBtn').textContent = 'Confirm & Send';
            });
        }

        // Auto-select non-cooldown, non-media items up to 10
        window.onload = function() {
            const deals = document.querySelectorAll('.deal');
            let selected = 0;
            deals.forEach(deal => {
                if (selected >= 10) return;
                const isCooldown = deal.dataset.cooldown === 'true';
                const isMedia = deal.dataset.media !== '';
                if (!isCooldown && !isMedia) {
                    deal.querySelector('.deal-checkbox').checked = true;
                    selected++;
                }
            });
            // If we don't have 10, add some media items
            if (selected < 10) {
                deals.forEach(deal => {
                    if (selected >= 10) return;
                    const isCooldown = deal.dataset.cooldown === 'true';
                    const cb = deal.querySelector('.deal-checkbox');
                    if (!isCooldown && !cb.checked) {
                        cb.checked = true;
                        selected++;
                    }
                });
            }
            updateCount();
        };
    </script>
</body>
</html>
"""
    return html


class ReviewHandler(BaseHTTPRequestHandler):
    """HTTP handler for the review interface."""

    html_content = ""
    candidates = []

    def log_message(self, format, *args):
        pass  # Suppress HTTP logs

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(self.html_content.encode())

    def do_POST(self):
        global selected_asins, server_should_stop

        if self.path == "/confirm":
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data)
            selected_asins = data.get("asins", [])
            custom_titles = data.get("titles", {})
            custom_benefits = data.get("benefits", {})
            custom_affiliate_urls = data.get("affiliateUrls", {})

            # Generate newsletter with selected items
            try:
                result = generate_and_send(selected_asins, self.candidates, custom_titles, custom_benefits, custom_affiliate_urls)
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())
                server_should_stop = True
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())


def update_archive_index(public_dir):
    """Update the archive index.html with links to all newsletters."""
    from pathlib import Path

    # Find all newsletter files
    newsletters = sorted(public_dir.glob("newsletter-*.html"), reverse=True)

    # Generate archive HTML
    archive_html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Recomendo Deals Archive</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f0f0f0;
            color: #363737;
        }
        .container {
            background: white;
            border-radius: 8px;
            padding: 30px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .logo {
            text-align: center;
            margin-bottom: 20px;
        }
        .logo img {
            max-width: 280px;
        }
        h1 {
            text-align: center;
            color: #363737;
            margin-bottom: 10px;
        }
        .subtitle {
            text-align: center;
            color: #666;
            margin-bottom: 30px;
        }
        .newsletter-list {
            list-style: none;
            padding: 0;
        }
        .newsletter-list li {
            padding: 15px;
            border-bottom: 1px solid #e0e0e0;
        }
        .newsletter-list li:last-child {
            border-bottom: none;
        }
        .newsletter-list a {
            color: #4384F3;
            text-decoration: none;
            font-size: 18px;
            font-weight: 500;
        }
        .newsletter-list a:hover {
            text-decoration: underline;
        }
        .date {
            color: #666;
            font-size: 14px;
            margin-top: 5px;
        }
        .subscribe {
            text-align: center;
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #e0e0e0;
        }
        .subscribe a {
            color: #4384F3;
            text-decoration: none;
        }
        .subscribe a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">
            <img src="https://kk.org/cooltools/files/2026/01/recomendo-deals.png" alt="Recomendo Deals">
        </div>
        <h1>Newsletter Archive</h1>
        <p class="subtitle">Past issues with live Amazon prices</p>
        <ul class="newsletter-list">
"""

    for newsletter in newsletters:
        # Extract date from filename (newsletter-YYYY-MM-DD.html)
        date_str = newsletter.stem.replace("newsletter-", "")
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            formatted_date = date_obj.strftime("%B %d, %Y")
        except ValueError:
            formatted_date = date_str

        archive_html += f"""            <li>
                <a href="{newsletter.stem}">{formatted_date}</a>
                <div class="date">View deals with live prices</div>
            </li>
"""

    archive_html += """        </ul>
        <div class="subscribe">
            <p><a href="/">← Browse the full catalog</a></p>
            <p>Get deals delivered to your inbox: <a href="https://mailchi.mp/cool-tools/recomendo-deals">Subscribe free</a></p>
            <p><a href="feed.xml">RSS Feed</a></p>
        </div>
    </div>
</body>
</html>
"""

    # Write archive index (archive.html — catalog homepage is index.html)
    archive_path = public_dir / "archive.html"
    with open(archive_path, "w") as f:
        f.write(archive_html)
    print(f"Archive index updated: {archive_path}")


def update_rss_feed(public_dir):
    """Generate RSS feed from newsletter archive."""
    import re
    from pathlib import Path
    from email.utils import formatdate
    from time import mktime

    VERCEL_URL = "https://reco-deals.vercel.app"

    # Find all newsletter files
    newsletters = sorted(public_dir.glob("newsletter-*.html"), reverse=True)

    rss_items = []
    for newsletter in newsletters:
        # Extract date from filename
        date_str = newsletter.stem.replace("newsletter-", "")
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            formatted_date = date_obj.strftime("%B %d, %Y")
            pub_date = formatdate(mktime(date_obj.timetuple()))
        except ValueError:
            formatted_date = date_str
            pub_date = ""

        # Read full HTML content
        html_content = newsletter.read_text()

        # Split by deal blocks - each deal starts with <div class="deal"
        parts = re.split(r'(<div class="deal"[^>]*>)', html_content)
        deal_blocks = []
        for i, part in enumerate(parts):
            if part.startswith('<div class="deal"'):
                # Combine the opening tag with the content that follows
                if i + 1 < len(parts):
                    # Find where this deal ends (before next deal or footer)
                    content = parts[i + 1]
                    # Take content up to the next major section
                    end_match = re.search(r'<div class="footer">', content)
                    if end_match:
                        content = content[:end_match.start()]
                    deal_blocks.append(part + content)

        if deal_blocks:
            # Build description with full deal HTML
            description = '<div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif;">'
            for block in deal_blocks:
                # Extract key info from each deal
                title_match = re.search(r'<div class="deal-title">\s*<a[^>]*href="([^"]+)"[^>]*>([^<]+)</a>', block)
                price_match = re.search(r'<div class="deal-price"[^>]*>([^<]+)</div>', block)
                indicator_match = re.search(r'<div class="deal-indicator">([^<]+)</div>', block)
                meta_match = re.search(r'<div class="deal-meta">(.+?)</div>', block, re.DOTALL)
                img_match = re.search(r'<img[^>]*src="([^"]+)"', block)

                if title_match:
                    url, title = title_match.groups()
                    price = price_match.group(1) if price_match else ""
                    indicator = indicator_match.group(1) if indicator_match else ""
                    meta = meta_match.group(1).strip() if meta_match else ""
                    img = img_match.group(1) if img_match else ""

                    description += f'''
<div style="margin-bottom: 20px; padding-bottom: 20px; border-bottom: 1px solid #eee;">
  <table><tr>
    <td style="vertical-align: top; padding-right: 15px;">{"<img src='" + img + "' width='80' height='80' style='border-radius: 8px;'>" if img else ""}</td>
    <td style="vertical-align: top;">
      <a href="{url}" style="font-weight: 600; color: #363737; text-decoration: none; font-size: 16px;">{title}</a><br>
      <span style="color: #27ae60; font-weight: 700; font-size: 18px;">{price}</span>
      {f'<span style="color: #27ae60; font-size: 14px;"> · {indicator}</span>' if indicator else ''}
      <div style="font-size: 13px; color: #666; margin-top: 5px;">{meta}</div>
    </td>
  </tr></table>
</div>'''
            description += '</div>'
        else:
            description = "View deals with live prices"

        link = f"{VERCEL_URL}/{newsletter.name}"

        rss_items.append(f"""    <item>
      <title>Recomendo Deals - {formatted_date}</title>
      <link>{link}</link>
      <guid>{link}</guid>
      <pubDate>{pub_date}</pubDate>
      <description><![CDATA[{description}]]></description>
    </item>""")

    # Build RSS feed
    rss_feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Recomendo Deals</title>
    <link>{VERCEL_URL}</link>
    <description>Daily deals on products recommended by Recomendo and Cool Tools</description>
    <language>en-us</language>
    <atom:link href="{VERCEL_URL}/feed.xml" rel="self" type="application/rss+xml"/>
{chr(10).join(rss_items)}
  </channel>
</rss>
"""

    # Write RSS feed
    feed_path = public_dir / "feed.xml"
    with open(feed_path, "w") as f:
        f.write(rss_feed)
    print(f"RSS feed updated: {feed_path}")


def generate_and_send(asins: list, candidates: list, custom_titles: dict = None, custom_benefits: dict = None, custom_affiliate_urls: dict = None, unclassified_ad: dict = None) -> dict:
    """Generate newsletter with selected ASINs and send to Mailchimp."""
    from generate_report import (
        generate_html_report, update_featured_history, LOGO_URL,
        load_catalog_benefits
    )
    from mailchimp_send import create_campaign
    import os

    # Web version URL (configurable via environment)
    VERCEL_URL = os.getenv("VERCEL_URL", "https://reco-deals.vercel.app")

    if custom_titles is None:
        custom_titles = {}
    if custom_benefits is None:
        custom_benefits = {}
    if custom_affiliate_urls is None:
        custom_affiliate_urls = {}

    # Filter candidates to selected ASINs
    selected = [(asin, deal) for asin, deal in candidates if asin in asins]

    # Order by the selection order (preserve user's implicit ranking by savings)
    asin_order = {asin: i for i, asin in enumerate(asins)}
    selected.sort(key=lambda x: asin_order.get(x[0], 999))

    print(f"\nGenerating newsletter with {len(selected)} selected deals...")

    # Build live_prices dict for report generation
    prices = {}
    for asin, deal in selected:
        # Use custom title if provided, otherwise fall back to live_title
        # Track if title is custom so generate_report won't shorten it again
        title_is_custom = asin in custom_titles and custom_titles[asin]
        title = custom_titles.get(asin) or deal.get("live_title")

        # Use custom affiliate URL if provided, otherwise fall back to catalog
        # Priority: custom_affiliate_url > affiliate_url (geni.us, amzn.to) > amazon_url with tag > construct with recomendos-20
        original_url = custom_affiliate_urls.get(asin) or deal.get("affiliate_url")
        # Handle case where affiliate_url is a dict (GeniusLink API response)
        if isinstance(original_url, dict):
            code = original_url.get("code", "")
            domain = original_url.get("domain", "geni.us")
            if code:
                original_url = f"https://{domain}/{code}"
            else:
                original_url = None
        if not original_url:
            amazon_url = deal.get("amazon_url", "")
            # Check if amazon_url already has an affiliate tag
            if "tag=" in amazon_url:
                original_url = amazon_url
            else:
                # Add recomendos-20 tag
                original_url = f"https://www.amazon.com/dp/{asin}?tag=recomendos-20"

        prices[asin] = {
            "current_price": deal.get("live_price"),
            "list_price": deal.get("live_list_price"),
            "price_source": deal.get("price_source"),
            "title": title,
            "title_is_custom": title_is_custom,
            "image_url": deal.get("live_image"),
            "affiliate_url": original_url,
            "benefits": custom_benefits.get(asin),
            "review_count": deal.get("review_count"),
            "star_rating": deal.get("star_rating"),
            "product_group": deal.get("product_group"),
            "binding": deal.get("binding"),
        }

    # Load catalog benefits for web version
    catalog_benefits = load_catalog_benefits()

    # Date string for file naming
    date_str = datetime.now().strftime('%Y-%m-%d')
    web_filename = f"newsletter-{date_str}.html"
    web_url = f"{VERCEL_URL}/{web_filename}"

    # Generate web version (dynamic prices via JavaScript)
    web_html = generate_html_report(
        selected, "Recomendo Deals", prices, datetime.now(),
        web_mode=True, catalog_benefits=catalog_benefits,
        unclassified_ad=unclassified_ad
    )

    # Save web version to public/ for Vercel
    public_dir = config.PROJECT_ROOT / "public"
    public_dir.mkdir(exist_ok=True)
    web_path = public_dir / web_filename
    with open(web_path, "w") as f:
        f.write(web_html)
    print(f"Web version saved to: {web_path}")

    # Update archive index and RSS feed
    update_archive_index(public_dir)
    update_rss_feed(public_dir)

    # Generate email version (static prices, with link to web version)
    html = generate_html_report(
        selected, "Recomendo Deals", prices, datetime.now(),
        web_mode=False, web_url=web_url, catalog_benefits=catalog_benefits,
        unclassified_ad=unclassified_ad
    )

    # Save email report
    report_path = config.PROJECT_ROOT / "reports" / f"deals-{date_str}.html"
    report_path.parent.mkdir(exist_ok=True)
    with open(report_path, "w") as f:
        f.write(html)
    print(f"Email report saved to: {report_path}")

    # Update featured history
    update_featured_history(asins)
    print(f"Updated featured history for {len(asins)} items")

    # Save any edited benefit descriptions and affiliate URLs back to catalog
    if custom_benefits or custom_affiliate_urls:
        catalog = load_full_catalog()
        benefits_updated = 0
        urls_updated = 0
        for asin, benefit in custom_benefits.items():
            if asin in catalog and benefit:
                catalog[asin]["benefit_description"] = benefit
                benefits_updated += 1
        for asin, url in custom_affiliate_urls.items():
            if asin in catalog and url:
                catalog[asin]["affiliate_url"] = url
                urls_updated += 1
        if benefits_updated > 0 or urls_updated > 0:
            save_catalog(catalog)
            if benefits_updated > 0:
                print(f"Updated {benefits_updated} benefit descriptions in catalog")
            if urls_updated > 0:
                print(f"Updated {urls_updated} affiliate URLs in catalog")

    # Generate preview text: "Product1 $XX • Product2 $XX • Product3 XX% off • N deals total"
    preview_parts = []
    for asin, deal in selected[:3]:  # First 3 deals for preview
        title = prices[asin].get("title", "")
        short_title = shorten_title(title)

        current = prices[asin].get("current_price")
        list_price = prices[asin].get("list_price")

        if current and list_price and list_price > current:
            savings_pct = int(((list_price - current) / list_price) * 100)
            # Alternate between showing price and percentage
            if len(preview_parts) % 2 == 0:
                preview_parts.append(f"{short_title} ${current:.0f}")
            else:
                preview_parts.append(f"{short_title} {savings_pct}% off")
        elif current:
            preview_parts.append(f"{short_title} ${current:.0f}")

    preview_parts.append(f"{len(selected)} deals total")
    preview_text = " • ".join(preview_parts)
    print(f"Preview text: {preview_text}")

    # Send to Mailchimp
    print("Creating Mailchimp campaign...")
    subject = f"Recomendo Deals - {datetime.now().strftime('%B %d, %Y')}"
    campaign = create_campaign(subject, html, preview_text)
    campaign_url = campaign.get("web_id", "")
    if campaign_url:
        campaign_url = f"https://admin.mailchimp.com/campaigns/edit?id={campaign_url}"

    print(f"Campaign created: {campaign_url}")

    # Save campaign history for analytics
    campaign_history_path = config.PROJECT_ROOT / "catalog" / "campaign_history.json"
    try:
        history = json.loads(campaign_history_path.read_text()) if campaign_history_path.exists() else []
        history.append({
            "campaign_id": campaign["campaign_id"],
            "web_id": campaign["web_id"],
            "date": datetime.now().strftime("%Y-%m-%d"),
            "subject": subject,
            "deals_count": len(selected),
            "asins": [asin for asin, _ in selected],
            "titles": {asin: custom_titles.get(asin, prices[asin].get("title", "")) for asin, _ in selected},
            "affiliate_urls": {asin: prices[asin].get("affiliate_url", "") for asin, _ in selected},
        })
        campaign_history_path.write_text(json.dumps(history, indent=2))
        print(f"Campaign history saved ({len(history)} campaigns)")
    except Exception as e:
        print(f"Warning: Failed to save campaign history: {e}")

    # Deploy to Vercel and push to git in background so the HTTP response
    # returns immediately and the browser can show the success modal.
    import threading

    def _deploy_and_push():
        print("Deploying to Vercel...")
        try:
            subprocess.run(["vercel", "--prod", "--yes"], check=True, capture_output=True, cwd=str(config.PROJECT_ROOT / "public"))
            print("Deployed to Vercel")
        except subprocess.CalledProcessError as e:
            print(f"Vercel deploy failed: {e}")

        print("Pushing to git...")
        try:
            subprocess.run(["git", "add", "-A"], check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Update deals and featured history\n\nCo-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"],
                check=True, capture_output=True
            )
            subprocess.run(["git", "push"], check=True, capture_output=True)
            print("Pushed to git")
        except subprocess.CalledProcessError as e:
            print(f"Git push failed: {e}")

    threading.Thread(target=_deploy_and_push, daemon=True).start()

    return {
        "success": True,
        "campaign_url": campaign_url,
        "deals_count": len(selected)
    }


def run_server(html: str, candidates: list, port: int = 8765):
    """Run the local review server."""
    global server_should_stop
    server_should_stop = False

    ReviewHandler.html_content = html
    ReviewHandler.candidates = candidates

    server = HTTPServer(("localhost", port), ReviewHandler)
    server.timeout = 1

    print(f"\nReview interface ready at http://localhost:{port}")
    print("Select deals and click 'Confirm & Send' when ready.")
    print("Press Ctrl+C to cancel.\n")

    webbrowser.open(f"http://localhost:{port}")

    while not server_should_stop:
        server.handle_request()

    server.server_close()
    print("\nDone!")


def main():
    parser = argparse.ArgumentParser(description="Review and select deals for newsletter")
    parser.add_argument("--top", type=int, default=100, help="Number of deals to show in review")
    parser.add_argument("--fresh", type=int, help="Fresh Keepa check on N random products (e.g., --fresh 200)")
    parser.add_argument("--thorough", action="store_true", help="Check ALL products in catalog (takes ~2 hours)")
    parser.add_argument("--cached", action="store_true", help="Use cached Keepa data only (skip PA API verification)")
    parser.add_argument("--port", type=int, default=8765, help="Local server port")
    args = parser.parse_args()

    if args.thorough:
        print("Running THOROUGH check on entire catalog...")
        print("This will check all 3500+ products via Keepa (takes ~2 hours)")
        # Run check_deals.py first to refresh deals.json
        import subprocess
        subprocess.run(["python3", "check_deals.py"], check=True)
        print("\nNow fetching top deals from fresh data...")
        candidates = fetch_candidates(args.top)
    elif args.fresh:
        print(f"Running fresh Keepa check on {args.fresh} random products...")
        # Show all products from fresh check, ranked by deal quality
        candidates = fetch_fresh_candidates(sample_size=args.fresh, top_n=args.fresh)
    elif args.cached:
        print("Using cached Keepa data (skipping PA API verification)...")
        candidates = fetch_candidates_cached(args.top)
    else:
        print("Fetching deal candidates from cache...")
        candidates = fetch_candidates(args.top)

    if not candidates:
        print("No deals found!")
        return

    print(f"Found {len(candidates)} deals to review")

    # Load catalog and generate benefit descriptions
    catalog = load_full_catalog()
    benefits = generate_benefits_for_deals(candidates, catalog)

    html = generate_review_html(candidates, benefits)
    run_server(html, candidates, args.port)


if __name__ == "__main__":
    main()
