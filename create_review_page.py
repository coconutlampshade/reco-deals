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
import json
import sys
import webbrowser
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

PROJECT_ROOT = Path(__file__).parent
DEALS_FILE = PROJECT_ROOT / "catalog" / "deals.json"

sys.path.insert(0, str(PROJECT_ROOT))
from review_deals import (
    generate_and_send, load_full_catalog, generate_benefits_for_deals,
    shorten_title, get_affiliate_group, check_keepa_prices,
)
from generate_report import load_featured_history, COOLDOWN_DAYS, get_media_category

# Server state
server_should_stop = False


def load_deals() -> dict:
    if DEALS_FILE.exists():
        with open(DEALS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


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

    merged = {}
    for asin, product in catalog.items():
        deal = deals.get(asin, {})
        merged[asin] = {
            # Catalog fields
            "title": product.get("title", asin),
            "image_url": deal.get("image_url") or product.get("image_url", ""),
            "issues": product.get("issues", []),
            "affiliate_url": resolve_affiliate_url(product.get("affiliate_url")),
            "amazon_url": product.get("amazon_url", f"https://www.amazon.com/dp/{asin}"),
            "first_featured": product.get("first_featured", ""),
            "catalog_title": product.get("title", ""),
            "benefit_description": product.get("benefit_description", ""),
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
            "has_deal_data": asin in deals,
        }

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
        pct_below = d.get("percent_below_avg") or 0

        deal = {
            "asin": asin,
            "live_price": current_price or None,
            "live_title": d.get("title") or asin,
            "live_image": d.get("image_url", ""),
            "live_list_price": avg_price if avg_price and current_price and avg_price > current_price else current_price or None,
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
        .card-meta {{
            font-size: 12px;
            color: #888;
            margin-top: 4px;
        }}
        .card-meta a {{ color: #4384F3; text-decoration: none; }}
        .card-meta a:hover {{ text-decoration: underline; }}
        .card-checkbox {{
            position: absolute;
            top: 12px;
            right: 12px;
            width: 22px;
            height: 22px;
            cursor: pointer;
            accent-color: #27ae60;
        }}

        /* card-edit removed — editing happens on the /edit page */

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
            </div>
            <div class="filter-group">
                <button class="filter-btn" data-filter="recomendo">Recomendo</button>
                <button class="filter-btn" data-filter="cooltools">Cool Tools</button>
            </div>
            <select class="sort-select" id="sortSelect">
                <option value="savings-desc">Savings % (high to low)</option>
                <option value="savings-asc">Savings % (low to high)</option>
                <option value="price-asc">Price (low to high)</option>
                <option value="price-desc">Price (high to low)</option>
                <option value="dollars-desc">$ Saved (most first)</option>
                <option value="below-high-desc">% Below High</option>
                <option value="title-asc">Title (A-Z)</option>
                <option value="date-desc">Newest First</option>
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
let selectedAsins = new Set();

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
            if (filter === 'recomendo' || filter === 'cooltools') {{
                if (activeSourceFilter === filter) {{
                    activeSourceFilter = null;
                    btn.classList.remove('active');
                }} else {{
                    document.querySelectorAll('.filter-btn[data-filter="recomendo"], .filter-btn[data-filter="cooltools"]').forEach(b => b.classList.remove('active'));
                    activeSourceFilter = filter;
                    btn.classList.add('active');
                }}
            }} else {{
                document.querySelectorAll('.filter-btn[data-filter="all"], .filter-btn[data-filter="priced"], .filter-btn[data-filter="deals"], .filter-btn[data-filter="below-avg"]').forEach(b => b.classList.remove('active'));
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

        if (activeSourceFilter) {{
            const source = getSource(deal);
            if (activeSourceFilter === 'recomendo' && source === 'cooltools') return false;
            if (activeSourceFilter === 'cooltools' && source !== 'cooltools') return false;
        }}

        return true;
    }});
}}

function sortDeals(deals) {{
    const sort = document.getElementById('sortSelect').value;
    const sorted = [...deals];

    switch (sort) {{
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
            sorted.sort((a, b) => (a.catalog_title || a.title || '').localeCompare(b.catalog_title || b.title || ''));
            break;
        case 'date-desc':
            sorted.sort((a, b) => (b.first_featured || '').localeCompare(a.first_featured || ''));
            break;
    }}
    return sorted;
}}

function renderCard(deal) {{
    const fullTitle = deal.catalog_title || deal.title || deal.asin;
    const price = deal.current_price;
    const avg = deal.avg_90_day;
    const pctBelow = deal.percent_below_avg || 0;
    const imgUrl = deal.image_url || '';
    const buyUrl = getAffiliateUrl(deal);
    const isSelected = selectedAsins.has(deal.asin);
    const isAtLow = deal.low_90_day && price && price <= deal.low_90_day;

    let badges = '';
    if (deal.is_deal) badges += '<span class="badge badge-deal">Deal</span>';
    if (pctBelow > 0) badges += `<span class="badge badge-savings">${{pctBelow.toFixed(0)}}% below avg</span>`;
    if (isAtLow) badges += '<span class="badge badge-low">90-day low</span>';
    if (deal._inCooldown) badges += `<span class="badge badge-cooldown">Featured ${{deal._daysSince}}d ago</span>`;

    const sourceLabel = getSourceLabel(deal);
    const meta = sourceLabel ? `Featured in ${{sourceLabel}}` : '';
    const priceHtml = price ? `$${{price.toFixed(2)}}` : '<span style="color:#999">No price data</span>';
    const origHtml = avg && price && avg > price ? `<span class="original">$${{avg.toFixed(2)}}</span>` : '';

    return `
        <div class="card ${{isSelected ? 'selected' : ''}} ${{deal._inCooldown ? 'cooldown' : ''}}" data-asin="${{deal.asin}}">
            <input type="checkbox" class="card-checkbox" ${{isSelected ? 'checked' : ''}} onclick="toggleSelect('${{deal.asin}}', event)">
            <div class="card-top" onclick="toggleSelect('${{deal.asin}}', event)">
                <div class="card-image">
                    ${{imgUrl ? `<a href="${{buyUrl}}" target="_blank" onclick="event.stopPropagation()"><img src="${{imgUrl}}" alt="" loading="lazy"></a>` : ''}}
                </div>
                <div class="card-body">
                    <div class="card-title-row">
                        <div class="card-title"><a href="${{buyUrl}}" target="_blank" onclick="event.stopPropagation()">${{escapeHtml(fullTitle)}}</a></div>
                        <a href="https://amazon.com/dp/${{deal.asin}}" target="_blank" class="card-link" onclick="event.stopPropagation()" title="View on Amazon">&#8599;</a>
                    </div>
                    <div class="card-price">${{priceHtml}}${{origHtml}}</div>
                    <div class="card-badges">${{badges}}</div>
                    <div class="card-meta">${{meta}}</div>
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

    // Navigate to edit page with selected ASINs
    fetch('/edit', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ asins: selected }})
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


def build_edit_html(selected_asins: list, products: dict) -> str:
    """Build the interim editing page for selected deals."""
    # Build items data for the edit page
    items = []
    for asin in selected_asins:
        p = products.get(asin, {})
        items.append({
            "asin": asin,
            "title": p.get("title", asin),
            "image_url": p.get("image_url", ""),
            "current_price": p.get("current_price"),
            "avg_90_day": p.get("avg_90_day"),
            "percent_below_avg": p.get("percent_below_avg") or 0,
            "affiliate_url": p.get("affiliate_url", ""),
            "benefit_description": p.get("benefit_description", ""),
            "issues": p.get("issues", []),
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

        /* Drag handle */
        .deal-card {{
            cursor: grab;
            position: relative;
        }}
        .deal-card.dragging {{
            opacity: 0.5;
            cursor: grabbing;
        }}
        .deal-card.drag-over {{
            border-top: 3px solid #4384F3;
        }}
        .drag-hint {{
            text-align: center;
            font-size: 13px;
            color: #999;
            margin-bottom: 16px;
        }}

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
                <button class="btn btn-verify" id="verifyBtn" onclick="verifyPrices()">Verify Prices</button>
                <button class="btn btn-send" id="sendBtn" onclick="sendToMailchimp()">Send to Mailchimp &#8594;</button>
            </div>
        </div>
    </div>

    <div class="container">
        <div class="verify-banner" id="verifyBanner"></div>
        <p class="drag-hint">Drag to reorder. Edit titles and descriptions below.</p>
        <div id="dealsList"></div>
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
        const pctBelow = item.percent_below_avg || 0;
        const priceHtml = price ? `$${{price.toFixed(2)}}` : '<span style="color:#999">No price</span>';
        const origHtml = avg && price && avg > price ? `<span class="original">$${{avg.toFixed(2)}}</span>` : '';
        const savingsHtml = pctBelow > 0 ? `${{pctBelow.toFixed(0)}}% below avg` : '';
        const sourceLabel = getSourceLabel(item);
        const affUrl = item.affiliate_url || `https://amazon.com/dp/${{item.asin}}`;

        return `
            <div class="deal-card" draggable="true" data-idx="${{idx}}">
                <div class="deal-number">Deal #${{idx + 1}}</div>
                <div class="deal-top">
                    <div class="deal-image">
                        ${{item.image_url ? `<img src="${{item.image_url}}" alt="" loading="lazy">` : ''}}
                    </div>
                    <div class="deal-info">
                        <div class="deal-asin">${{item.asin}}</div>
                        <div class="deal-price">${{priceHtml}}${{origHtml}}</div>
                        ${{savingsHtml ? `<div class="deal-savings">${{savingsHtml}}</div>` : ''}}
                        ${{sourceLabel ? `<div class="deal-source">${{sourceLabel}}</div>` : ''}}
                    </div>
                </div>
                <div class="field-group">
                    <div class="field-label">Title</div>
                    <input type="text" class="field-input title-input" data-asin="${{item.asin}}" value="${{escapeHtml(item.title).replace(/"/g, '&quot;')}}">
                </div>
                <div class="field-group">
                    <div class="field-label">Benefit Description</div>
                    <textarea class="field-input benefit-input" data-asin="${{item.asin}}" placeholder="Describe why this product is great...">${{escapeHtml(item.benefit_description)}}</textarea>
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

    initDragAndDrop();
}}

// Drag and drop reordering
let dragIdx = null;

function initDragAndDrop() {{
    const cards = document.querySelectorAll('.deal-card');
    cards.forEach(card => {{
        card.addEventListener('dragstart', (e) => {{
            dragIdx = parseInt(card.dataset.idx);
            card.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
        }});
        card.addEventListener('dragend', () => {{
            card.classList.remove('dragging');
            document.querySelectorAll('.deal-card').forEach(c => c.classList.remove('drag-over'));
        }});
        card.addEventListener('dragover', (e) => {{
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            card.classList.add('drag-over');
        }});
        card.addEventListener('dragleave', () => {{
            card.classList.remove('drag-over');
        }});
        card.addEventListener('drop', (e) => {{
            e.preventDefault();
            const dropIdx = parseInt(card.dataset.idx);
            if (dragIdx !== null && dragIdx !== dropIdx) {{
                // Save current edits before reorder
                saveEditsToItems();
                const moved = ITEMS.splice(dragIdx, 1)[0];
                ITEMS.splice(dropIdx, 0, moved);
                renderDealsList();
            }}
        }});
    }});
}}

function saveEditsToItems() {{
    document.querySelectorAll('.deal-card').forEach((card, idx) => {{
        const titleInput = card.querySelector('.title-input');
        const benefitInput = card.querySelector('.benefit-input');
        const affInput = card.querySelector('.affiliate-input');
        if (idx < ITEMS.length) {{
            ITEMS[idx].title = titleInput.value;
            ITEMS[idx].benefit_description = benefitInput.value;
            ITEMS[idx].affiliate_url = affInput.value;
        }}
    }});
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

        ITEMS.forEach((item, idx) => {{
            const fresh = prices[item.asin];
            if (!fresh) return;

            if (fresh.changed) {{
                changedCount++;
                const oldP = fresh.old_price ? '$' + fresh.old_price.toFixed(2) : 'N/A';
                const newP = '$' + fresh.current_price.toFixed(2);
                details.push(item.title.substring(0, 40) + ': ' + oldP + ' → ' + newP);
            }}

            // Update ITEMS data
            item.current_price = fresh.current_price;
            if (fresh.avg_90_day != null) item.avg_90_day = fresh.avg_90_day;
            if (fresh.percent_below_avg != null) item.percent_below_avg = fresh.percent_below_avg;
        }});

        // Save current edits then re-render with updated prices
        saveEditsToItems();
        renderDealsList();

        if (changedCount > 0) {{
            banner.className = 'verify-banner warning';
            banner.innerHTML = '<strong>' + changedCount + ' price(s) changed:</strong> ' + details.join('; ');
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

    ITEMS.forEach(item => {{
        asins.push(item.asin);
        titles[item.asin] = item.title;
        if (item.benefit_description.trim()) benefits[item.asin] = item.benefit_description.trim();
        if (item.affiliate_url.trim()) affiliateUrls[item.asin] = item.affiliate_url.trim();
    }});

    const btn = document.getElementById('sendBtn');
    btn.disabled = true;
    btn.textContent = 'Sending...';

    fetch('/confirm', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ asins, titles, benefits, affiliateUrls }})
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

            try:
                edit_html = build_edit_html(selected_asins, self.products)
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

        if self.path == "/verify-prices":
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data)
            asins = data.get("asins", [])

            try:
                print(f"Verifying prices for {len(asins)} products...")
                fresh = check_keepa_prices(asins)

                # Update in-memory products with fresh prices
                changes = {}
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

                    # Update in-memory data for /confirm
                    if asin in self.products:
                        self.products[asin]["current_price"] = new_price
                        if avg:
                            self.products[asin]["avg_90_day"] = avg
                        if pct is not None:
                            self.products[asin]["percent_below_avg"] = pct
                        if savings is not None:
                            self.products[asin]["savings_dollars"] = savings

                num_changed = sum(1 for v in changes.values() if v["changed"])
                print(f"  {num_changed} price(s) changed out of {len(changes)} checked")

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

        if self.path == "/confirm":
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data)

            selected_asins = data.get("asins", [])
            custom_titles = data.get("titles", {})
            custom_benefits = data.get("benefits", {})
            custom_affiliate_urls = data.get("affiliateUrls", {})

            try:
                result = generate_and_send(
                    selected_asins, self.candidates,
                    custom_titles, custom_benefits, custom_affiliate_urls
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


def run_server(html: str, candidates: list, products: dict, port: int = 8765):
    global server_should_stop
    server_should_stop = False

    ReviewHandler.html_content = html
    ReviewHandler.candidates = candidates
    ReviewHandler.products = products

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
