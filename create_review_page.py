#!/usr/bin/env python3
"""
Interactive deal review page served from deals.json.

Reads catalog/deals.json, serves a review interface, and creates
a Mailchimp newsletter draft when deals are confirmed.

Usage:
    python3 create_review_page.py              # Serve review page and open browser
    python3 create_review_page.py --port 9000  # Custom port
"""

import argparse
import csv
import json
import sys
import webbrowser
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

PROJECT_ROOT = Path(__file__).parent
DEALS_FILE = PROJECT_ROOT / "catalog" / "deals.json"
HIDDEN_FILE = PROJECT_ROOT / "catalog" / "hidden_products.json"
SALES_CSV = PROJECT_ROOT / "amazon-2025.csv"

sys.path.insert(0, str(PROJECT_ROOT))
from review_deals import (
    generate_and_send, load_full_catalog, generate_benefits_for_deals,
    shorten_title, get_affiliate_group, check_keepa_prices,
    generate_benefit_description,
)
from generate_report import load_featured_history, COOLDOWN_DAYS, get_media_category

# Server state
server_should_stop = False


def load_deals() -> dict:
    if DEALS_FILE.exists():
        with open(DEALS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_hidden_products() -> dict:
    """Load hidden products with their expiry dates. Returns {asin: expiry_iso}."""
    if HIDDEN_FILE.exists():
        with open(HIDDEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Prune expired entries
        today = datetime.now().strftime("%Y-%m-%d")
        active = {asin: exp for asin, exp in data.items() if exp > today}
        if len(active) != len(data):
            with open(HIDDEN_FILE, "w", encoding="utf-8") as f:
                json.dump(active, f, indent=2)
        return active
    return {}


def save_hidden_products(hidden: dict):
    with open(HIDDEN_FILE, "w", encoding="utf-8") as f:
        json.dump(hidden, f, indent=2)


def load_sales_data() -> dict:
    """Parse sales CSV and return {asin: {qty, revenue}} for DI sales only."""
    sales = {}
    if not SALES_CSV.exists():
        return sales
    with open(SALES_CSV, "r", encoding="utf-8") as f:
        next(f)  # skip metadata line 1
        reader = csv.DictReader(f)
        for row in reader:
            asin = row.get("ASIN", "").strip()
            if not asin:
                continue
            indirect = row.get("Indirect Sales", "").strip().lower()
            if indirect != "di":
                continue
            try:
                qty = int(row.get("Qty", 0))
                price = float(row.get("Price($)", 0))
            except (ValueError, TypeError):
                continue
            if asin not in sales:
                sales[asin] = {"qty": 0, "revenue": 0.0}
            sales[asin]["qty"] += qty
            sales[asin]["revenue"] += qty * price
    return sales


def resolve_affiliate_url(aff) -> str:
    """Resolve affiliate_url (string or GeniusLink dict) to a URL string."""
    if isinstance(aff, dict):
        code = aff.get("code", "")
        domain = aff.get("domain", "geni.us")
        return f"https://{domain}/{code}" if code else ""
    return aff if isinstance(aff, str) else ""


def merge_catalog_and_deals() -> dict:
    """Load all products from products.json, merge in deal data from deals.json."""
    catalog = load_full_catalog()
    deals_data = load_deals()
    deals = deals_data.get("deals", {})
    hidden = load_hidden_products()
    all_results = deals_data.get("all_results", {})
    sales = load_sales_data()

    merged = {}
    for asin, product in catalog.items():
        if asin in hidden:
            continue
        deal = deals.get(asin, {})
        result = all_results.get(asin, {})
        merged[asin] = {
            # Catalog fields — prefer Keepa title (actual product name) over
            # catalog title (may be an article/episode name for Cool Tools entries)
            "title": deal.get("title") or product.get("title") or asin,
            "image_url": deal.get("image_url") or result.get("image_url") or product.get("image_url", ""),
            "issues": product.get("issues", []),
            "affiliate_url": resolve_affiliate_url(product.get("affiliate_url")),
            "amazon_url": product.get("amazon_url", f"https://www.amazon.com/dp/{asin}"),
            "first_featured": product.get("first_featured", ""),
            "catalog_title": product.get("title", ""),
            "benefit_description": product.get("benefit_description", ""),
            "short_title": product.get("short_title", ""),
            # Deal fields (from deals.json, if checked)
            "current_price": deal.get("current_price"),
            "avg_90_day": deal.get("avg_90_day"),
            "high_90_day": deal.get("high_90_day"),
            "low_90_day": deal.get("low_90_day"),
            "is_deal": deal.get("is_deal", False),
            "deal_reasons": deal.get("deal_reasons", []),
            "percent_below_avg": deal.get("percent_below_avg") or 0,
            "percent_below_high": deal.get("percent_below_high") or 0,
            "savings_dollars": deal.get("savings_dollars") or 0,
            "rating": deal.get("rating"),
            "review_count": deal.get("review_count"),
            "deal_score": deal.get("deal_score", 0),
            "price_source": deal.get("price_source"),
            "has_deal_data": asin in deals,
            "sales_qty": sales.get(asin, {}).get("qty", 0),
            "sales_revenue": sales.get(asin, {}).get("revenue", 0),
        }
        # Use cached list_price from deals.json if available
        # Discard absurd list prices (Keepa sometimes returns garbage MSRP)
        deal_list = deal.get("list_price")
        deal_current = merged[asin].get("current_price")
        if deal_list and deal_current and deal_list <= deal_current * 5:
            merged[asin]["list_price"] = deal_list
        elif deal_list and not deal_current:
            merged[asin]["list_price"] = deal_list

    # Enrich deal candidates with PA API data (real-time prices, list price, availability)
    deal_asins = [asin for asin, p in merged.items() if p.get("is_deal")]
    if deal_asins:
        try:
            from pa_api import get_prices_for_asins
            print(f"Enriching {len(deal_asins)} deals with PA API...")
            pa_data = get_prices_for_asins(deal_asins)
            enriched = 0
            for asin, info in pa_data.items():
                if "error" in info or asin not in merged:
                    continue
                p = merged[asin]
                if info.get("list_price"):
                    p["list_price"] = info["list_price"]
                if info.get("current_price"):
                    p["pa_current_price"] = info["current_price"]
                if info.get("availability"):
                    p["availability"] = info["availability"]
                if info.get("savings_percent"):
                    p["savings_percent"] = info["savings_percent"]
                if info.get("product_features"):
                    p["product_features"] = info["product_features"]
                enriched += 1
            print(f"  Enriched {enriched}/{len(deal_asins)} deals with PA API data")

            # Cache list prices back to deals.json for future sessions
            cached = 0
            for asin, info in pa_data.items():
                if "error" in info or not info.get("list_price"):
                    continue
                if asin in deals and deals[asin].get("list_price") != info["list_price"]:
                    deals[asin]["list_price"] = info["list_price"]
                    cached += 1
            if cached:
                deals_data["deals"] = deals
                with open(DEALS_FILE, "w", encoding="utf-8") as f:
                    json.dump(deals_data, f, indent=2)
                print(f"  Cached {cached} list prices to deals.json")
        except Exception as e:
            print(f"  PA API enrichment failed (non-blocking): {e}")

    return {
        "products": merged,
        "generated_at": deals_data.get("generated_at", ""),
        "deals_checked": len(deals),
    }


def prepare_candidates(products: dict) -> list:
    """Transform merged products into the (asin, deal) format expected by generate_and_send."""
    candidates = []
    for asin, d in products.items():
        aff = d.get("affiliate_url", "")

        avg_price = d.get("avg_90_day") or 0
        current_price = d.get("current_price") or 0
        list_price = d.get("list_price") or 0
        pct_below = d.get("percent_below_avg") or 0

        # Discard absurd list prices (Keepa sometimes returns garbage MSRP data)
        if list_price and current_price and list_price > current_price * 5:
            list_price = 0

        # Prefer list price (MSRP) for discount display, fall back to 90-day avg
        if list_price and current_price and list_price > current_price:
            display_list_price = list_price
        elif avg_price and current_price and avg_price > current_price:
            display_list_price = avg_price
        else:
            display_list_price = current_price or None

        deal = {
            "asin": asin,
            "live_price": current_price or None,
            "live_title": d.get("title") or asin,
            "live_image": d.get("image_url", ""),
            "live_list_price": display_list_price,
            "savings_percent": pct_below if pct_below > 0 else 0,
            "review_count": d.get("review_count", 0),
            "star_rating": d.get("rating", 0),
            "product_group": "",
            "binding": "",
            "issues": d.get("issues", []),
            "affiliate_url": aff,
            "amazon_url": d.get("amazon_url", f"https://www.amazon.com/dp/{asin}"),
            "is_deal": d.get("is_deal", False),
            "percent_below_avg": pct_below,
            "percent_below_high": d.get("percent_below_high", 0),
            "avg_90_day": d.get("avg_90_day"),
            "high_90_day": d.get("high_90_day"),
            "low_90_day": d.get("low_90_day"),
            "savings_dollars": d.get("savings_dollars", 0),
            "catalog_title": d.get("title", ""),
            "first_featured": d.get("first_featured", ""),
            "has_deal_data": d.get("has_deal_data", False),
            "price_source": d.get("price_source"),
            "sales_qty": d.get("sales_qty", 0),
            "sales_revenue": d.get("sales_revenue", 0),
            "near_low_pct": round((current_price / d["low_90_day"] - 1) * 100, 1) if d.get("low_90_day") and current_price and d["low_90_day"] > 0 else None,
        }
        candidates.append((asin, deal))

    # Sort: deals with savings first, then everything else by title
    candidates.sort(key=lambda x: (x[1].get("percent_below_avg", 0) > 0, x[1].get("percent_below_avg", 0)), reverse=True)
    return candidates


def build_html(merged_data: dict) -> str:
    products = merged_data.get("products", {})
    generated_at = merged_data.get("generated_at", "")
    deals_checked = merged_data.get("deals_checked", 0)

    # Serialize products as JSON for embedding
    products_json = json.dumps(products)

    # Load featured history for cooldown display
    history = load_featured_history()
    history_json = json.dumps(history)

    # Extract benefit descriptions already in the merged data
    benefits = {asin: p["benefit_description"] for asin, p in products.items() if p.get("benefit_description")}
    benefits_json = json.dumps(benefits)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Review Deals - Recomendo</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f0f2f5;
            color: #363737;
            line-height: 1.5;
        }}
        .header {{
            background: #fff;
            border-bottom: 1px solid #e0e0e0;
            padding: 16px 24px;
            position: sticky;
            top: 0;
            z-index: 100;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }}
        .header-inner {{
            max-width: 1200px;
            margin: 0 auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
        }}
        .header h1 {{
            font-size: 20px;
            font-weight: 700;
            color: #363737;
        }}
        .header h1 span {{
            color: #4384F3;
        }}
        .stats {{
            display: flex;
            gap: 16px;
            font-size: 13px;
            color: #666;
        }}
        .stats .stat-value {{
            font-weight: 700;
            color: #363737;
        }}
        .toolbar {{
            background: #fff;
            border-bottom: 1px solid #e0e0e0;
            padding: 12px 24px;
            position: sticky;
            top: 57px;
            z-index: 99;
        }}
        .toolbar-inner {{
            max-width: 1200px;
            margin: 0 auto;
            display: flex;
            gap: 12px;
            align-items: center;
            flex-wrap: wrap;
        }}
        .search-box {{
            flex: 1;
            min-width: 200px;
            padding: 8px 12px;
            border: 1px solid #ddd;
            border-radius: 6px;
            font-size: 14px;
            outline: none;
            transition: border-color 0.2s;
        }}
        .search-box:focus {{ border-color: #4384F3; }}
        .filter-group {{
            display: flex;
            gap: 4px;
        }}
        .filter-btn {{
            padding: 6px 14px;
            border: 1px solid #ddd;
            border-radius: 6px;
            background: #fff;
            font-size: 13px;
            cursor: pointer;
            transition: all 0.15s;
            color: #555;
            white-space: nowrap;
        }}
        .filter-btn:hover {{ border-color: #4384F3; color: #4384F3; }}
        .filter-btn.active {{ background: #4384F3; color: #fff; border-color: #4384F3; }}
        select.sort-select {{
            padding: 6px 12px;
            border: 1px solid #ddd;
            border-radius: 6px;
            font-size: 13px;
            background: #fff;
            cursor: pointer;
            outline: none;
            color: #555;
        }}
        .container {{
            max-width: 1200px;
            margin: 20px auto;
            padding: 0 24px 80px;
        }}
        .results-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
            font-size: 13px;
            color: #888;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(520px, 1fr));
            gap: 12px;
        }}
        .card {{
            background: #fff;
            border-radius: 10px;
            overflow: hidden;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
            transition: all 0.2s;
            border: 2px solid transparent;
            position: relative;
        }}
        .card:hover {{ box-shadow: 0 4px 12px rgba(0,0,0,0.1); }}
        .card.selected {{ border-color: #27ae60; background: #f6fdf6; }}
        .card.cooldown {{ opacity: 0.55; }}
        .card-top {{
            display: flex;
            gap: 14px;
            padding: 16px;
            cursor: pointer;
        }}
        .card-image {{
            width: 80px;
            height: 80px;
            flex-shrink: 0;
            border-radius: 8px;
            overflow: hidden;
            background: #f5f5f5;
        }}
        .card-image img {{
            width: 100%;
            height: 100%;
            object-fit: contain;
        }}
        .card-body {{
            flex: 1;
            min-width: 0;
        }}
        .card-title-row {{
            display: flex;
            align-items: center;
            gap: 6px;
            margin-bottom: 4px;
        }}
        .card-title {{
            font-size: 14px;
            font-weight: 600;
            color: #363737;
            flex: 1;
            min-width: 0;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .card-title a {{
            color: inherit;
            text-decoration: none;
        }}
        .card-title a:hover {{ color: #4384F3; }}
        .card-link {{
            color: #bbb;
            text-decoration: none;
            font-size: 13px;
            flex-shrink: 0;
        }}
        .card-link:hover {{ color: #4384F3; }}
        .card-price {{
            font-size: 18px;
            font-weight: 700;
            color: #27ae60;
            margin-bottom: 2px;
        }}
        .card-price .original {{
            font-size: 13px;
            color: #999;
            text-decoration: line-through;
            font-weight: 400;
            margin-left: 6px;
        }}
        .card-badges {{
            display: flex;
            flex-wrap: wrap;
            gap: 4px;
            margin-top: 6px;
        }}
        .badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
        }}
        .badge-deal {{ background: #dcfce7; color: #16a34a; }}
        .badge-savings {{ background: #fef3c7; color: #d97706; }}
        .badge-low {{ background: #fee2e2; color: #dc2626; }}
        .badge-media {{ background: #f3e8ff; color: #7c3aed; }}
        .badge-cooldown {{ background: #fee2e2; color: #dc2626; }}
        .badge-unavailable {{ background: #fee2e2; color: #dc2626; }}
        .badge-list {{ background: #dbeafe; color: #2563eb; }}
        .badge-prime {{ background: #dbeafe; color: #1d4ed8; }}
        .badge-discrepancy {{ background: #fef3c7; color: #d97706; }}
        .card-meta {{
            font-size: 12px;
            color: #888;
            margin-top: 4px;
        }}
        .card-meta a {{ color: #4384F3; text-decoration: none; }}
        .card-meta a:hover {{ text-decoration: underline; }}
        .card-rating {{
            font-size: 12px;
            color: #d97706;
            font-weight: 600;
        }}
        .card-score {{
            font-size: 12px;
            font-weight: 600;
            margin-left: 8px;
        }}
        .card-sales {{
            font-size: 12px;
            color: #7c3aed;
            font-weight: 600;
            margin-left: 8px;
        }}
        .card-extra {{
            display: flex;
            align-items: center;
            gap: 4px;
            margin-top: 4px;
        }}
        .card-actions-col {{
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 6px;
            flex-shrink: 0;
            padding-top: 2px;
        }}
        .card-checkbox {{
            width: 22px;
            height: 22px;
            cursor: pointer;
            accent-color: #27ae60;
        }}

        /* Inline edit panel on review cards */
        .card-edit-toggle, .card-hide-btn {{
            width: 24px;
            height: 24px;
            cursor: pointer;
            background: none;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 13px;
            color: #999;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.15s;
        }}
        .card-edit-toggle:hover {{ border-color: #4384F3; color: #4384F3; }}
        .card.has-edits .card-edit-toggle {{ color: #27ae60; border-color: #27ae60; }}
        .card-hide-btn:hover {{ border-color: #e67e22; color: #e67e22; }}
        .card-edit-panel {{
            display: none;
            padding: 0 16px 14px;
            border-top: 1px solid #eee;
        }}
        .card-edit-panel.open {{ display: block; }}
        .card-edit-panel .edit-field {{
            margin-top: 10px;
        }}
        .card-edit-panel .edit-label {{
            font-size: 11px;
            font-weight: 600;
            color: #888;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 4px;
        }}
        .card-edit-panel .edit-row {{
            display: flex;
            gap: 6px;
            align-items: flex-start;
        }}
        .card-edit-panel input,
        .card-edit-panel textarea {{
            flex: 1;
            padding: 6px 10px;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 13px;
            font-family: inherit;
            outline: none;
            transition: border-color 0.15s;
        }}
        .card-edit-panel input:focus,
        .card-edit-panel textarea:focus {{ border-color: #4384F3; }}
        .card-edit-panel textarea {{ resize: vertical; min-height: 54px; }}
        .card-edit-panel .edit-btn {{
            padding: 6px 10px;
            border: 1px solid #ddd;
            border-radius: 5px;
            background: #fff;
            font-size: 12px;
            cursor: pointer;
            white-space: nowrap;
            color: #555;
            transition: all 0.15s;
        }}
        .card-edit-panel .edit-btn:hover {{ border-color: #4384F3; color: #4384F3; }}
        .card-edit-panel .edit-btn:disabled {{ opacity: 0.5; cursor: wait; }}
        .card-edit-panel .edit-btn.generating {{
            color: #999;
            border-style: dashed;
        }}

        /* Selection bar */
        .selection-bar {{
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            background: #4384F3;
            color: #fff;
            padding: 14px 24px;
            display: none;
            z-index: 200;
            box-shadow: 0 -2px 10px rgba(0,0,0,0.15);
        }}
        .selection-bar.visible {{ display: block; }}
        .selection-bar-inner {{
            max-width: 1200px;
            margin: 0 auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .selection-bar .count {{
            font-weight: 600;
            font-size: 16px;
        }}
        .selection-bar .actions {{
            display: flex;
            gap: 10px;
        }}
        .selection-bar button {{
            padding: 8px 20px;
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.15s;
        }}
        .btn-secondary {{ background: rgba(255,255,255,0.2); color: #fff; }}
        .btn-secondary:hover {{ background: rgba(255,255,255,0.3); }}
        .btn-primary {{ background: #fff; color: #4384F3; }}
        .btn-primary:hover {{ background: #f0f0f0; }}
        .btn-confirm {{ background: #27ae60; color: #fff; font-size: 15px; padding: 10px 28px; }}
        .btn-confirm:hover {{ background: #219a52; }}
        .btn-confirm:disabled {{ background: #999; cursor: not-allowed; }}

        .empty-state {{
            text-align: center;
            padding: 60px 20px;
            color: #888;
        }}
        .empty-state h2 {{
            font-size: 18px;
            margin-bottom: 8px;
            color: #666;
        }}

        @media (max-width: 720px) {{
            .grid {{ grid-template-columns: 1fr; }}
            .header-inner {{ flex-direction: column; align-items: flex-start; }}
            .toolbar-inner {{ flex-direction: column; }}
            .search-box {{ width: 100%; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="header-inner">
            <h1><span>Recomendo</span> Deals Review</h1>
            <div class="stats">
                <div><span class="stat-value" id="totalCount">0</span> products</div>
                <div><span class="stat-value" id="priceCount">0</span> priced</div>
                <div><span class="stat-value" id="dealCount">0</span> deals</div>
                <div>Updated <span class="stat-value" id="updatedAt"></span></div>
            </div>
        </div>
    </div>

    <div class="toolbar">
        <div class="toolbar-inner">
            <input type="text" class="search-box" id="searchBox" placeholder="Search by title, ASIN, or source...">
            <div class="filter-group">
                <button class="filter-btn active" data-filter="all">All</button>
                <button class="filter-btn" data-filter="priced">Has Price</button>
                <button class="filter-btn" data-filter="deals">Deals Only</button>
                <button class="filter-btn" data-filter="below-avg">Below Avg</button>
                <button class="filter-btn" data-filter="near-low">Near Low</button>
            </div>
            <div class="filter-group">
                <button class="filter-btn" data-filter="recomendo">Recomendo</button>
                <button class="filter-btn" data-filter="cooltools">Cool Tools</button>
            </div>
            <div class="filter-group">
                <button class="filter-btn" id="featuredToggle" data-filter="hide-featured">Hide Featured</button>
            </div>
            <select class="sort-select" id="sortSelect">
                <option value="score-desc">Deal Score (best first)</option>
                <option value="near-low-asc">Near 90-Day Low</option>
                <option value="proven-desc">Proven Sellers (DI revenue)</option>
                <option value="rev-per-unit-desc">Revenue per Unit</option>
                <option value="hidden-gems">Hidden Gems</option>
                <option value="pct-off-list">% Off List Price</option>
                <option value="savings-desc">Savings % (high to low)</option>
                <option value="savings-asc">Savings % (low to high)</option>
                <option value="price-asc">Price (low to high)</option>
                <option value="price-desc">Price (high to low)</option>
                <option value="dollars-desc">$ Saved (most first)</option>
                <option value="below-high-desc">% Below High</option>
                <option value="title-asc">Title (A-Z)</option>
                <option value="oldest-featured">Longest Since Featured</option>
                <option value="date-desc">Newest First</option>
                <option value="bestseller-desc">Bestseller (most sold)</option>
                <option value="random">Random</option>
            </select>
        </div>
    </div>

    <div class="container">
        <div class="results-bar">
            <span id="resultsText">Loading...</span>
            <span id="priceRange"></span>
        </div>
        <div class="grid" id="grid"></div>
        <div class="empty-state" id="emptyState" style="display:none;">
            <h2>No deals match your filters</h2>
            <p>Try adjusting your search or filter criteria</p>
        </div>
    </div>

    <div class="selection-bar" id="selectionBar">
        <div class="selection-bar-inner">
            <span class="count"><span id="selectedCount">0</span> deals selected</span>
            <div class="actions">
                <button class="btn-secondary" onclick="clearSelection()">Clear</button>
                <button class="btn-secondary" onclick="exportSelection()">Copy ASINs</button>
                <button class="btn-confirm" id="confirmBtn" onclick="confirmAndSend()">Review &amp; Edit &#8594;</button>
            </div>
        </div>
    </div>

<script>
const DEALS_DATA = {products_json};
const FEATURED_HISTORY = {history_json};
const CATALOG_BENEFITS = {benefits_json};
const COOLDOWN_DAYS = {COOLDOWN_DAYS};

let allDeals = [];
let activeFilter = 'all';
let activeSourceFilter = null;
let hideFeatured = false;
let selectedAsins = new Set();
let cardEdits = {{}};       // {{asin: {{title: '...', benefit: '...'}}}}
let openEditPanels = new Set();

function init() {{
    const today = new Date();
    for (const [asin, deal] of Object.entries(DEALS_DATA)) {{
        deal.asin = asin;

        // Compute cooldown
        const lastFeatured = FEATURED_HISTORY[asin];
        if (lastFeatured) {{
            const lastDate = new Date(lastFeatured);
            deal._daysSince = Math.floor((today - lastDate) / 86400000);
            deal._inCooldown = deal._daysSince < COOLDOWN_DAYS;
        }} else {{
            deal._daysSince = null;
            deal._inCooldown = false;
        }}

        allDeals.push(deal);
    }}

    document.getElementById('totalCount').textContent = allDeals.length;
    document.getElementById('priceCount').textContent = allDeals.filter(d => d.current_price).length;
    document.getElementById('dealCount').textContent = allDeals.filter(d => d.is_deal).length;
    document.getElementById('updatedAt').textContent = formatDate('{generated_at}');

    document.getElementById('searchBox').addEventListener('input', render);
    document.getElementById('sortSelect').addEventListener('change', render);

    document.querySelectorAll('.filter-btn').forEach(btn => {{
        btn.addEventListener('click', () => {{
            const filter = btn.dataset.filter;
            if (filter === 'hide-featured') {{
                hideFeatured = !hideFeatured;
                btn.classList.toggle('active', hideFeatured);
                btn.textContent = hideFeatured ? 'Show Featured' : 'Hide Featured';
            }} else if (filter === 'recomendo' || filter === 'cooltools') {{
                if (activeSourceFilter === filter) {{
                    activeSourceFilter = null;
                    btn.classList.remove('active');
                }} else {{
                    document.querySelectorAll('.filter-btn[data-filter="recomendo"], .filter-btn[data-filter="cooltools"]').forEach(b => b.classList.remove('active'));
                    activeSourceFilter = filter;
                    btn.classList.add('active');
                }}
            }} else {{
                document.querySelectorAll('.filter-btn[data-filter="all"], .filter-btn[data-filter="priced"], .filter-btn[data-filter="deals"], .filter-btn[data-filter="below-avg"], .filter-btn[data-filter="near-low"]').forEach(b => b.classList.remove('active'));
                activeFilter = filter;
                btn.classList.add('active');
            }}
            render();
        }});
    }});

    render();
}}

function formatDate(iso) {{
    if (!iso) return 'N/A';
    try {{
        const d = new Date(iso);
        return d.toLocaleDateString('en-US', {{ month: 'short', day: 'numeric', year: 'numeric' }});
    }} catch {{
        return iso;
    }}
}}

function getSource(deal) {{
    const issues = deal.issues || [];
    if (issues.length === 0) return null;
    if (issues.some(i => i.source === 'cooltools')) return 'cooltools';
    return 'recomendo';
}}

function getSourceLabel(deal) {{
    const issues = deal.issues || [];
    if (!issues.length) return '';
    const recomendo = issues.filter(i => i.source !== 'cooltools');
    const cooltools = issues.filter(i => i.source === 'cooltools');
    if (recomendo.length) {{
        return `<a href="${{recomendo[0].url}}" target="_blank" onclick="event.stopPropagation()">Recomendo</a>`;
    }}
    if (cooltools.length) {{
        return `<a href="${{cooltools[0].url}}" target="_blank" onclick="event.stopPropagation()">Cool Tools</a>`;
    }}
    return '';
}}

function getAffiliateUrl(deal) {{
    const aff = deal.affiliate_url;
    if (!aff) return `https://amazon.com/dp/${{deal.asin}}`;
    if (typeof aff === 'string') return aff;
    if (typeof aff === 'object') {{
        const code = aff.code || '';
        const domain = aff.domain || 'geni.us';
        return code ? `https://${{domain}}/${{code}}` : `https://amazon.com/dp/${{deal.asin}}`;
    }}
    return `https://amazon.com/dp/${{deal.asin}}`;
}}

function getAffiliateGroup(deal) {{
    const issues = deal.issues || [];
    if (!issues.length) return 'Recomendo';
    const url = issues[0].url || '';
    const mapping = {{
        'recomendo.substack.com': 'Recomendo',
        'kk.org/cooltools': 'Recomendo',
        'bookfreak.substack.com': 'Book Freak',
        'booksthatbelongonpaper.substack.com': 'Books-on-Paper',
        'nomadico.substack.com': 'Nomadico',
        'toolsforpossibilities.substack.com': 'Possibilities-Tools',
        'garstips.substack.com': 'Tips Tools Shoptales',
        'whatsinmynow.substack.com': 'Whats in my NOW',
    }};
    for (const [pattern, group] of Object.entries(mapping)) {{
        if (url.includes(pattern)) return group;
    }}
    return 'Recomendo';
}}

function filterDeals() {{
    const query = document.getElementById('searchBox').value.toLowerCase().trim();

    return allDeals.filter(deal => {{
        if (query) {{
            const searchable = [
                deal.title || '',
                deal.catalog_title || '',
                deal.asin || '',
                ...(deal.issues || []).map(i => i.title || ''),
            ].join(' ').toLowerCase();
            if (!searchable.includes(query)) return false;
        }}

        if (activeFilter === 'priced' && !deal.current_price) return false;
        if (activeFilter === 'deals' && !deal.is_deal) return false;
        if (activeFilter === 'below-avg' && (deal.percent_below_avg || 0) <= 0) return false;
        if (activeFilter === 'near-low' && (deal.near_low_pct === null || deal.near_low_pct === undefined || deal.near_low_pct > 5)) return false;

        if (activeSourceFilter) {{
            const source = getSource(deal);
            if (activeSourceFilter === 'recomendo' && source === 'cooltools') return false;
            if (activeSourceFilter === 'cooltools' && source !== 'cooltools') return false;
        }}

        if (hideFeatured && deal._daysSince !== null) return false;

        return true;
    }});
}}

function sortDeals(deals) {{
    const sort = document.getElementById('sortSelect').value;
    const sorted = [...deals];

    switch (sort) {{
        case 'score-desc':
            sorted.sort((a, b) => (b.deal_score || 0) - (a.deal_score || 0));
            break;
        case 'near-low-asc':
            sorted.sort((a, b) => {{
                const aVal = a.near_low_pct !== null && a.near_low_pct !== undefined ? a.near_low_pct : 9999;
                const bVal = b.near_low_pct !== null && b.near_low_pct !== undefined ? b.near_low_pct : 9999;
                return aVal - bVal;
            }});
            break;
        case 'proven-desc':
            sorted.sort((a, b) => (b.sales_revenue || 0) - (a.sales_revenue || 0));
            break;
        case 'rev-per-unit-desc':
            sorted.sort((a, b) => {{
                const aRpu = a.sales_qty ? (a.sales_revenue || 0) / a.sales_qty : 0;
                const bRpu = b.sales_qty ? (b.sales_revenue || 0) / b.sales_qty : 0;
                return bRpu - aRpu;
            }});
            break;
        case 'hidden-gems':
            sorted.sort((a, b) => {{
                const aGem = (a.is_deal ? 1 : 0) + (a.sales_qty > 0 ? 1 : 0) + (a.first_featured < '2024-01-01' ? 1 : 0);
                const bGem = (b.is_deal ? 1 : 0) + (b.sales_qty > 0 ? 1 : 0) + (b.first_featured < '2024-01-01' ? 1 : 0);
                if (bGem !== aGem) return bGem - aGem;
                return (b.sales_revenue || 0) - (a.sales_revenue || 0);
            }});
            break;
        case 'pct-off-list':
            sorted.sort((a, b) => {{
                const aPct = a.list_price && a.current_price && a.list_price > a.current_price
                    ? ((a.list_price - a.current_price) / a.list_price) * 100 : 0;
                const bPct = b.list_price && b.current_price && b.list_price > b.current_price
                    ? ((b.list_price - b.current_price) / b.list_price) * 100 : 0;
                return bPct - aPct;
            }});
            break;
        case 'savings-desc':
            sorted.sort((a, b) => (b.percent_below_avg || 0) - (a.percent_below_avg || 0));
            break;
        case 'savings-asc':
            sorted.sort((a, b) => (a.percent_below_avg || 0) - (b.percent_below_avg || 0));
            break;
        case 'price-asc':
            sorted.sort((a, b) => (a.current_price || 999) - (b.current_price || 999));
            break;
        case 'price-desc':
            sorted.sort((a, b) => (b.current_price || 0) - (a.current_price || 0));
            break;
        case 'dollars-desc':
            sorted.sort((a, b) => (b.savings_dollars || 0) - (a.savings_dollars || 0));
            break;
        case 'below-high-desc':
            sorted.sort((a, b) => (b.percent_below_high || 0) - (a.percent_below_high || 0));
            break;
        case 'title-asc':
            sorted.sort((a, b) => (a.title || a.catalog_title || '').localeCompare(b.title || b.catalog_title || ''));
            break;
        case 'oldest-featured':
            sorted.sort((a, b) => (a.first_featured || 'zzzz').localeCompare(b.first_featured || 'zzzz'));
            break;
        case 'date-desc':
            sorted.sort((a, b) => (b.first_featured || '').localeCompare(a.first_featured || ''));
            break;
        case 'bestseller-desc':
            sorted.sort((a, b) => (b.sales_qty || 0) - (a.sales_qty || 0));
            break;
        case 'random':
            for (let i = sorted.length - 1; i > 0; i--) {{
                const j = Math.floor(Math.random() * (i + 1));
                [sorted[i], sorted[j]] = [sorted[j], sorted[i]];
            }}
            break;
    }}
    return sorted;
}}

function renderCard(deal) {{
    const fullTitle = deal.title || deal.catalog_title || deal.asin;
    const price = deal.current_price;
    const avg = deal.avg_90_day;
    const listPrice = deal.list_price;
    const high90 = deal.high_90_day;
    const pctBelow = deal.percent_below_avg || 0;
    const imgUrl = deal.image_url || '';
    const buyUrl = getAffiliateUrl(deal);
    const isSelected = selectedAsins.has(deal.asin);
    const isAtLow = deal.low_90_day && price && price <= deal.low_90_day;
    const availability = deal.availability || '';
    const paPrice = deal.pa_current_price;
    const savingsPct = deal.savings_percent;

    const dealScore = deal.deal_score || 0;
    const rating = deal.rating;
    const reviewCount = deal.review_count;

    let badges = '';
    if (availability === 'OUT_OF_STOCK') badges += '<span class="badge badge-unavailable">Out of Stock</span>';
    if (dealScore >= 70) badges += '<span class="badge badge-deal">Top Deal</span>';
    else if (deal.is_deal) badges += '<span class="badge badge-deal">Deal</span>';
    if (listPrice && price && listPrice > price) {{
        const offPct = Math.round(((listPrice - price) / listPrice) * 100);
        if (offPct >= 5) badges += `<span class="badge badge-list">${{offPct}}% off list</span>`;
    }} else if (high90 && price && high90 > price) {{
        const offPct = Math.round(((high90 - price) / high90) * 100);
        if (offPct >= 5) badges += `<span class="badge badge-list">${{offPct}}% off recent high</span>`;
    }}
    if (pctBelow > 0) badges += `<span class="badge badge-savings">${{pctBelow.toFixed(0)}}% below avg</span>`;
    if (isAtLow) badges += '<span class="badge badge-low">90-day low</span>';
    if (paPrice && price && Math.abs(paPrice - price) / price > 0.05) {{
        badges += `<span class="badge badge-discrepancy">PA: $${{paPrice.toFixed(2)}}</span>`;
    }}
    if (deal.price_source === 'buy_box_prime') badges += '<span class="badge badge-prime"><a href="https://amzn.to/4c7wkNg" target="_blank" style="color:inherit;text-decoration:none">Prime exclusive deal</a></span>';
    if (deal.price_source === 'new_3rd_party') badges += '<span class="badge" style="background:#fef3c7;color:#d97706">3rd party seller</span>';
    if (deal._inCooldown) badges += `<span class="badge badge-cooldown">Featured ${{deal._daysSince}}d ago</span>`;

    // Rating and review count
    let ratingHtml = '';
    if (rating && rating > 0) {{
        const stars = '\u2605';
        ratingHtml = `<span class="card-rating">${{stars}} ${{rating.toFixed(1)}}`;
        if (reviewCount && reviewCount > 0) {{
            ratingHtml += ` (${{reviewCount.toLocaleString()}})`;
        }}
        ratingHtml += '</span>';
    }}

    // Deal score indicator
    let scoreHtml = '';
    if (dealScore > 0) {{
        const scoreColor = dealScore >= 70 ? '#16a34a' : dealScore >= 40 ? '#d97706' : '#999';
        scoreHtml = `<span class="card-score" style="color:${{scoreColor}}">Score: ${{dealScore}}</span>`;
    }}

    // Sales info
    let salesHtml = '';
    if (deal.sales_revenue > 0) {{
        salesHtml = `<span class="card-sales">${{deal.sales_qty.toLocaleString()}} sold · $${{Math.round(deal.sales_revenue).toLocaleString()}} rev</span>`;
    }} else if (deal.sales_qty > 0) {{
        salesHtml = `<span class="card-sales">${{deal.sales_qty.toLocaleString()}} sold</span>`;
    }}

    // Near 90-day low badge
    if (deal.near_low_pct !== null && deal.near_low_pct !== undefined && deal.near_low_pct <= 5) {{
        const label = deal.near_low_pct <= 0 ? 'At 90-day low' : `${{deal.near_low_pct.toFixed(0)}}% above low`;
        badges += `<span class="badge" style="background:#dbeafe;color:#1d4ed8">${{label}}</span>`;
    }}

    const sourceLabel = getSourceLabel(deal);
    const meta = sourceLabel ? `Featured in ${{sourceLabel}}` : '';
    const priceHtml = price ? `$${{price.toFixed(2)}}` : '<span style="color:#999">No price data</span>';
    // Use list_price for strikethrough, then high_90_day, then avg_90_day
    const strikePrice = (listPrice && price && listPrice > price) ? listPrice : ((high90 && price && high90 > price) ? high90 : ((avg && price && avg > price) ? avg : null));
    const origHtml = strikePrice ? `<span class="original">$${{strikePrice.toFixed(2)}}</span>` : '';

    const hasEdits = cardEdits[deal.asin] && (cardEdits[deal.asin].title || cardEdits[deal.asin].benefit);
    const editTitle = (cardEdits[deal.asin] && cardEdits[deal.asin].title) || '';
    const editBenefit = (cardEdits[deal.asin] && cardEdits[deal.asin].benefit) || CATALOG_BENEFITS[deal.asin] || '';
    const panelOpen = openEditPanels.has(deal.asin);

    return `
        <div class="card ${{isSelected ? 'selected' : ''}} ${{deal._inCooldown ? 'cooldown' : ''}} ${{hasEdits ? 'has-edits' : ''}}" data-asin="${{deal.asin}}">
            <div class="card-top" onclick="toggleSelect('${{deal.asin}}', event)">
                <div class="card-image">
                    ${{imgUrl ? `<a href="${{buyUrl}}" target="_blank" onclick="event.stopPropagation()"><img src="${{imgUrl}}" alt="" loading="lazy"></a>` : ''}}
                </div>
                <div class="card-body">
                    <div class="card-title-row">
                        <div class="card-title"><a href="${{buyUrl}}" target="_blank" onclick="event.stopPropagation()">${{escapeHtml(editTitle || fullTitle)}}</a></div>
                        <a href="https://amazon.com/dp/${{deal.asin}}" target="_blank" class="card-link" onclick="event.stopPropagation()" title="View on Amazon">&#8599;</a>
                    </div>
                    <div class="card-price">${{priceHtml}}${{origHtml}}</div>
                    <div class="card-badges">${{badges}}</div>
                    ${{(ratingHtml || scoreHtml || salesHtml) ? `<div class="card-extra">${{ratingHtml}}${{scoreHtml}}${{salesHtml}}</div>` : ''}}
                    <div class="card-meta">${{meta}}</div>
                </div>
                <div class="card-actions-col" onclick="event.stopPropagation()">
                    <input type="checkbox" class="card-checkbox" ${{isSelected ? 'checked' : ''}} onclick="toggleSelect('${{deal.asin}}', event)">
                    <button class="card-edit-toggle" onclick="toggleEditPanel('${{deal.asin}}', event)" title="Edit title &amp; benefit">&#9998;</button>
                    <button class="card-hide-btn" onclick="hideProduct('${{deal.asin}}', event)" title="Hide for 30 days">&#10005;</button>
                </div>
            </div>
            <div class="card-edit-panel ${{panelOpen ? 'open' : ''}}" onclick="event.stopPropagation()">
                <div class="edit-field">
                    <div class="edit-label">Title</div>
                    <div class="edit-row">
                        <input type="text" value="${{escapeHtml(editTitle || fullTitle)}}" placeholder="Product title" oninput="saveCardEdit('${{deal.asin}}', 'title', this.value)" data-edit-title="${{deal.asin}}">
                        <button class="edit-btn" onclick="inlineShortenTitle('${{deal.asin}}')">Shorten</button>
                    </div>
                </div>
                <div class="edit-field">
                    <div class="edit-label">Benefit Description</div>
                    <div class="edit-row">
                        <textarea placeholder="Why this product is great..." oninput="saveCardEdit('${{deal.asin}}', 'benefit', this.value)" data-edit-benefit="${{deal.asin}}">${{escapeHtml(editBenefit)}}</textarea>
                        <button class="edit-btn" onclick="inlineGenerateBenefit('${{deal.asin}}')" data-gen-btn="${{deal.asin}}">Generate</button>
                    </div>
                </div>
            </div>
        </div>
    `;
}}

function escapeHtml(str) {{
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
}}

function toggleEditPanel(asin, event) {{
    event.stopPropagation();
    if (openEditPanels.has(asin)) {{
        openEditPanels.delete(asin);
    }} else {{
        openEditPanels.add(asin);
    }}
    // Toggle panel without full re-render to preserve input focus
    const card = document.querySelector(`.card[data-asin="${{asin}}"]`);
    if (card) {{
        const panel = card.querySelector('.card-edit-panel');
        if (panel) panel.classList.toggle('open');
    }}
}}

function hideProduct(asin, event) {{
    event.stopPropagation();

    fetch('/hide', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ asin, days: 30 }})
    }})
    .then(r => r.json())
    .then(data => {{
        if (data.success) {{
            allDeals = allDeals.filter(d => d.asin !== asin);
            selectedAsins.delete(asin);
            render();
        }} else {{
            alert('Error hiding product: ' + (data.error || 'Unknown'));
        }}
    }})
    .catch(err => alert('Error: ' + err));
}}

function saveCardEdit(asin, field, value) {{
    if (!cardEdits[asin]) cardEdits[asin] = {{}};
    cardEdits[asin][field] = value;
}}

function inlineShortenTitle(asin) {{
    const input = document.querySelector(`[data-edit-title="${{asin}}"]`);
    if (!input) return;
    const deal = allDeals.find(d => d.asin === asin);
    if (!deal) return;
    // Use shorten_title imported from review_deals (via the edit page endpoint)
    // For now, do a simple client-side shortening: strip brand prefix, trim parenthetical suffixes
    let title = input.value || deal.live_title || '';
    // Remove common trailing parenthetical details
    title = title.replace(/\\s*\\([^)]*\\)\\s*$/g, '').replace(/\\s*,\\s*[^,]{{0,30}}$/g, '').trim();
    // Truncate to ~60 chars on word boundary
    if (title.length > 60) {{
        title = title.substring(0, 57).replace(/\\s+\\S*$/, '') + '...';
    }}
    input.value = title;
    saveCardEdit(asin, 'title', title);
}}

async function inlineGenerateBenefit(asin) {{
    const btn = document.querySelector(`[data-gen-btn="${{asin}}"]`);
    const textarea = document.querySelector(`[data-edit-benefit="${{asin}}"]`);
    if (!btn || !textarea) return;

    const deal = allDeals.find(d => d.asin === asin);
    const title = (cardEdits[asin] && cardEdits[asin].title) || (deal && deal.live_title) || asin;

    btn.disabled = true;
    btn.textContent = 'Generating...';
    btn.classList.add('generating');

    try {{
        const resp = await fetch('/generate-benefit', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ asin, title }})
        }});
        const result = await resp.json();
        if (result.success && result.benefit) {{
            textarea.value = result.benefit;
            saveCardEdit(asin, 'benefit', result.benefit);
        }} else {{
            textarea.value = result.error || 'Could not generate benefit';
        }}
    }} catch (err) {{
        textarea.value = 'Error: ' + err.message;
    }} finally {{
        btn.disabled = false;
        btn.textContent = 'Generate';
        btn.classList.remove('generating');
    }}
}}

function render() {{
    const filtered = filterDeals();
    const sorted = sortDeals(filtered);
    const grid = document.getElementById('grid');
    const empty = document.getElementById('emptyState');

    if (sorted.length === 0) {{
        grid.innerHTML = '';
        empty.style.display = 'block';
    }} else {{
        empty.style.display = 'none';
        grid.innerHTML = sorted.map(renderCard).join('');
    }}

    const dealCount = sorted.filter(d => d.is_deal).length;
    document.getElementById('resultsText').textContent =
        `Showing ${{sorted.length}} products (${{dealCount}} deals)`;

    const prices = sorted.filter(d => d.current_price).map(d => d.current_price);
    if (prices.length) {{
        const min = Math.min(...prices);
        const max = Math.max(...prices);
        document.getElementById('priceRange').textContent = `$${{min.toFixed(2)}} - $${{max.toFixed(2)}}`;
    }}

    updateSelectionBar();
}}

function toggleSelect(asin, event) {{
    event.stopPropagation();
    if (selectedAsins.has(asin)) {{
        selectedAsins.delete(asin);
    }} else {{
        selectedAsins.add(asin);
    }}
    render();
}}

function clearSelection() {{
    selectedAsins.clear();
    render();
}}

function updateSelectionBar() {{
    const bar = document.getElementById('selectionBar');
    const count = selectedAsins.size;
    document.getElementById('selectedCount').textContent = count;
    bar.classList.toggle('visible', count > 0);
}}

function exportSelection() {{
    const asins = Array.from(selectedAsins).join('\\n');
    navigator.clipboard.writeText(asins).then(() => {{
        const btn = event.target;
        const original = btn.textContent;
        btn.textContent = 'Copied!';
        setTimeout(() => btn.textContent = original, 1500);
    }});
}}

function confirmAndSend() {{
    const selected = Array.from(selectedAsins);
    if (selected.length === 0) {{
        alert('Please select at least one deal');
        return;
    }}

    const btn = document.getElementById('confirmBtn');
    btn.disabled = true;
    btn.textContent = 'Loading...';

    // Navigate to edit page with selected ASINs + any inline edits
    fetch('/edit', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ asins: selected, edits: cardEdits }})
    }})
    .then(r => r.text())
    .then(html => {{
        document.open();
        document.write(html);
        document.close();
    }})
    .catch(err => {{
        alert('Error: ' + err);
        btn.disabled = false;
        btn.textContent = 'Review & Edit';
    }});
}}

document.addEventListener('DOMContentLoaded', init);
</script>
</body>
</html>"""


def build_edit_html(selected_asins: list, products: dict, inline_edits: dict = None) -> str:
    """Build the interim editing page for selected deals."""
    inline_edits = inline_edits or {}
    # Build items data for the edit page
    items = []
    for asin in selected_asins:
        p = products.get(asin, {})
        full_title = p.get("title", asin)
        edits = inline_edits.get(asin, {})
        # Use catalog short_title if it's a real product-specific shortening.
        # Skip it if it equals the original catalog/article title with no word
        # overlap to the Amazon title — that means it's an article title like
        # "Gifts for the Cook", not an actual product description.
        catalog_short = p.get("short_title", "")
        catalog_title_orig = p.get("catalog_title", "")
        if catalog_short and catalog_short != full_title:
            if catalog_short != catalog_title_orig:
                short = catalog_short  # Was explicitly customized — keep it
            else:
                # catalog_short equals the article title; only keep it if it
                # shares at least one meaningful word with the Amazon title
                skip_words = {'the', 'a', 'an', 'and', 'or', 'for', 'of', 'in',
                              'on', 'to', 'with', 'by', 'at', 'is', 'its', 'be'}
                words_cs = {w.lower().strip('.,!?-') for w in catalog_short.split()
                            if len(w) > 2} - skip_words
                words_ft = {w.lower().strip('.,!?-') for w in full_title.split()
                            if len(w) > 2} - skip_words
                short = catalog_short if (words_cs & words_ft) else shorten_title(full_title)
        else:
            short = shorten_title(full_title)
        # Apply inline edits; if no edits, default to short title for display
        item_title = edits.get("title") or (short if short != full_title else full_title)
        item_benefit = edits.get("benefit") or p.get("benefit_description", "")
        items.append({
            "asin": asin,
            "title": item_title,
            "short_title": short,
            "image_url": p.get("image_url", ""),
            "current_price": p.get("current_price"),
            "avg_90_day": p.get("avg_90_day"),
            "percent_below_avg": p.get("percent_below_avg") or 0,
            "affiliate_url": p.get("affiliate_url", ""),
            "benefit_description": item_benefit,
            "issues": p.get("issues", []),
            "list_price": p.get("list_price"),
            "high_90_day": p.get("high_90_day"),
            "availability": p.get("availability", ""),
            "savings_percent": p.get("savings_percent"),
            "price_source": p.get("price_source"),
        })

    items_json = json.dumps(items)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Edit Deals - Recomendo</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f0f2f5;
            color: #363737;
            line-height: 1.5;
        }}
        .header {{
            background: #fff;
            border-bottom: 1px solid #e0e0e0;
            padding: 16px 24px;
            position: sticky;
            top: 0;
            z-index: 100;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }}
        .header-inner {{
            max-width: 800px;
            margin: 0 auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .header h1 {{
            font-size: 20px;
            font-weight: 700;
            color: #363737;
        }}
        .header h1 span {{ color: #4384F3; }}
        .header-actions {{
            display: flex;
            gap: 10px;
            align-items: center;
        }}
        .btn {{
            padding: 8px 20px;
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.15s;
        }}
        .btn-back {{
            background: #eee;
            color: #666;
        }}
        .btn-back:hover {{ background: #ddd; }}
        .btn-send {{
            background: #27ae60;
            color: #fff;
            font-size: 15px;
            padding: 10px 28px;
        }}
        .btn-send:hover {{ background: #219a52; }}
        .btn-send:disabled {{ background: #999; cursor: not-allowed; }}
        .btn-verify {{
            background: #4384F3;
            color: #fff;
        }}
        .btn-verify:hover {{ background: #2b74f1; }}
        .btn-verify:disabled {{ background: #999; cursor: not-allowed; }}
        .verify-banner {{
            max-width: 800px;
            margin: 0 auto 16px;
            padding: 12px 20px;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 500;
            display: none;
        }}
        .verify-banner.success {{ display: block; background: #dcfce7; color: #16a34a; }}
        .verify-banner.warning {{ display: block; background: #fef3c7; color: #d97706; }}
        .verify-banner.error {{ display: block; background: #fee2e2; color: #dc2626; }}
        .price-changed {{ color: #d97706; font-weight: 600; }}
        .price-updated {{ animation: flash 1s ease; }}
        @keyframes flash {{
            0% {{ background: #fef3c7; }}
            100% {{ background: transparent; }}
        }}
        .container {{
            max-width: 800px;
            margin: 20px auto;
            padding: 0 24px 40px;
        }}
        .deal-card {{
            background: #fff;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 16px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
        }}
        .deal-top {{
            display: flex;
            gap: 16px;
            margin-bottom: 16px;
        }}
        .deal-image {{
            width: 100px;
            height: 100px;
            flex-shrink: 0;
            border-radius: 8px;
            overflow: hidden;
            background: #f5f5f5;
        }}
        .deal-image img {{
            width: 100%;
            height: 100%;
            object-fit: contain;
        }}
        .deal-info {{
            flex: 1;
            min-width: 0;
        }}
        .deal-asin {{
            font-size: 12px;
            color: #999;
            margin-bottom: 2px;
        }}
        .deal-price {{
            font-size: 20px;
            font-weight: 700;
            color: #27ae60;
        }}
        .deal-price .original {{
            font-size: 13px;
            color: #999;
            text-decoration: line-through;
            font-weight: 400;
            margin-left: 6px;
        }}
        .deal-savings {{
            font-size: 13px;
            color: #27ae60;
            font-weight: 600;
        }}
        .deal-source {{
            font-size: 12px;
            color: #888;
            margin-top: 4px;
        }}
        .deal-source a {{ color: #4384F3; text-decoration: none; }}
        .deal-source a:hover {{ text-decoration: underline; }}
        .field-group {{
            margin-bottom: 12px;
        }}
        .field-group:last-child {{
            margin-bottom: 0;
        }}
        .field-label {{
            font-size: 12px;
            font-weight: 600;
            color: #888;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 4px;
        }}
        .field-input {{
            width: 100%;
            padding: 8px 12px;
            border: 1px solid #ddd;
            border-radius: 6px;
            font-size: 14px;
            font-family: inherit;
            outline: none;
            transition: border-color 0.2s;
        }}
        .field-input:focus {{
            border-color: #4384F3;
        }}
        textarea.field-input {{
            min-height: 60px;
            resize: vertical;
            line-height: 1.5;
        }}
        .deal-number {{
            font-size: 13px;
            font-weight: 600;
            color: #999;
            margin-bottom: 12px;
        }}
        .card-actions {{
            display: flex;
            gap: 8px;
            margin-top: 12px;
            padding-top: 12px;
            border-top: 1px solid #eee;
        }}
        .btn-skip {{
            padding: 5px 14px;
            border: 1px solid #ddd;
            border-radius: 5px;
            background: #fff;
            color: #888;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
        }}
        .btn-skip:hover {{ background: #f5f5f5; color: #555; }}
        .btn-delete {{
            padding: 5px 14px;
            border: 1px solid #f5c6cb;
            border-radius: 5px;
            background: #fff;
            color: #dc3545;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
        }}
        .btn-delete:hover {{ background: #fdecea; }}
        .title-row {{
            display: flex;
            gap: 8px;
            align-items: center;
        }}
        .title-row .field-input {{
            flex: 1;
        }}
        .btn-suggest {{
            padding: 6px 12px;
            border: 1px solid #4384F3;
            border-radius: 5px;
            background: #fff;
            color: #4384F3;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            white-space: nowrap;
            flex-shrink: 0;
        }}
        .btn-suggest:hover {{ background: #f0f5ff; }}
        .benefit-row {{
            display: flex;
            gap: 8px;
            align-items: flex-start;
        }}
        .benefit-row textarea {{
            flex: 1;
        }}
        .btn-generate {{
            padding: 6px 12px;
            border: 1px solid #7c3aed;
            border-radius: 5px;
            background: #fff;
            color: #7c3aed;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            white-space: nowrap;
            flex-shrink: 0;
            margin-top: 2px;
        }}
        .btn-generate:hover {{ background: #f5f0ff; }}
        .btn-generate:disabled {{ opacity: 0.5; cursor: not-allowed; }}

        .deal-card {{
            position: relative;
        }}
        .reorder-btns {{
            position: absolute;
            top: 12px;
            right: 12px;
            display: flex;
            flex-direction: column;
            gap: 2px;
        }}
        .reorder-btn {{
            width: 28px;
            height: 24px;
            border: 1px solid #ddd;
            border-radius: 4px;
            background: #fff;
            cursor: pointer;
            font-size: 14px;
            color: #888;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.15s;
            padding: 0;
        }}
        .reorder-btn:hover {{ border-color: #4384F3; color: #4384F3; }}
        .reorder-btn:disabled {{ opacity: 0.25; cursor: default; }}

        /* Modal */
        .modal-overlay {{
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.5);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 1000;
        }}
        .modal {{
            background: #fff;
            padding: 30px;
            border-radius: 12px;
            max-width: 500px;
            text-align: center;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3);
        }}
        .modal h2 {{
            margin: 0 0 10px;
            color: #333;
        }}
        .modal p {{
            color: #666;
            margin-bottom: 20px;
        }}
        .modal a.modal-link {{
            display: inline-block;
            background: #4384F3;
            color: #fff;
            padding: 12px 24px;
            border-radius: 6px;
            text-decoration: none;
            font-weight: 600;
            margin-bottom: 15px;
        }}
        .modal a.modal-link:hover {{ background: #2b74f1; }}
        .modal button {{
            background: #eee;
            border: none;
            padding: 10px 20px;
            border-radius: 6px;
            cursor: pointer;
            color: #666;
        }}

        .unclassified-ad-section {{
            margin-top: 32px;
            padding-top: 24px;
            border-top: 2px dashed #4384F3;
        }}
        .ad-section-title {{
            font-size: 18px;
            color: #4384F3;
            margin-bottom: 4px;
        }}
        .ad-preview-card {{
            background: #f8faff;
            border: 1px solid #d0deff;
            border-radius: 10px;
            padding: 20px;
        }}

        @media (max-width: 600px) {{
            .deal-top {{ flex-direction: column; align-items: center; text-align: center; }}
            .header-inner {{ flex-direction: column; gap: 10px; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="header-inner">
            <h1><span>Edit</span> Selected Deals</h1>
            <div class="header-actions">
                <button class="btn btn-back" onclick="window.location.href='/'">&#8592; Back</button>
                <button class="btn btn-suggest" onclick="generateAllTitles()" style="background:#f0fdf4;border:1px solid #22c55e;color:#16a34a;">AI Title All</button>
                <button class="btn btn-verify" id="verifyBtn" onclick="verifyPrices()">Verify Prices</button>
                <button class="btn btn-send" id="sendBtn" onclick="sendToMailchimp()">Send to Mailchimp &#8594;</button>
            </div>
        </div>
    </div>

    <div class="container">
        <div class="verify-banner" id="verifyBanner"></div>
        <p style="text-align:center;font-size:13px;color:#999;margin-bottom:16px">Use arrows to reorder. Edit titles and descriptions below.</p>
        <div id="dealsList"></div>

        <div class="unclassified-ad-section">
            <h2 class="ad-section-title">Unclassified Ad <span style="font-weight:400;color:#888;font-size:14px">(optional)</span></h2>
            <p style="color:#666;font-size:13px;margin-bottom:12px;">Add a deal too good not to share &mdash; doesn't need to be in the catalog.</p>
            <div style="display:flex;gap:8px;margin-bottom:16px;">
                <input type="text" id="adAsin" class="field-input" placeholder="Enter ASIN (e.g., B0D5CJ41SH)" style="flex:1;font-size:15px;">
                <button class="btn btn-verify" id="lookupBtn" onclick="lookupAsin()">Look Up</button>
            </div>
            <div id="adError" style="display:none;color:#dc2626;background:#fee2e2;padding:8px 12px;border-radius:6px;font-size:13px;margin-bottom:12px;"></div>
            <div id="adPreview" style="display:none;">
                <div class="ad-preview-card">
                    <div style="display:flex;gap:16px;margin-bottom:16px;">
                        <img id="adImage" src="" alt="" style="width:100px;height:100px;object-fit:contain;border-radius:6px;background:#f5f5f5;">
                        <div style="flex:1;">
                            <div style="display:flex;align-items:center;gap:8px;">
                                <span style="font-size:18px;font-weight:700;color:#16a34a;">$</span>
                                <input type="number" step="0.01" id="adPriceInput" class="field-input" style="width:100px;font-size:18px;font-weight:700;color:#16a34a;padding:4px 8px;" placeholder="0.00">
                            </div>
                            <div id="adPriceDisplay" style="font-size:13px;color:#888;margin-top:2px;"></div>
                            <div id="adAvailability" style="font-size:12px;margin-top:4px;"></div>
                        </div>
                    </div>
                    <div class="field-group">
                        <label class="field-label">Title</label>
                        <input type="text" id="adTitle" class="field-input" placeholder="Product title">
                    </div>
                    <div class="field-group">
                        <label class="field-label">Description</label>
                        <textarea id="adDescription" class="field-input" rows="2" placeholder="Why is this deal worth sharing?"></textarea>
                    </div>
                    <div class="field-group">
                        <label class="field-label">Affiliate URL</label>
                        <input type="text" id="adAffiliateUrl" class="field-input" style="color:#888;">
                    </div>
                    <button class="btn" onclick="clearAd()" style="background:#fee2e2;color:#dc2626;border:none;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:13px;margin-top:4px;">Remove Ad</button>
                </div>
            </div>
        </div>
    </div>

<script>
const ITEMS = {items_json};

function getSourceLabel(item) {{
    const issues = item.issues || [];
    if (!issues.length) return '';
    const recomendo = issues.filter(i => i.source !== 'cooltools');
    const cooltools = issues.filter(i => i.source === 'cooltools');
    if (cooltools.length) {{
        return `Reviewed in <a href="${{cooltools[0].url}}" target="_blank">Cool Tools</a>`;
    }}
    if (recomendo.length) {{
        const issue = recomendo[0];
        const title = issue.title || 'Recomendo';
        return `Reviewed in <a href="${{issue.url}}" target="_blank">${{escapeHtml(title)}}</a>`;
    }}
    return '';
}}

function escapeHtml(str) {{
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
}}

function renderDealsList() {{
    const container = document.getElementById('dealsList');
    container.innerHTML = ITEMS.map((item, idx) => {{
        const price = item.current_price;
        const avg = item.avg_90_day;
        const high90 = item.high_90_day;
        const pctBelow = item.percent_below_avg || 0;
        const listPrice = item.list_price;
        const availability = item.availability || '';
        const priceHtml = price ? `$${{price.toFixed(2)}}` : '<span style="color:#999">No price</span>';
        // Show list price strikethrough, then high_90_day, then avg
        const strikePrice = (listPrice && price && listPrice > price) ? listPrice : ((high90 && price && high90 > price) ? high90 : ((avg && price && avg > price) ? avg : null));
        const strikeLabel = (listPrice && price && listPrice > price) ? ' list' : '';
        const origHtml = strikePrice ? `<span class="original">$${{strikePrice.toFixed(2)}}${{strikeLabel}}</span>` : '';
        let savingsHtml = '';
        if (listPrice && price && listPrice > price) {{
            const offPct = Math.round(((listPrice - price) / listPrice) * 100);
            if (offPct >= 5) savingsHtml = `${{offPct}}% off list`;
        }} else if (high90 && price && high90 > price) {{
            const offPct = Math.round(((high90 - price) / high90) * 100);
            if (offPct >= 5) savingsHtml = `${{offPct}}% off recent high`;
        }}
        if (!savingsHtml && pctBelow > 0) savingsHtml = `${{pctBelow.toFixed(0)}}% below avg`;
        const sourceLabel = getSourceLabel(item);
        const affUrl = item.affiliate_url || `https://amazon.com/dp/${{item.asin}}`;

        return `
            <div class="deal-card" data-idx="${{idx}}">
                <div class="reorder-btns">
                    <button class="reorder-btn" onclick="moveItem(${{idx}}, -1)" title="Move up" ${{idx === 0 ? 'disabled' : ''}}>&#9650;</button>
                    <button class="reorder-btn" onclick="moveItem(${{idx}}, 1)" title="Move down" ${{idx === ITEMS.length - 1 ? 'disabled' : ''}}>&#9660;</button>
                </div>
                <div class="deal-number">Deal #${{idx + 1}}</div>
                <div class="deal-top">
                    <div class="deal-image">
                        ${{item.image_url ? `<img src="${{item.image_url}}" alt="" loading="lazy">` : ''}}
                    </div>
                    <div class="deal-info">
                        <div class="deal-asin">${{item.asin}}</div>
                        <div class="deal-price" style="display:flex;align-items:center;gap:6px;">
                            <span style="color:#16a34a;font-weight:700;">$</span>
                            <input type="number" step="0.01" class="field-input price-input" data-asin="${{item.asin}}" value="${{price ? price.toFixed(2) : ''}}" style="width:90px;font-size:16px;font-weight:700;color:#16a34a;padding:2px 6px;" placeholder="0.00">
                            ${{origHtml}}
                        </div>
                        ${{savingsHtml ? `<div class="deal-savings">${{savingsHtml}}</div>` : ''}}
                        ${{item.price_source === 'buy_box_prime' ? '<div class="deal-savings" style="background:#dbeafe;color:#1d4ed8"><a href="https://amzn.to/4c7wkNg" target="_blank" style="color:inherit;text-decoration:none">Prime exclusive deal</a></div>' : ''}}
                        ${{sourceLabel ? `<div class="deal-source">${{sourceLabel}}</div>` : ''}}
                    </div>
                </div>
                ${{availability === 'OUT_OF_STOCK' ? '<div style="background:#fee2e2;color:#dc2626;padding:8px 12px;border-radius:6px;font-size:13px;font-weight:600;margin-bottom:12px;">&#9888; This item is currently out of stock on Amazon</div>' : ''}}
                <div class="field-group">
                    <div class="field-label">Title</div>
                    <div class="title-row">
                        <input type="text" class="field-input title-input" data-asin="${{item.asin}}" value="${{escapeHtml(item.title).replace(/"/g, '&quot;')}}" placeholder="${{escapeHtml(item.short_title || '').replace(/"/g, '&quot;')}}">
                        ${{item.short_title && item.short_title !== item.title ? `<button class="btn-suggest" onclick="useSuggestion(${{idx}})" title="${{escapeHtml(item.short_title).replace(/"/g, '&quot;')}}">Shorten</button>` : ''}}
                        <button class="btn-suggest" onclick="generateTitle(${{idx}})" style="background:#f0fdf4;border-color:#22c55e;color:#16a34a;">AI Title</button>
                    </div>
                </div>
                <div class="field-group">
                    <div class="field-label">Benefit Description</div>
                    <div class="benefit-row">
                        <textarea class="field-input benefit-input" data-asin="${{item.asin}}" placeholder="Describe why this product is great...">${{escapeHtml(item.benefit_description)}}</textarea>
                        <button class="btn-generate" onclick="generateBenefit(${{idx}})" title="Generate using AI">Generate</button>
                    </div>
                </div>
                <div class="field-group">
                    <div class="field-label">Affiliate URL</div>
                    <input type="text" class="field-input affiliate-input" data-asin="${{item.asin}}" value="${{escapeHtml(affUrl).replace(/"/g, '&quot;')}}">
                </div>
                <div class="card-actions">
                    <button class="btn-skip" onclick="skipDeal(${{idx}})">Don't Include</button>
                    <button class="btn-delete" onclick="deleteDeal(${{idx}})">Delete from Catalog</button>
                </div>
            </div>
        `;
    }}).join('');

}}

function moveItem(idx, direction) {{
    const newIdx = idx + direction;
    if (newIdx < 0 || newIdx >= ITEMS.length) return;
    saveEditsToItems();
    const moved = ITEMS.splice(idx, 1)[0];
    ITEMS.splice(newIdx, 0, moved);
    renderDealsList();
    // Scroll the moved card into view
    const cards = document.querySelectorAll('.deal-card');
    if (cards[newIdx]) cards[newIdx].scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
}}

function saveEditsToItems() {{
    document.querySelectorAll('.deal-card').forEach((card, idx) => {{
        const titleInput = card.querySelector('.title-input');
        const benefitInput = card.querySelector('.benefit-input');
        const affInput = card.querySelector('.affiliate-input');
        const priceInput = card.querySelector('.price-input');
        if (idx < ITEMS.length) {{
            ITEMS[idx].title = titleInput.value;
            ITEMS[idx].benefit_description = benefitInput.value;
            ITEMS[idx].affiliate_url = affInput.value;
            if (priceInput && priceInput.value) {{
                ITEMS[idx].current_price = parseFloat(priceInput.value);
            }}
        }}
    }});
}}

function generateBenefit(idx) {{
    saveEditsToItems();
    const item = ITEMS[idx];
    const asin = item.asin;
    const btn = document.querySelectorAll('.btn-generate')[idx];
    if (!btn) return;

    btn.disabled = true;
    btn.textContent = 'Generating...';

    fetch('/generate-benefit', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ asin: asin, title: item.title }})
    }})
    .then(r => r.json())
    .then(data => {{
        // Find the item by ASIN, not index — items may have been reordered/removed
        const currentIdx = ITEMS.findIndex(i => i.asin === asin);
        const currentItem = currentIdx >= 0 ? ITEMS[currentIdx] : null;
        const currentBtn = currentIdx >= 0 ? document.querySelectorAll('.btn-generate')[currentIdx] : btn;
        const currentTextarea = currentIdx >= 0 ? document.querySelectorAll('.benefit-input')[currentIdx] : null;

        if (currentBtn) {{
            currentBtn.disabled = false;
            currentBtn.textContent = 'Generate';
        }}
        if (data.success && data.benefit) {{
            if (currentTextarea) currentTextarea.value = data.benefit;
            if (currentItem) currentItem.benefit_description = data.benefit;
        }} else {{
            alert('Could not generate: ' + (data.error || 'No source article found'));
        }}
    }})
    .catch(err => {{
        const currentIdx = ITEMS.findIndex(i => i.asin === asin);
        const currentBtn = currentIdx >= 0 ? document.querySelectorAll('.btn-generate')[currentIdx] : btn;
        if (currentBtn) {{
            currentBtn.disabled = false;
            currentBtn.textContent = 'Generate';
        }}
        alert('Error: ' + err);
    }});
}}

function useSuggestion(idx) {{
    saveEditsToItems();
    const item = ITEMS[idx];
    if (!item || !item.short_title) return;
    const card = document.querySelectorAll('.deal-card')[idx];
    const input = card ? card.querySelector('.title-input') : null;
    if (input) {{
        input.value = item.short_title;
        input.focus();
    }}
}}

function generateTitle(idx) {{
    saveEditsToItems();
    const item = ITEMS[idx];
    const btn = document.querySelectorAll('.deal-card')[idx]?.querySelectorAll('.btn-suggest');
    const aiBtn = btn ? btn[btn.length - 1] : null;
    if (aiBtn) {{ aiBtn.disabled = true; aiBtn.textContent = '...'; }}

    fetch('/generate-title', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ asin: item.asin, title: item.title }})
    }})
    .then(r => r.json())
    .then(data => {{
        if (data.success && data.title) {{
            const card = document.querySelectorAll('.deal-card')[idx];
            const input = card ? card.querySelector('.title-input') : null;
            if (input) {{
                input.value = data.title;
                ITEMS[idx].title = data.title;
            }}
        }} else {{
            alert('Error: ' + (data.error || 'Unknown'));
        }}
        if (aiBtn) {{ aiBtn.disabled = false; aiBtn.textContent = 'AI Title'; }}
    }})
    .catch(err => {{
        if (aiBtn) {{ aiBtn.disabled = false; aiBtn.textContent = 'AI Title'; }}
        alert('Error: ' + err);
    }});
}}

function generateAllTitles() {{
    const cards = document.querySelectorAll('.deal-card');
    let idx = 0;
    function next() {{
        if (idx >= ITEMS.length) return;
        generateTitle(idx);
        idx++;
        setTimeout(next, 1500);
    }}
    next();
}}

function skipDeal(idx) {{
    saveEditsToItems();
    const item = ITEMS[idx];
    if (!confirm(`Remove "${{item.title}}" from today's newsletter?`)) return;
    ITEMS.splice(idx, 1);
    renderDealsList();
}}

function deleteDeal(idx) {{
    saveEditsToItems();
    const item = ITEMS[idx];
    if (!confirm(`Permanently delete "${{item.title}}" from the catalog?\\n\\nThis cannot be undone.`)) return;

    fetch('/delete', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ asin: item.asin }})
    }})
    .then(r => r.json())
    .then(data => {{
        if (data.success) {{
            ITEMS.splice(idx, 1);
            renderDealsList();
        }} else {{
            alert('Error deleting: ' + data.error);
        }}
    }})
    .catch(err => alert('Error: ' + err));
}}

function verifyPrices() {{
    const btn = document.getElementById('verifyBtn');
    const banner = document.getElementById('verifyBanner');
    btn.disabled = true;
    btn.textContent = 'Checking...';
    banner.className = 'verify-banner';
    banner.textContent = '';

    const asins = ITEMS.map(item => item.asin);

    fetch('/verify-prices', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ asins }})
    }})
    .then(r => r.json())
    .then(data => {{
        btn.disabled = false;
        btn.textContent = 'Verify Prices';

        if (!data.success) {{
            banner.className = 'verify-banner error';
            banner.textContent = 'Error: ' + (data.error || 'Unknown error');
            return;
        }}

        const prices = data.prices || {{}};
        let changedCount = 0;
        let details = [];

        let unavailableCount = 0;

        ITEMS.forEach((item, idx) => {{
            const fresh = prices[item.asin];
            if (!fresh) return;

            if (fresh.changed) {{
                changedCount++;
                const oldP = fresh.old_price ? '$' + fresh.old_price.toFixed(2) : 'N/A';
                const newP = '$' + fresh.current_price.toFixed(2);
                details.push(item.title.substring(0, 40) + ': ' + oldP + ' → ' + newP);
            }}

            if (fresh.availability === 'OUT_OF_STOCK') {{
                unavailableCount++;
                details.push(item.title.substring(0, 40) + ': OUT OF STOCK');
            }}

            // Update ITEMS data
            item.current_price = fresh.current_price;
            if (fresh.list_price != null) item.list_price = fresh.list_price;
            if (fresh.availability) item.availability = fresh.availability;
            if (fresh.avg_90_day != null) item.avg_90_day = fresh.avg_90_day;
            if (fresh.percent_below_avg != null) item.percent_below_avg = fresh.percent_below_avg;
        }});

        // Save current edits then re-render with updated prices
        saveEditsToItems();
        renderDealsList();

        if (unavailableCount > 0 || changedCount > 0) {{
            banner.className = 'verify-banner warning';
            let msg = '';
            if (changedCount > 0) msg += '<strong>' + changedCount + ' price(s) changed.</strong> ';
            if (unavailableCount > 0) msg += '<strong>' + unavailableCount + ' item(s) out of stock.</strong> ';
            msg += details.join('; ');
            banner.innerHTML = msg;
        }} else {{
            banner.className = 'verify-banner success';
            banner.textContent = 'All ' + asins.length + ' prices verified — no changes detected.';
        }}
    }})
    .catch(err => {{
        btn.disabled = false;
        btn.textContent = 'Verify Prices';
        banner.className = 'verify-banner error';
        banner.textContent = 'Error verifying prices: ' + err;
    }});
}}

function sendToMailchimp() {{
    saveEditsToItems();

    const asins = [];
    const titles = {{}};
    const benefits = {{}};
    const affiliateUrls = {{}};
    const priceOverrides = {{}};

    ITEMS.forEach(item => {{
        asins.push(item.asin);
        titles[item.asin] = item.title;
        if (item.benefit_description.trim()) benefits[item.asin] = item.benefit_description.trim();
        if (item.affiliate_url.trim()) affiliateUrls[item.asin] = item.affiliate_url.trim();
        if (item.current_price) priceOverrides[item.asin] = item.current_price;
    }});

    const body = {{ asins, titles, benefits, affiliateUrls, priceOverrides }};
    if (AD_DATA) {{
        body.unclassifiedAd = {{
            asin: AD_DATA.asin,
            title: document.getElementById('adTitle')?.value || AD_DATA.title,
            description: document.getElementById('adDescription')?.value || '',
            image_url: AD_DATA.image_url,
            current_price: parseFloat(document.getElementById('adPriceInput')?.value) || AD_DATA.current_price,
            list_price: AD_DATA.list_price,
            affiliate_url: document.getElementById('adAffiliateUrl')?.value || AD_DATA.affiliate_url,
        }};
    }}

    const btn = document.getElementById('sendBtn');
    btn.disabled = true;
    btn.textContent = 'Sending...';

    fetch('/confirm', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(body)
    }})
    .then(r => r.json())
    .then(data => {{
        if (data.success) {{
            showSuccessModal(data.campaign_url);
        }} else {{
            alert('Error: ' + data.error);
            btn.disabled = false;
            btn.textContent = 'Send to Mailchimp \\u2192';
        }}
    }})
    .catch(err => {{
        alert('Error: ' + err);
        btn.disabled = false;
        btn.textContent = 'Send to Mailchimp \\u2192';
    }});
}}

function showSuccessModal(campaignUrl) {{
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `
        <div class="modal">
            <div style="font-size:48px;margin-bottom:15px;">&#9989;</div>
            <h2>Newsletter Created!</h2>
            <p>Your Mailchimp draft is ready for review.</p>
            <a href="${{campaignUrl}}" target="_blank" class="modal-link">Open in Mailchimp &#8594;</a>
            <br><br>
            <button onclick="this.closest('.modal-overlay').remove()">Close</button>
        </div>
    `;
    document.body.appendChild(overlay);
}}

let AD_DATA = null;

function lookupAsin() {{
    const asinInput = document.getElementById('adAsin');
    const asin = asinInput.value.trim().toUpperCase();
    const errorEl = document.getElementById('adError');
    const previewEl = document.getElementById('adPreview');

    errorEl.style.display = 'none';
    if (!asin || asin.length !== 10) {{
        errorEl.textContent = 'Enter a valid 10-character ASIN.';
        errorEl.style.display = 'block';
        return;
    }}

    const btn = document.getElementById('lookupBtn');
    btn.disabled = true;
    btn.textContent = 'Looking up...';

    fetch('/lookup-asin', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ asin }})
    }})
    .then(r => r.json())
    .then(data => {{
        btn.disabled = false;
        btn.textContent = 'Look Up';

        if (!data.success) {{
            errorEl.textContent = data.error || 'Product not found.';
            errorEl.style.display = 'block';
            previewEl.style.display = 'none';
            AD_DATA = null;
            return;
        }}

        AD_DATA = data;
        document.getElementById('adImage').src = data.image_url || '';
        document.getElementById('adTitle').value = data.title || '';
        document.getElementById('adAffiliateUrl').value = data.affiliate_url || '';

        // Set editable price input
        const priceInput = document.getElementById('adPriceInput');
        if (data.current_price) {{
            priceInput.value = data.current_price.toFixed(2);
        }} else {{
            priceInput.value = '';
        }}

        let priceHtml = '';
        if (data.current_price) {{
            priceHtml = `API price: $${{data.current_price.toFixed(2)}}`;
            if (data.list_price && data.list_price > data.current_price) {{
                const pct = Math.round(((data.list_price - data.current_price) / data.list_price) * 100);
                priceHtml += ` <span style="text-decoration:line-through">$${{data.list_price.toFixed(2)}}</span>`;
                priceHtml += ` (${{pct}}% off)`;
            }}
            priceHtml += ' — edit above if different on Amazon';
        }} else {{
            priceHtml = 'Price unavailable — enter manually above';
        }}
        document.getElementById('adPriceDisplay').innerHTML = priceHtml;

        const availEl = document.getElementById('adAvailability');
        if (data.availability === 'OUT_OF_STOCK') {{
            availEl.innerHTML = '<span style="color:#dc2626;font-weight:600">&#9888; Out of Stock</span>';
        }} else {{
            availEl.innerHTML = '';
        }}

        if (data.in_catalog) {{
            errorEl.textContent = 'Note: This product is already in your catalog.';
            errorEl.style.display = 'block';
            errorEl.style.color = '#d97706';
            errorEl.style.background = '#fef3c7';
        }}

        previewEl.style.display = 'block';
    }})
    .catch(err => {{
        btn.disabled = false;
        btn.textContent = 'Look Up';
        errorEl.textContent = 'Lookup failed: ' + err;
        errorEl.style.display = 'block';
    }});
}}

function clearAd() {{
    AD_DATA = null;
    document.getElementById('adAsin').value = '';
    document.getElementById('adPreview').style.display = 'none';
    document.getElementById('adError').style.display = 'none';
}}

document.addEventListener('DOMContentLoaded', renderDealsList);
</script>
</body>
</html>"""


class ReviewHandler(BaseHTTPRequestHandler):
    """HTTP handler for the review interface."""

    html_content = ""
    candidates = []
    products = {}

    def log_message(self, format, *args):
        pass  # Suppress HTTP logs

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(self.html_content.encode())

    def do_POST(self):
        global server_should_stop

        if self.path == "/edit":
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data)
            selected_asins = data.get("asins", [])
            inline_edits = data.get("edits", {})

            try:
                edit_html = build_edit_html(selected_asins, self.products, inline_edits)
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(edit_html.encode())
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.send_response(500)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(f"Error: {e}".encode())
            return

        if self.path == "/delete":
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data)
            asin = data.get("asin", "")

            try:
                # Remove from products.json
                products_file = PROJECT_ROOT / "catalog" / "products.json"
                with open(products_file, "r", encoding="utf-8") as f:
                    catalog = json.load(f)
                if asin in catalog:
                    del catalog[asin]
                    with open(products_file, "w", encoding="utf-8") as f:
                        json.dump(catalog, f, indent=2, ensure_ascii=False)
                # Also remove from deals.json
                if DEALS_FILE.exists():
                    with open(DEALS_FILE, "r", encoding="utf-8") as f:
                        deals_data = json.load(f)
                    if asin in deals_data.get("deals", {}):
                        del deals_data["deals"][asin]
                        with open(DEALS_FILE, "w", encoding="utf-8") as f:
                            json.dump(deals_data, f, indent=2, ensure_ascii=False)
                # Remove from in-memory products
                if asin in self.products:
                    del self.products[asin]
                print(f"Deleted {asin} from catalog")
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode())
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.send_response(500)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())
            return

        if self.path == "/hide":
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data)
            asin = data.get("asin", "")
            days = data.get("days", 30)

            try:
                hidden = load_hidden_products()
                expiry = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
                hidden[asin] = expiry
                save_hidden_products(hidden)
                # Remove from in-memory products
                if asin in self.products:
                    del self.products[asin]
                print(f"Hidden {asin} until {expiry}")
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "expiry": expiry}).encode())
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.send_response(500)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())
            return

        if self.path == "/verify-prices":
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data)
            asins = data.get("asins", [])

            try:
                print(f"Verifying prices for {len(asins)} products via PA API...")
                changes = {}
                pa_failed_asins = []

                # Try PA API first (faster, real-time)
                try:
                    from pa_api import get_prices_for_asins as pa_get_prices
                    pa_data = pa_get_prices(asins)
                    for asin in asins:
                        info = pa_data.get(asin, {})
                        if "error" in info or not info.get("current_price"):
                            pa_failed_asins.append(asin)
                            continue
                        old_price = (self.products.get(asin) or {}).get("current_price")
                        new_price = info["current_price"]
                        changes[asin] = {
                            "old_price": old_price,
                            "current_price": new_price,
                            "list_price": info.get("list_price"),
                            "availability": info.get("availability"),
                            "changed": old_price is not None and abs((old_price or 0) - new_price) >= 0.01,
                        }
                        # Update in-memory data
                        if asin in self.products:
                            self.products[asin]["current_price"] = new_price
                            if info.get("list_price"):
                                self.products[asin]["list_price"] = info["list_price"]
                            if info.get("availability"):
                                self.products[asin]["availability"] = info["availability"]
                            if info.get("savings_percent"):
                                self.products[asin]["savings_percent"] = info["savings_percent"]
                    if pa_failed_asins:
                        print(f"  PA API missed {len(pa_failed_asins)} ASINs, falling back to Keepa...")
                except Exception as e:
                    print(f"  PA API failed, falling back to Keepa: {e}")
                    pa_failed_asins = asins

                # Fall back to Keepa for any ASINs PA API missed
                if pa_failed_asins:
                    fresh = check_keepa_prices(pa_failed_asins)
                    for asin, price_data in fresh.items():
                        new_price = price_data.get("current_price")
                        if new_price is None:
                            continue
                        old_price = (self.products.get(asin) or {}).get("current_price")
                        avg = price_data.get("avg_price_90") or price_data.get("avg_price")
                        pct = price_data.get("percent_below_avg")
                        savings = price_data.get("savings_dollars")
                        changes[asin] = {
                            "old_price": old_price,
                            "current_price": new_price,
                            "avg_90_day": avg,
                            "percent_below_avg": pct,
                            "savings_dollars": savings,
                            "changed": old_price is not None and abs((old_price or 0) - new_price) >= 0.01,
                        }
                        if asin in self.products:
                            self.products[asin]["current_price"] = new_price
                            if avg:
                                self.products[asin]["avg_90_day"] = avg
                            if pct is not None:
                                self.products[asin]["percent_below_avg"] = pct
                            if savings is not None:
                                self.products[asin]["savings_dollars"] = savings

                num_changed = sum(1 for v in changes.values() if v["changed"])
                unavailable = sum(1 for v in changes.values() if v.get("availability") == "OUT_OF_STOCK")
                print(f"  {num_changed} price(s) changed, {unavailable} unavailable out of {len(changes)} checked")

                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "prices": changes}).encode())
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.send_response(500)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())
            return

        if self.path == "/generate-benefit":
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data)
            asin = data.get("asin", "")
            title = data.get("title", "")

            try:
                print(f"Generating benefit for {asin}: {title[:40]}...")
                catalog = load_full_catalog()
                # Build a deal-like dict with the info generate_benefit_description needs
                product = self.products.get(asin, {})
                deal = {
                    "live_title": title,
                    "title": title,
                    "catalog_title": product.get("catalog_title", title),
                    "issues": product.get("issues", []),
                    "product_features": product.get("product_features", []),
                }
                benefit = generate_benefit_description(asin, deal, catalog)

                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "benefit": benefit}).encode())
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.send_response(500)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())
            return

        if self.path == "/generate-title":
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data)
            full_title = data.get("title", "")

            try:
                import anthropic
                client = anthropic.Anthropic()
                response = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=50,
                    messages=[{"role": "user", "content": f"""Shorten this Amazon product title to a clean, readable name (3-7 words). Keep the brand if it's well-known. Drop model numbers, sizes, colors, marketing fluff, and redundant category words. Just output the short title, nothing else.

Title: {full_title}"""}]
                )
                short = response.content[0].text.strip().strip('"')
                print(f"  AI title: {full_title[:40]}... -> {short}")
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "title": short}).encode())
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.send_response(500)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())
            return

        if self.path == "/lookup-asin":
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data)
            asin = data.get("asin", "").strip().upper()

            if not asin or len(asin) != 10:
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": "Invalid ASIN format. Expected 10 characters."}).encode())
                return

            try:
                # Try PA API first for title, image, features
                from pa_api import get_prices_for_asins
                pa_data = get_prices_for_asins([asin])
                info = pa_data.get(asin, {})

                if not info or "error" in info:
                    self.send_response(200)
                    self.send_header("Content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "error": f"Product not found on Amazon ({asin})."}).encode())
                    return

                # Fall back to Keepa for price if PA API has none
                current_price = info.get("current_price")
                list_price = info.get("list_price")
                price_source = None
                if current_price is None:
                    try:
                        print(f"  PA API has no price for {asin}, trying Keepa...")
                        keepa_data = check_keepa_prices([asin])
                        keepa_info = keepa_data.get(asin, {})
                        if keepa_info.get("current_price"):
                            current_price = keepa_info["current_price"]
                            price_source = keepa_info.get("price_source")
                            # Prefer Keepa list price (MSRP), fall back to avg
                            keepa_list = keepa_info.get("list_price")
                            if keepa_list and keepa_list > current_price:
                                list_price = keepa_list
                            elif keepa_info.get("avg_price") and keepa_info["avg_price"] > current_price:
                                list_price = keepa_info["avg_price"]
                    except Exception as ke:
                        print(f"  Keepa fallback failed: {ke}")

                catalog = load_full_catalog()
                result = {
                    "success": True,
                    "asin": asin,
                    "title": info.get("title", f"Product {asin}"),
                    "current_price": current_price,
                    "list_price": list_price,
                    "price_source": price_source,
                    "image_url": info.get("image_url", ""),
                    "availability": info.get("availability"),
                    "affiliate_url": f"https://www.amazon.com/dp/{asin}?tag=recomendos-20",
                    "in_catalog": asin in catalog,
                }
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": f"Lookup failed: {e}"}).encode())
            return

        if self.path == "/confirm":
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data)

            selected_asins = data.get("asins", [])
            custom_titles = data.get("titles", {})
            custom_benefits = data.get("benefits", {})
            custom_affiliate_urls = data.get("affiliateUrls", {})
            price_overrides = data.get("priceOverrides", {})
            unclassified_ad = data.get("unclassifiedAd")

            try:
                result = generate_and_send(
                    selected_asins, self.candidates,
                    custom_titles, custom_benefits, custom_affiliate_urls,
                    unclassified_ad=unclassified_ad,
                    custom_prices=price_overrides
                )
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())
                server_should_stop = True
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.send_response(500)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())
            return

        # Fallthrough: unrecognized POST path
        self.send_response(404)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": "Not found"}).encode())


def run_server(html: str, candidates: list, products: dict, port: int = 8765):
    global server_should_stop
    server_should_stop = False

    ReviewHandler.html_content = html
    ReviewHandler.candidates = candidates
    ReviewHandler.products = products

    HTTPServer.allow_reuse_address = True
    server = HTTPServer(("localhost", port), ReviewHandler)
    server.timeout = 1

    print(f"\nReview interface ready at http://localhost:{port}")
    print("Select deals and click 'Confirm & Send' when ready.")
    print("Press Ctrl+C to cancel.\n")

    webbrowser.open(f"http://localhost:{port}")

    try:
        while not server_should_stop:
            server.handle_request()
    except KeyboardInterrupt:
        print("\nCancelled.")

    # Give the browser a moment to receive the final response
    import time
    time.sleep(2)
    server.server_close()
    print("\nDone!")


def main():
    parser = argparse.ArgumentParser(description="Review deals and create Mailchimp newsletter")
    parser.add_argument("--port", type=int, default=8765, help="Local server port (default: 8765)")
    args = parser.parse_args()

    print("Loading catalog and deals...")
    data = merge_catalog_and_deals()
    products = data.get("products", {})
    priced = sum(1 for v in products.values() if v.get("current_price"))
    deal_count = sum(1 for v in products.values() if v.get("is_deal"))
    print(f"  {len(products)} products, {priced} with prices, {deal_count} deals")

    print("Preparing candidates...")
    candidates = prepare_candidates(products)
    print(f"  {len(candidates)} candidates ready")

    print("Generating review page...")
    html = build_html(data)

    run_server(html, candidates, products, args.port)


if __name__ == "__main__":
    main()
