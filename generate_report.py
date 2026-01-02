#!/usr/bin/env python3
"""
Generate deal reports in HTML and plain text formats.

Fetches live prices from Amazon PA API for Associates compliance.

Usage:
    python generate_report.py                    # Generate HTML report
    python generate_report.py --format text      # Generate plain text
    python generate_report.py --top 10           # Limit to top 10 deals
    python generate_report.py --min-savings 10   # Min $10 savings
"""

import argparse
import base64
import json
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


def get_logo_base64() -> tuple[str, str]:
    """Load logo and convert to base64 for embedding in HTML.
    Returns (base64_data, mime_type)
    """
    # Try new Recomendo Deals logo first
    for logo_file, mime in [("recomendo-deals.png", "image/png"),
                            ("recomendo-deals.jpg", "image/jpeg"),
                            ("Recomendo_title_logo.png", "image/png")]:
        logo_path = config.PROJECT_ROOT / logo_file
        if logo_path.exists():
            with open(logo_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8"), mime
    return "", "image/png"


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


def score_deal(deal: dict) -> float:
    """
    Calculate a deal score for ranking.
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


def filter_and_sort_deals(deals: dict, min_savings: float = 0, top_n: int = None) -> list:
    """
    Filter and sort deals by score.

    Returns list of (asin, deal_data) tuples.
    """
    # Filter out deals without prices
    valid_deals = [
        (asin, data) for asin, data in deals.items()
        if data.get("current_price") and data.get("is_deal")
    ]

    # Filter by minimum savings
    if min_savings > 0:
        valid_deals = [
            (asin, data) for asin, data in valid_deals
            if (data.get("savings_dollars") or 0) >= min_savings
        ]

    # Sort by score (highest first)
    valid_deals.sort(key=lambda x: score_deal(x[1]), reverse=True)

    # Limit to top N
    if top_n:
        valid_deals = valid_deals[:top_n]

    return valid_deals


def get_buy_link(deal: dict) -> str:
    """Get the best buy link for a deal (prefer affiliate URL)."""
    return deal.get("affiliate_url") or deal.get("amazon_url") or ""


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


def generate_html_report(deals: list, title: str = "Recomendo Deals", live_prices: dict = None, price_timestamp: str = None) -> str:
    """
    Generate an HTML email report with Recomendo styling.

    Args:
        deals: List of (asin, deal_data) tuples
        title: Report title
        live_prices: Dict of ASIN -> live price info from PA API
        price_timestamp: Timestamp when prices were fetched from PA API
    """
    if live_prices is None:
        live_prices = {}
    today = datetime.now().strftime("%B %d, %Y")

    # Format price timestamp for display (time only if same day per Amazon requirements)
    import time
    if price_timestamp is None:
        price_timestamp = datetime.now()
    tz_name = time.strftime("%Z")
    price_time_str = price_timestamp.strftime(f"%H:%M {tz_name}") if isinstance(price_timestamp, datetime) else price_timestamp
    logo_b64, logo_mime = get_logo_base64()

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
            gap: 15px;
        }}
        .deal:last-child {{
            border-bottom: none;
            margin-bottom: 0;
            padding-bottom: 0;
        }}
        .deal-image {{
            flex-shrink: 0;
            width: 120px;
            height: 120px;
            border-radius: 8px;
            overflow: hidden;
            background-color: #f5f5f5;
        }}
        .deal-image img {{
            width: 100%;
            height: 100%;
            object-fit: contain;
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
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #e0e0e0;
            font-size: 13px;
            color: #666;
            line-height: 1.6;
        }}
        .footer a {{
            color: #4384F3;
            text-decoration: none;
        }}
        .footer a:hover {{
            text-decoration: underline;
        }}
        .footer .copyright {{
            margin-top: 15px;
            font-size: 12px;
            color: #999;
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
        .disclosure a {{
            color: #4384F3;
            text-decoration: none;
        }}
        .disclosure a:hover {{
            text-decoration: underline;
        }}
        .price-timestamp {{
            font-size: 11px;
            color: #999;
            margin-left: 5px;
        }}
        .price-timestamp a {{
            color: #999;
            text-decoration: none;
        }}
        .price-timestamp a:hover {{
            text-decoration: underline;
        }}
        .price-disclaimer {{
            display: none;
            font-size: 11px;
            color: #666;
            background: #f9f9f9;
            padding: 8px;
            border-radius: 4px;
            margin-top: 5px;
        }}
        .price-disclaimer:target {{
            display: block;
        }}
        @media (max-width: 480px) {{
            .deal {{
                flex-direction: column;
            }}
            .deal-image {{
                width: 100%;
                height: 200px;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">
            <img src="data:{logo_mime};base64,{logo_b64}" alt="Recomendo Deals">
        </div>
        <div class="subtitle">{today}</div>

        <div class="intro">
            Today, we've found <strong>{len(deals)}</strong> great deals on things we've previously featured in our <a href="https://recomendo.com">Recomendo newsletter</a>.
        </div>

        <div class="disclosure">
            As an Amazon Associate we earn from qualifying purchases. Prices shown as of {price_time_str} (<a href="#price-disclaimer">details</a>).
        </div>

        <div id="price-disclaimer" class="price-disclaimer">
            Product prices and availability are accurate as of the date/time indicated and are subject to change. Any price and availability information displayed on Amazon at the time of purchase applies to your purchase.
        </div>
"""

    for asin, deal in deals:
        title_text = deal.get("catalog_title") or deal.get("title") or f"Product {asin}"
        image_url = deal.get("image_url") or ""

        # Get live price from PA API (Amazon Associates compliant)
        live_price = live_prices.get(asin, {})

        # Use PA API's detail_page_url when showing PA API prices (compliance requirement)
        # The URL must come from the same API call as the price data
        if live_price.get("detail_page_url"):
            buy_link = live_price["detail_page_url"]
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

        # Build meta info with issue links
        meta_parts = []
        issues = deal.get("issues", [])
        if issues:
            # Link to the first issue
            first_issue = issues[0]
            issue_url = first_issue.get("url", "")
            issue_date = first_issue.get("date", "")
            issue_num = calculate_issue_number(issue_date)

            if issue_url and issue_num:
                meta_parts.append(f'Reviewed in <a href="{issue_url}" target="_blank">Recomendo #{issue_num}</a>')
            elif issue_url:
                meta_parts.append(f'Reviewed in <a href="{issue_url}" target="_blank">Recomendo</a>')

            # Note if recommended multiple times
            if len(issues) > 1:
                meta_parts.append(f"(and {len(issues) - 1} more issue{'s' if len(issues) > 2 else ''})")

        meta_html = " ".join(meta_parts) if meta_parts else ""

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

        html += f"""
        <div class="deal">
            {image_html}
            <div class="deal-content">
                <div class="deal-title">
                    <a href="{buy_link}" target="_blank">{title_text}</a>
                </div>
                {price_html}
                {indicator_html}
                <div class="deal-meta">{meta_html}</div>
                <a href="{buy_link}" class="buy-button" target="_blank">{button_text}</a>
            </div>
        </div>
"""

    html += """
        <div class="footer">
            <p><em>Recomendo is published by Cool Tools Lab, a small company of three people. We also run <a href="https://recomendo.com">Recomendo</a>, the <a href="https://kk.org/cooltools/">Cool Tools website</a>, a <a href="https://www.youtube.com/cooltools">YouTube channel</a> and <a href="https://open.spotify.com/show/5Bx52UzoVrjSp8bsZyNJcI">podcast</a>, and other newsletters, including <a href="https://garstips.substack.com/">Gar's Tips &amp; Tools</a>, <a href="https://nomadico.substack.com/">Nomadico</a>, <a href="https://whatsinmynow.substack.com/">What's in my NOW?</a>, <a href="https://toolsforpossibilities.substack.com/">Tools for Possibilities</a>, <a href="https://booksthatbelongonpaper.substack.com/">Books That Belong On Paper</a>, and <a href="https://bookfreak.substack.com/">Book Freak</a>.</em></p>
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


def main():
    parser = argparse.ArgumentParser(description="Generate deal reports")
    parser.add_argument("--format", choices=["html", "text", "markdown"], default="html",
                        help="Output format (default: html)")
    parser.add_argument("--top", type=int, help="Limit to top N deals")
    parser.add_argument("--min-savings", type=float, default=0,
                        help="Minimum dollar savings to include")
    parser.add_argument("--output", type=str, help="Output file path")
    parser.add_argument("--title", type=str, default="Recomendo Deals",
                        help="Report title")
    parser.add_argument("--no-live-prices", action="store_true",
                        help="Skip fetching live prices from PA API")
    args = parser.parse_args()

    # Load and process deals
    data = load_deals()
    deals_dict = data.get("deals", {})

    print(f"Loaded {len(deals_dict)} deals from {data.get('generated_at', 'unknown')}")

    # Filter and sort
    deals = filter_and_sort_deals(deals_dict, min_savings=args.min_savings, top_n=args.top)
    print(f"After filtering: {len(deals)} deals")

    if not deals:
        print("No deals to report!")
        return

    # Fetch live prices from PA API (Amazon Associates compliant)
    live_prices = {}
    price_timestamp = None
    if not args.no_live_prices:
        asins = [asin for asin, _ in deals]
        price_timestamp = datetime.now()
        live_prices = fetch_live_prices(asins)

    # Generate report
    if args.format == "html":
        report = generate_html_report(deals, args.title, live_prices, price_timestamp)
        ext = "html"
    elif args.format == "markdown":
        report = generate_markdown_report(deals, args.title, live_prices)
        ext = "md"
    else:
        report = generate_text_report(deals, args.title, live_prices)
        ext = "txt"

    # Output
    if args.output:
        output_path = Path(args.output)
    else:
        today = datetime.now().strftime("%Y-%m-%d")
        output_path = config.PROJECT_ROOT / "reports" / f"deals-{today}.{ext}"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"Report saved to: {output_path}")

    # Also print summary with live prices
    print(f"\nTop deals:")
    for asin, deal in deals[:5]:
        title_text = (deal.get("catalog_title") or deal.get("title") or asin)[:45]
        live_price = live_prices.get(asin, {})

        print(f"  - {title_text}")
        if live_price.get("current_price"):
            price_info = format_price(live_price["current_price"])
            if live_price.get("list_price") and live_price["list_price"] > live_price["current_price"]:
                price_info += f" (was {format_price(live_price['list_price'])})"
            print(f"    {price_info}")
        print(f"    {format_deal_indicator(deal)}")


if __name__ == "__main__":
    main()
