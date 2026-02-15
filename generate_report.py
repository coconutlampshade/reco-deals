#!/usr/bin/env python3
"""
Report generation utilities for Recomendo Deals.

This module provides functions for generating HTML reports and managing
deal data. It is used by review_deals.py for the interactive newsletter workflow.
"""

import json
import math
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config
from pa_api import get_prices_for_asins, format_price

# Recomendo Issue #1 approximate start date (for calculating issue numbers)
RECOMENDO_START_DATE = datetime(2016, 2, 14)


def load_deals() -> dict:
    """Load deals from the deals.json file."""
    deals_file = config.CATALOG_DIR / "deals.json"
    if not deals_file.exists():
        print(f"Error: Deals file not found at {deals_file}")
        print("Run check_deals.py first to generate deals.")
        sys.exit(1)

    with open(deals_file, "r", encoding="utf-8") as f:
        return json.load(f)


def shorten_title(title):
    """Create a clean, short product name (3-5 words)."""
    import re
    if not title:
        return "Deal"

    # Remove parenthetical content (model numbers, sizes, etc.)
    title = re.sub(r'\([^)]*\)', '', title)
    # Remove bracketed content
    title = re.sub(r'\[[^\]]*\]', '', title)

    # Remove common filler phrases
    filler = [
        r'\d+\s*sheet\s*capacity', r'jam\s*free', r'heavy\s*duty',
        r'\d+\s*count', r'\d+\s*pack', r'\d+\s*piece', r'\d+\s*ct\b',
        r'\d+"\s*', r'\d+\s*inch', r'\d+\s*"', r'\d+\s*ft\b',
        r'business\s*', r'professional\s*', r'premium\s*',
        r'classic\s*', r'original\s*', r'standard\s*',
        r'non-stick\s*', r'ceramic\s*', r'stainless\s*steel\s*',
        r'america\'s\s*#?\d*\s*favorite\s*', r'canary\s*yellow\s*',
        r'clean\s*removal\s*', r'recyclable\s*', r'portable\s*',
        r'low\s*profile\s*', r'replaces\s*\d+\s*', r'black\s*$',
        r'diy\s*', r'hobby\s*', r'tool\s*painting\s*',
    ]
    for f in filler:
        title = re.sub(f, '', title, flags=re.IGNORECASE)

    # Clean up punctuation
    title = re.sub(r'\s*-\s*$', '', title)  # trailing dash
    title = re.sub(r'\s*,\s*,+', ',', title)  # multiple commas
    title = re.sub(r'\s+', ' ', title)  # multiple spaces
    title = title.strip(' ,-')

    # Words to skip
    skip_words = {'and', 'or', 'the', 'a', 'an', 'in', 'on', 'of', 'for', 'with', 'to'}

    # Product type words we want to keep (ensures name makes sense)
    product_types = {
        'stapler', 'fryer', 'slicer', 'pan', 'trimmer', 'steamer', 'microscope',
        'knife', 'scissors', 'cutter', 'grinder', 'blender', 'mixer', 'cooker',
        'grill', 'toaster', 'maker', 'press', 'opener', 'peeler', 'grater',
        'thermometer', 'scale', 'timer', 'clock', 'light', 'lamp', 'flashlight',
        'charger', 'cable', 'adapter', 'speaker', 'headphones', 'earbuds',
        'bag', 'case', 'pouch', 'wallet', 'holder', 'stand', 'rack', 'organizer',
        'brush', 'comb', 'razor', 'clipper', 'tweezer', 'file',
        'tape', 'glue', 'pen', 'pencil', 'pencils', 'marker', 'notebook', 'planner',
        'tool', 'wrench', 'pliers', 'screwdriver', 'hammer', 'drill',
        'game', 'puzzle', 'toy', 'book', 'guide', 'kit', 'set',
        'pills', 'tablets', 'chewables', 'capsules', 'cream', 'lotion',
        'stripper', 'sealer', 'dispenser', 'sharpener',
        'lock', 'twister', 'grips', 'mat', 'pad', 'bed', 'seat',
        'notes', 'pads', 'plate', 'shelter', 'booth', 'anchor', 'bowl', 'bowls',
        'cubes', 'stick', 'sticks', 'bottle', 'mop', 'sprayer', 'nozzle',
    }

    # Split and remove duplicates while preserving order
    words = []
    seen = set()
    for word in title.replace(',', ' ').replace('-', ' ').split():
        word_lower = word.lower()
        if word_lower not in seen and word_lower not in skip_words and len(word) > 1:
            words.append(word)
            seen.add(word_lower)

    # Find if there's a product type word and ensure we include it
    result_words = []
    found_product_type = False
    for i, word in enumerate(words):
        if len(result_words) < 4:
            result_words.append(word)
            if word.lower() in product_types:
                found_product_type = True
        elif not found_product_type and word.lower() in product_types:
            # Add the product type
            result_words.append(word)
            found_product_type = True
            break

    result = " ".join(result_words[:5] if not found_product_type else result_words)
    return result if result else "Deal"


def load_catalog_benefits() -> dict:
    """Load benefit descriptions from products.json catalog."""
    catalog_file = config.CATALOG_DIR / "products.json"
    if not catalog_file.exists():
        return {}

    with open(catalog_file, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    # Extract benefit_description for each ASIN
    benefits = {}
    for asin, product in catalog.items():
        if product.get("benefit_description"):
            benefits[asin] = product["benefit_description"]
    return benefits


# Logo URL (hosted externally for smaller email size)
LOGO_URL = "https://kk.org/cooltools/files/2026/01/recomendo-deals.png"


def calculate_issue_number(date_str: str) -> int:
    """Calculate Recomendo issue number from date."""
    if not date_str:
        return 0
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        weeks = (d - RECOMENDO_START_DATE).days // 7
        return weeks + 1
    except ValueError:
        return 0


FEATURED_HISTORY_FILE = Path(__file__).parent / "catalog" / "featured_history.json"
COOLDOWN_DAYS = 30
MIN_DEALS = 5  # Minimum number of deals per newsletter


def load_featured_history() -> dict:
    """Load history of when items were last featured."""
    if FEATURED_HISTORY_FILE.exists():
        with open(FEATURED_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_featured_history(history: dict):
    """Save featured history."""
    from utils import atomic_json_write
    atomic_json_write(FEATURED_HISTORY_FILE, history)


def filter_recently_featured(deals: list, cooldown_days: int = COOLDOWN_DAYS) -> list:
    """
    Filter out items that were featured within the cooldown period.

    Args:
        deals: List of (asin, deal_data) tuples
        cooldown_days: Number of days before an item can be featured again

    Returns:
        Filtered list excluding recently featured items
    """
    history = load_featured_history()
    today = datetime.now()
    filtered = []

    for asin, deal in deals:
        last_featured = history.get(asin)
        if last_featured:
            last_date = datetime.fromisoformat(last_featured)
            days_since = (today - last_date).days
            if days_since < cooldown_days:
                continue
        filtered.append((asin, deal))

    return filtered


def update_featured_history(asins: list[str]):
    """Mark items as featured today."""
    history = load_featured_history()
    today = datetime.now().isoformat()

    for asin in asins:
        history[asin] = today

    save_featured_history(history)


def get_media_category(live_price: dict) -> str | None:
    """
    Determine if an item is a book, movie, or TV show.

    Returns: "book", "movie", "tv", or None for other products.
    """
    product_group = (live_price.get("product_group") or "").lower()
    binding = (live_price.get("binding") or "").lower()
    title = (live_price.get("title") or "").lower()

    # Books
    if product_group in ("book", "books", "ebooks", "kindle store"):
        return "book"
    if binding in ("paperback", "hardcover", "kindle edition", "spiral-bound"):
        return "book"

    # Video content (movies and TV)
    if product_group in ("dvd", "movies & tv", "blu-ray", "video"):
        # Distinguish TV from movies by title patterns
        tv_patterns = ["season", "series", "complete", "episode", "collection", "box set"]
        if any(pattern in title for pattern in tv_patterns):
            return "tv"
        return "movie"

    if binding in ("dvd", "blu-ray", "prime video"):
        tv_patterns = ["season", "series", "complete", "episode", "collection", "box set"]
        if any(pattern in title for pattern in tv_patterns):
            return "tv"
        return "movie"

    return None


def limit_media_items(deals: list, live_prices: dict, max_total: int = 1) -> list:
    """
    Limit total books, movies, and TV shows combined.

    Args:
        deals: List of (asin, deal_data) tuples, already sorted by quality
        live_prices: Dict of ASIN -> live price info from PA API
        max_total: Maximum total media items (books + movies + TV combined)

    Returns:
        Filtered list with media limits applied
    """
    media_count = 0
    filtered = []

    for asin, deal in deals:
        live_price = live_prices.get(asin, {})
        category = get_media_category(live_price)

        if category:
            if media_count >= max_total:
                # Skip this item, already have enough media
                continue
            media_count += 1

        filtered.append((asin, deal))

    return filtered


def score_deal(deal: dict) -> float:
    """
    Calculate a deal score for ranking (Keepa data).
    Higher score = better deal.
    """
    score = 0

    # Percent below average (weight: 2x)
    if deal.get("percent_below_avg") and deal["percent_below_avg"] > 0:
        score += deal["percent_below_avg"] * 2

    # Percent below high (weight: 1x)
    if deal.get("percent_below_high") and deal["percent_below_high"] > 0:
        score += deal["percent_below_high"]

    # Near all-time low bonus
    if deal.get("all_time_low") and deal.get("current_price"):
        if deal["current_price"] <= deal["all_time_low"] * 1.05:
            score += 50  # Big bonus for all-time low

    # Dollar savings bonus (for expensive items)
    if deal.get("savings_dollars") and deal["savings_dollars"] > 0:
        score += min(deal["savings_dollars"], 20)  # Cap at 20 points

    return score


def score_live_deal(asin: str, live_prices: dict) -> float:
    """
    Calculate deal score using PA API live price data.
    Higher score = better deal.
    """
    price_info = live_prices.get(asin, {})
    if not price_info.get("list_price") or not price_info.get("current_price"):
        return 0

    # Savings percentage (0-100 scale)
    savings_pct = ((price_info["list_price"] - price_info["current_price"])
                  / price_info["list_price"]) * 100

    # Popularity score based on review count (log scale, 0-30 points)
    review_count = price_info.get("review_count") or 0
    if review_count > 0:
        popularity = min(math.log10(review_count + 1) * 10, 30)
    else:
        popularity = 0

    # Quality bonus for high ratings (0-10 points)
    star_rating = price_info.get("star_rating") or 0
    if star_rating >= 4.5:
        quality = 10
    elif star_rating >= 4.0:
        quality = 5
    else:
        quality = 0

    # Composite: savings is primary, popularity and quality are secondary
    return savings_pct + (popularity * 0.5) + (quality * 0.5)


def filter_and_sort_deals(deals: dict, min_savings: float = 0, top_n: int = None) -> list:
    """
    Filter and sort deals by score.

    Returns list of (asin, deal_data) tuples.
    Includes all products with valid prices (not just strict deals).
    """
    # Include all products with valid prices
    valid_deals = [
        (asin, data) for asin, data in deals.items()
        if data.get("current_price")
    ]

    # Filter by minimum savings
    if min_savings > 0:
        valid_deals = [
            (asin, data) for asin, data in valid_deals
            if (data.get("savings_dollars") or 0) >= min_savings
        ]

    # Sort by score (highest first) - strict deals will rank higher due to better savings
    valid_deals.sort(key=lambda x: score_deal(x[1]), reverse=True)

    # Limit to top N
    if top_n:
        valid_deals = valid_deals[:top_n]

    return valid_deals


def get_buy_link(deal: dict) -> str:
    """Get the best buy link for a deal (prefer affiliate URL)."""
    affiliate_url = deal.get("affiliate_url")
    # Handle affiliate_url being a dict with productUrl key
    if isinstance(affiliate_url, dict):
        return affiliate_url.get("productUrl") or ""
    return affiliate_url or deal.get("amazon_url") or ""


def format_price(price: float) -> str:
    """Format price as currency string."""
    return f"${price:.2f}"


def format_deal_indicator(deal: dict) -> str:
    """
    Format deal indicator without showing static prices.
    Amazon Associates requires dynamic prices only.
    """
    indicators = []

    # Show percentage-based indicators (these describe the deal, not the price)
    if deal.get("percent_below_avg") and deal["percent_below_avg"] >= 15:
        indicators.append(f"{deal['percent_below_avg']:.0f}% below typical price")
    elif deal.get("percent_below_high") and deal["percent_below_high"] >= 25:
        indicators.append(f"{deal['percent_below_high']:.0f}% off recent high")

    # Check for all-time low
    if deal.get("all_time_low") and deal.get("current_price"):
        if deal["current_price"] <= deal["all_time_low"] * 1.05:
            indicators.append("Near all-time low")

    return indicators[0] if indicators else "Deal detected"


def fetch_live_prices(asins: list[str]) -> dict[str, dict]:
    """
    Fetch live prices from Amazon PA API.

    Returns dict of ASIN -> price info with:
        - current_price
        - list_price (if on sale)
        - savings_percent
        - title (from Amazon)
        - image_url
    """
    print(f"Fetching live prices for {len(asins)} products from PA API...")
    try:
        prices = get_prices_for_asins(asins)
        successful = sum(1 for p in prices.values() if p.get("current_price"))
        print(f"  Got prices for {successful}/{len(asins)} products")
        return prices
    except Exception as e:
        print(f"  Warning: PA API error: {e}")
        return {}


def generate_html_report(deals: list, title: str = "Recomendo Deals", live_prices: dict = None, price_timestamp: str = None, web_mode: bool = False, web_url: str = None, catalog_benefits: dict = None, unclassified_ad: dict = None) -> str:
    """
    Generate an HTML email report with Recomendo styling.

    Args:
        deals: List of (asin, deal_data) tuples
        title: Report title
        live_prices: Dict of ASIN -> live price info from PA API
        price_timestamp: Timestamp when prices were fetched from PA API
        web_mode: If True, generate web version with dynamic price fetching
        web_url: URL to the web version (for "View in browser" link in email)
        catalog_benefits: Dict of ASIN -> benefit description from catalog
    """
    if live_prices is None:
        live_prices = {}
    if catalog_benefits is None:
        catalog_benefits = {}
    today = datetime.now().strftime("%B %d, %Y")

    # Format price timestamp for display (time only if same day per Amazon requirements)
    import time
    if price_timestamp is None:
        price_timestamp = datetime.now()
    tz_name = time.strftime("%Z")
    price_time_str = price_timestamp.strftime(f"%H:%M {tz_name}") if isinstance(price_timestamp, datetime) else price_timestamp

    # Recomendo color palette
    # Primary: #4384F3 (bright blue)
    # Text: #363737 (dark charcoal)
    # Background: #ffffff, #f0f0f0
    # Hover: #2b74f1

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            color: #363737;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f0f0f0;
        }}
        .container {{
            background-color: #ffffff;
            border-radius: 8px;
            padding: 30px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .logo {{
            text-align: center;
            margin-bottom: 15px;
        }}
        .logo img {{
            max-width: 320px;
            height: auto;
        }}
        .subtitle {{
            color: #666;
            margin-bottom: 20px;
            font-size: 14px;
            text-align: center;
        }}
        .deal {{
            margin-bottom: 30px;
            padding-bottom: 30px;
            border-bottom: 1px solid #e0e0e0;
            display: flex;
            gap: 20px;
            align-items: flex-start;
        }}
        .deal:last-child {{
            border-bottom: none;
            margin-bottom: 0;
            padding-bottom: 0;
        }}
        .deal-image {{
            flex-shrink: 0;
            width: 100px;
            height: 100px;
        }}
        .deal-image img {{
            width: 100px;
            height: 100px;
            object-fit: contain;
            border-radius: 8px;
        }}
        .deal-content {{
            flex: 1;
            min-width: 0;
        }}
        .deal-title {{
            font-size: 17px;
            font-weight: 600;
            color: #363737;
            margin-bottom: 6px;
            line-height: 1.3;
        }}
        .deal-title a {{
            color: #363737;
            text-decoration: none;
        }}
        .deal-title a:hover {{
            color: #4384F3;
        }}
        .deal-indicator {{
            font-size: 16px;
            font-weight: 600;
            color: #27ae60;
            margin-bottom: 6px;
        }}
        .deal-price {{
            font-size: 20px;
            font-weight: 700;
            color: #27ae60;
            margin-bottom: 4px;
        }}
        .deal-price-context {{
            font-size: 13px;
            color: #888;
            margin-bottom: 6px;
        }}
        .deal-badges {{
            margin-bottom: 6px;
        }}
        .deal-badge {{
            display: inline-block;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
            margin-right: 5px;
        }}
        .deal-badge.near-low {{
            background-color: #fee2e2;
            color: #dc2626;
        }}
        .deal-badge.top-deal {{
            background-color: #dcfce7;
            color: #16a34a;
        }}
        .deal-badge.big-savings {{
            background-color: #fef3c7;
            color: #d97706;
        }}
        .deal-tag {{
            display: inline-block;
            background-color: #4384F3;
            color: white;
            padding: 3px 10px;
            border-radius: 3px;
            font-size: 11px;
            font-weight: 600;
            margin-right: 5px;
            margin-bottom: 5px;
        }}
        .deal-tag.highlight {{
            background-color: #27ae60;
        }}
        .deal-meta {{
            font-size: 13px;
            color: #666;
            margin-top: 8px;
        }}
        .deal-meta a {{
            color: #4384F3;
            text-decoration: none;
        }}
        .deal-meta a:hover {{
            text-decoration: underline;
        }}
        .buy-button {{
            display: inline-block;
            background-color: #4384F3;
            color: white;
            padding: 10px 20px;
            text-decoration: none;
            border-radius: 5px;
            font-weight: 600;
            margin-top: 10px;
            font-size: 14px;
        }}
        .buy-button:hover {{
            background-color: #2b74f1;
        }}
        .footer {{
            font-size: 13px;
            color: #666;
            line-height: 1.6;
        }}
        .intro {{
            font-size: 16px;
            line-height: 1.6;
            margin-bottom: 15px;
        }}
        .intro a {{
            color: #4384F3;
            text-decoration: none;
        }}
        .intro a:hover {{
            text-decoration: underline;
        }}
        .disclosure {{
            font-size: 12px;
            color: #666;
            margin-bottom: 25px;
            padding: 10px;
            background-color: #f9f9f9;
            border-radius: 4px;
        }}
        @media (max-width: 480px) {{
            .deal {{
                flex-direction: column;
                align-items: center;
                text-align: center;
            }}
            .deal-image {{
                width: 150px;
                height: 150px;
                margin-bottom: 15px;
            }}
            .deal-image img {{
                width: 150px;
                height: 150px;
            }}
            .deal-content {{
                width: 100%;
            }}
        }}
        /* Hide Mailchimp's extra line breaks before footer */
        center > br {{ display: none; }}
        .view-online {{
            text-align: center;
            margin-bottom: 15px;
            font-size: 13px;
        }}
        .view-online a {{
            color: #4384F3;
            text-decoration: none;
        }}
        .view-online a:hover {{
            text-decoration: underline;
        }}
        .price-loading {{
            color: #999;
            font-style: italic;
        }}
        .unclassified-ad {{
            margin-top: 30px;
            padding: 25px;
            border: 2px dashed #4384F3;
            border-radius: 8px;
            background-color: #f8faff;
        }}
        .unclassified-ad-label {{
            font-size: 11px;
            font-weight: 700;
            color: #4384F3;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            margin-bottom: 2px;
        }}
        .unclassified-ad-tagline {{
            font-size: 13px;
            color: #888;
            margin-bottom: 15px;
            font-style: italic;
        }}
    </style>
"""

    # Add JavaScript for web mode (dynamic price fetching)
    if web_mode:
        html += """
    <script>
        async function fetchPrices() {
            const deals = document.querySelectorAll('.deal[data-asin]');
            const asins = Array.from(deals).map(d => d.dataset.asin).filter(Boolean);

            if (asins.length === 0) return;

            // Batch into groups of 10 (API limit)
            for (let i = 0; i < asins.length; i += 10) {
                const batch = asins.slice(i, i + 10);
                try {
                    const response = await fetch(`/api/prices?asins=${batch.join(',')}`);
                    if (!response.ok) throw new Error('API error');
                    const prices = await response.json();

                    for (const [asin, data] of Object.entries(prices)) {
                        const deal = document.querySelector(`.deal[data-asin="${asin}"]`);
                        if (!deal) continue;

                        const priceEl = deal.querySelector('.deal-price');
                        const indicatorEl = deal.querySelector('.deal-indicator');

                        if (data.current_price && priceEl) {
                            priceEl.textContent = `$${data.current_price.toFixed(2)}`;
                            priceEl.classList.remove('price-loading');
                        }

                        if (data.current_price && data.list_price && data.list_price > data.current_price && indicatorEl) {
                            const savingsPct = Math.round(((data.list_price - data.current_price) / data.list_price) * 100);
                            indicatorEl.textContent = `${savingsPct}% off list price`;
                        }
                    }
                } catch (e) {
                    console.error('Error fetching prices:', e);
                }
            }

            // Update timestamp
            const disclosureEl = document.querySelector('.disclosure');
            if (disclosureEl) {
                const now = new Date();
                const timeStr = now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' });
                disclosureEl.innerHTML = disclosureEl.innerHTML.replace(/at \\d+:\\d+ [A-Z]+/, `at ${timeStr}`);
            }
        }

        document.addEventListener('DOMContentLoaded', fetchPrices);
    </script>
"""

    html += """
</head>
<body>
    <div class="container">
"""
    html += f"""
        <div class="logo">
            <img src="{LOGO_URL}" alt="Recomendo Deals">
        </div>
        <div class="subtitle">{today}</div>
"""

    # Add "View in browser" link for email mode
    if not web_mode and web_url:
        html += f"""
        <div class="view-online">
            <a href="{web_url}" target="_blank">View online for live prices</a>
        </div>
"""

    html += f"""
        <div class="intro">
            Today, we've found <strong>{len(deals)}</strong> great deals on things we've previously featured in our <a href="https://recomendo.com">Recomendo newsletter</a> and in <a href="https://cool-tools.org">Cool Tools</a>.
        </div>

        <div class="disclosure">
            As an Amazon Associate we earn from qualifying purchases.{'' if web_mode else f' Product prices and availability are accurate as of today at {price_time_str} and are subject to change. Any price and availability information displayed on Amazon at the time of purchase applies to your purchase.'}
        </div>
"""

    for asin, deal in deals:
        image_url = deal.get("image_url") or ""

        # Get live price from PA API (Amazon Associates compliant)
        live_price = live_prices.get(asin, {})

        # Title: use custom title as-is, otherwise shorten the full title
        full_title = live_price.get("title") or deal.get("title") or deal.get("catalog_title") or f"Product {asin}"
        if live_price.get("title_is_custom"):
            title_text = full_title  # User already edited this, don't shorten again
        else:
            title_text = shorten_title(full_title)

        # Use original affiliate URL from catalog (for accounting purposes)
        # Only fall back to constructed URL if no original exists
        if live_price.get("affiliate_url"):
            buy_link = live_price["affiliate_url"]
        else:
            buy_link = get_buy_link(deal)

        price_html = ""
        indicator_html = ""
        if live_price.get("current_price"):
            current = live_price["current_price"]
            price_html = f'<div class="deal-price">{format_price(current)}</div>'

            # Only show savings if PA API provides list_price (Amazon's own data)
            if live_price.get("list_price") and live_price["list_price"] > current:
                savings_pct = ((live_price["list_price"] - current) / live_price["list_price"]) * 100
                indicator_html = f'<div class="deal-indicator">{savings_pct:.0f}% off list price</div>'

        # Price context: show 90-day average and badges
        price_context_html = ""
        badges_html = ""
        current = live_price.get("current_price") or deal.get("current_price")
        avg_90 = deal.get("avg_90_day")
        low_90 = deal.get("low_90_day")
        deal_score = deal.get("deal_score", 0)
        savings_dollars = deal.get("savings_dollars", 0) or 0

        if current and avg_90 and avg_90 > current:
            price_context_html = f'<div class="deal-price-context">90-day avg: {format_price(avg_90)}</div>'

        badges = []
        if current and low_90 and current <= low_90 * 1.05:
            badges.append('<span class="deal-badge near-low">90-Day Low</span>')
        if deal_score >= 70:
            badges.append('<span class="deal-badge top-deal">Top Deal</span>')
        if savings_dollars >= 20:
            badges.append(f'<span class="deal-badge big-savings">Save {format_price(savings_dollars)}</span>')
        if badges:
            badges_html = '<div class="deal-badges">' + ''.join(badges) + '</div>'

        # Build meta info with issue links and benefits
        # Priority: Recomendo over Cool Tools (if both exist, only show Recomendo)
        # Format: "Reviewed in [Source]: [benefits sentence]"
        meta_html = ""
        # Benefits priority: live_prices > catalog_benefits
        benefits = live_price.get("benefits") or catalog_benefits.get(asin, "")
        issues = deal.get("issues", [])
        if issues:
            recomendo_issues = [i for i in issues if i.get("source") != "cooltools"]
            cooltools_issues = [i for i in issues if i.get("source") == "cooltools"]

            if recomendo_issues:
                # Show Recomendo link (prioritized)
                first_issue = recomendo_issues[0]
                issue_url = first_issue.get("url", "")
                issue_date = first_issue.get("date", "")
                issue_num = calculate_issue_number(issue_date)

                if issue_url and issue_num:
                    meta_html = f'Reviewed in <a href="{issue_url}" target="_blank">Recomendo #{issue_num}</a>'
                elif issue_url:
                    meta_html = f'Reviewed in <a href="{issue_url}" target="_blank">Recomendo</a>'
            elif cooltools_issues:
                # Only show Cool Tools if no Recomendo source
                first_ct = cooltools_issues[0]
                ct_url = first_ct.get("url", "")
                if ct_url:
                    meta_html = f'Reviewed in <a href="{ct_url}" target="_blank">Cool Tools</a>'

        # Append benefits description if provided
        if benefits:
            if meta_html:
                meta_html += f': {benefits}'
            else:
                meta_html = benefits

        # Image HTML - prefer PA API image if available
        actual_image = live_price.get("image_url") or image_url
        image_html = ""
        if actual_image:
            image_html = f'''
        <div class="deal-image">
            <a href="{buy_link}" target="_blank">
                <img src="{actual_image}" alt="{title_text}" loading="lazy">
            </a>
        </div>'''

        # Simple button text
        button_text = "SEE DEAL"

        # For web mode, add data-asin attribute and placeholder for prices
        if web_mode:
            # In web mode, show loading placeholder for price
            price_class = 'deal-price price-loading' if not live_price.get("current_price") else 'deal-price'
            if not price_html:
                price_html = f'<div class="{price_class}">Loading...</div>'
            if not indicator_html:
                indicator_html = '<div class="deal-indicator"></div>'

        html += f"""
        <div class="deal" data-asin="{asin}">
            {image_html}
            <div class="deal-content">
                <div class="deal-title">
                    <a href="{buy_link}" target="_blank">{title_text}</a>
                </div>
                {price_html}
                {price_context_html}
                {indicator_html}
                {badges_html}
                <div class="deal-meta">{meta_html}</div>
                <a href="{buy_link}" class="buy-button" target="_blank">{button_text}</a>
            </div>
        </div>
"""

    # Unclassified Ad section (optional)
    if unclassified_ad and unclassified_ad.get("asin"):
        ad_asin = unclassified_ad["asin"]
        ad_title = unclassified_ad.get("title", f"Product {ad_asin}")
        ad_desc = unclassified_ad.get("description", "")
        ad_image = unclassified_ad.get("image_url", "")
        ad_price = unclassified_ad.get("current_price")
        ad_list_price = unclassified_ad.get("list_price")
        ad_url = unclassified_ad.get("affiliate_url",
                     f"https://www.amazon.com/dp/{ad_asin}?tag=recomendos-20")

        ad_price_html = ""
        if ad_price:
            ad_price_html = f'<div class="deal-price">${ad_price:.2f}</div>'
            if ad_list_price and ad_list_price > ad_price:
                savings_pct = ((ad_list_price - ad_price) / ad_list_price) * 100
                ad_price_html += f'<div class="deal-indicator">{savings_pct:.0f}% off list price</div>'

        ad_image_html = ""
        if ad_image:
            ad_image_html = f'''
                <div class="deal-image">
                    <a href="{ad_url}" target="_blank">
                        <img src="{ad_image}" alt="">
                    </a>
                </div>'''

        ad_desc_html = f'<div class="deal-meta">{ad_desc}</div>' if ad_desc else ""

        html += f"""
        <div class="unclassified-ad">
            <div class="unclassified-ad-label">Unclassified Ad</div>
            <div class="unclassified-ad-tagline">A deal too good not to share</div>
            <div class="deal" style="border-bottom:none;margin-bottom:0;padding-bottom:0;">
                {ad_image_html}
                <div class="deal-content">
                    <div class="deal-title">
                        <a href="{ad_url}" target="_blank">{ad_title}</a>
                    </div>
                    {ad_price_html}
                    {ad_desc_html}
                    <a href="{ad_url}" class="buy-button" target="_blank">SEE DEAL</a>
                </div>
            </div>
        </div>
"""

    # Archive URL for past deals with live prices
    archive_url = "https://reco-deals.vercel.app/"

    html += f"""
        <div class="footer">
            <p><em>Recomendo Deals is published by Cool Tools Lab, LLC, a small company of three people. We also run <a href="https://recomendo.com">Recomendo</a>, the <a href="https://kk.org/cooltools/">Cool Tools website</a>, a <a href="https://www.youtube.com/cooltools">YouTube channel</a> and <a href="https://open.spotify.com/show/5Bx52UzoVrjSp8bsZyNJcI">podcast</a>, and other newsletters, including <a href="https://garstips.substack.com/">Gar's Tips &amp; Tools</a>, <a href="https://nomadico.substack.com/">Nomadico</a>, <a href="https://whatsinmynow.substack.com/">What's in my NOW?</a>, <a href="https://toolsforpossibilities.substack.com/">Tools for Possibilities</a>, <a href="https://booksthatbelongonpaper.substack.com/">Books That Belong On Paper</a>, and <a href="https://bookfreak.substack.com/">Book Freak</a>.</em></p>
            {'<p>Looking for past deals? <a href="' + archive_url + '">Browse our archive</a> for previous issues with up-to-date prices.</p>' if not web_mode else ''}
            <p>If a friend sent this issue of Recomendo Deals to you and you'd like to subscribe, <a href="https://mailchi.mp/cool-tools/recomendo-deals">sign up here</a>. It's free.</p>
            <p class="copyright">&copy; 2026 Cool Tools Lab, LLC. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
"""
    return html


def generate_text_report(deals: list, title: str = "Recomendo Deals", live_prices: dict = None) -> str:
    """Generate a plain text report with live prices."""
    if live_prices is None:
        live_prices = {}
    today = datetime.now().strftime("%B %d, %Y")

    lines = [
        "=" * 60,
        title.upper(),
        today,
        "=" * 60,
        "",
        f"{len(deals)} deals found today",
        "",
        "-" * 60,
        "",
    ]

    for i, (asin, deal) in enumerate(deals, 1):
        title_text = deal.get("catalog_title") or deal.get("title") or f"Product {asin}"
        buy_link = get_buy_link(deal)

        lines.append(f"{i}. {title_text}")

        # Show live price if available
        live_price = live_prices.get(asin, {})
        if live_price.get("current_price"):
            price_line = f"   {format_price(live_price['current_price'])}"
            if live_price.get("list_price") and live_price["list_price"] > live_price["current_price"]:
                price_line += f" (was {format_price(live_price['list_price'])})"
            lines.append(price_line)

        lines.append(f"   {format_deal_indicator(deal)}")

        # Issue info
        issues = deal.get("issues", [])
        if issues:
            first_issue = issues[0]
            issue_date = first_issue.get("date", "")
            issue_num = calculate_issue_number(issue_date)
            if issue_num:
                lines.append(f"   Reviewed in Recomendo #{issue_num}")

        lines.append(f"   {buy_link}")
        lines.append("")

    lines.extend([
        "-" * 60,
        "",
        "These are products previously recommended by Recomendo",
        "that are currently on sale. Prices subject to change.",
    ])

    return "\n".join(lines)


def generate_markdown_report(deals: list, title: str = "Recomendo Deals", live_prices: dict = None) -> str:
    """Generate a Markdown report with live prices (good for newsletters)."""
    if live_prices is None:
        live_prices = {}
    today = datetime.now().strftime("%B %d, %Y")

    lines = [
        f"# {title}",
        f"*{today}*",
        "",
        f"**{len(deals)} deals found today**",
        "",
        "---",
        "",
    ]

    for asin, deal in deals:
        title_text = deal.get("catalog_title") or deal.get("title") or f"Product {asin}"
        buy_link = get_buy_link(deal)

        # Prefer live image from PA API
        live_price = live_prices.get(asin, {})
        image_url = live_price.get("image_url") or deal.get("image_url")

        lines.append(f"### [{title_text}]({buy_link})")
        lines.append("")

        if image_url:
            lines.append(f"![{title_text}]({image_url})")
            lines.append("")

        # Show live price if available
        if live_price.get("current_price"):
            price_line = f"**{format_price(live_price['current_price'])}**"
            if live_price.get("list_price") and live_price["list_price"] > live_price["current_price"]:
                price_line += f" ~~{format_price(live_price['list_price'])}~~"
                if live_price.get("savings_percent"):
                    price_line += f" ({live_price['savings_percent']:.0f}% off)"
            lines.append(price_line)
            lines.append("")

        lines.append(f"*{format_deal_indicator(deal)}*")
        lines.append("")

        # Issue info
        issues = deal.get("issues", [])
        if issues:
            first_issue = issues[0]
            issue_url = first_issue.get("url", "")
            issue_date = first_issue.get("date", "")
            issue_num = calculate_issue_number(issue_date)
            if issue_url and issue_num:
                lines.append(f"*Reviewed in [Recomendo #{issue_num}]({issue_url})*")

        lines.append("")
        lines.append(f"[View on Amazon →]({buy_link})")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.extend([
        "*These are products previously recommended by Recomendo that are currently on sale.*",
    ])

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate Recomendo Deals reports")
    parser.add_argument("--top", type=int, default=10, help="Number of top deals to include")
    parser.add_argument("--output", "-o", type=str, help="Output file path")
    parser.add_argument("--web", action="store_true", help="Generate web version with dynamic price fetching")
    parser.add_argument("--web-url", type=str, help="URL to web version (for 'View in browser' link)")
    parser.add_argument("--format", choices=["html", "text", "markdown"], default="html", help="Output format")

    args = parser.parse_args()

    # Load deals
    deals_data = load_deals()
    if not deals_data:
        print("No deals found. Run check_deals.py first.")
        sys.exit(1)

    # Handle nested structure (deals.json has 'deals' key)
    if isinstance(deals_data, dict) and "deals" in deals_data:
        deals_data = deals_data["deals"]

    # Filter and sort deals
    deals = filter_and_sort_deals(deals_data, top_n=args.top)
    if not deals:
        print("No valid deals after filtering.")
        sys.exit(1)

    print(f"Found {len(deals)} deals")

    # Load catalog benefits for all modes
    catalog_benefits = load_catalog_benefits()
    print(f"Loaded {len(catalog_benefits)} benefit descriptions from catalog")

    # Fetch live prices for HTML output (email mode)
    live_prices = {}
    price_timestamp = None
    if args.format == "html" and not args.web:
        asins = [asin for asin, _ in deals]
        live_prices = fetch_live_prices(asins)
        price_timestamp = datetime.now()

    # Generate report
    if args.format == "html":
        content = generate_html_report(
            deals,
            live_prices=live_prices,
            price_timestamp=price_timestamp,
            web_mode=args.web,
            web_url=args.web_url,
            catalog_benefits=catalog_benefits
        )
    elif args.format == "markdown":
        content = generate_markdown_report(deals, live_prices=live_prices)
    else:
        content = generate_text_report(deals, live_prices=live_prices)

    # Output
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Report written to {output_path}")
    else:
        print(content)

