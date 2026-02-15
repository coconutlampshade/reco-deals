#!/usr/bin/env python3
"""Sales reporting tool for Amazon Associates data.

Generates markdown reports from Amazon Associates CSV exports with
clear separation of direct (DI) vs non-direct/indirect (NDI) sales.

Usage:
    python3 sales_report.py                     # Full report to stdout
    python3 sales_report.py --save              # Save to reports/sales/
    python3 sales_report.py --top 30            # Top 30 instead of 20
    python3 sales_report.py --tag recomendos-20 # Filter to single tag
    python3 sales_report.py --featured-only     # Only featured products
    python3 sales_report.py --csv path/to/file  # Custom CSV path
"""

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from config import CATALOG_DIR, SALES_CSV, SALES_REPORTS_DIR, EARNINGS_CSV, BOUNTY_CSV
from generate_report import shorten_title

# Amazon Associates commission rates by category (approximate)
COMMISSION_RATES = {
    "Books": 0.045,
    "Kitchen & Dining": 0.045,
    "Kitchen": 0.045,
    "Home & Kitchen": 0.04,
    "Home": 0.04,
    "Beauty & Personal Care": 0.06,
    "Beauty": 0.06,
    "Health & Household": 0.01,
    "Health": 0.01,
    "Health & Baby Care": 0.01,
    "Tools & Home Improvement": 0.03,
    "Tools": 0.03,
    "Electronics": 0.03,
    "Clothing, Shoes & Jewelry": 0.04,
    "Clothing": 0.04,
    "Sports & Outdoors": 0.03,
    "Toys & Games": 0.03,
    "Automotive": 0.045,
    "Garden & Outdoor": 0.03,
    "Patio, Lawn & Garden": 0.03,
    "Office Products": 0.04,
    "Pet Supplies": 0.03,
    "Grocery & Gourmet Food": 0.01,
    "Baby": 0.03,
    "Musical Instruments": 0.03,
    "Industrial & Scientific": 0.03,
    "Arts, Crafts & Sewing": 0.04,
    "Cell Phones & Accessories": 0.03,
    "Computers & Accessories": 0.025,
    "Camera & Photo": 0.025,
    "Video Games": 0.01,
    "Software": 0.05,
    "Handmade": 0.06,
}
DEFAULT_COMMISSION_RATE = 0.04


# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------

def load_csv(path):
    """Load Amazon Associates CSV, skipping the metadata line."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        # Skip metadata line (e.g. "Fee-Orders reports from ...")
        first_line = f.readline()
        if not first_line.startswith("Category"):
            # It was the metadata line, reader starts at header
            pass
        else:
            # No metadata line, seek back
            f.seek(0)

        reader = csv.DictReader(f)
        for row in reader:
            # Normalize fields
            try:
                qty = int(row.get("Qty", 0))
                price = float(row.get("Price($)", 0))
            except (ValueError, TypeError):
                continue
            rows.append({
                "category": row.get("Category", "").strip(),
                "name": row.get("Name", "").strip(),
                "asin": row.get("ASIN", "").strip(),
                "date": row.get("Date", "").strip(),
                "qty": qty,
                "price": price,
                "link_type": row.get("Link Type", "").strip(),
                "tag": row.get("Tag", "").strip(),
                "indirect": row.get("Indirect Sales", "").strip().lower(),
                "device": row.get("Device Type Group", "").strip(),
            })
    return rows


def load_earnings_csv(path):
    """Load Fee-Earnings CSV with actual commission data."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        first_line = f.readline()
        if not first_line.startswith("Category"):
            pass
        else:
            f.seek(0)
        reader = csv.DictReader(f)
        for row in reader:
            try:
                price = float(row.get("Price($)", 0) or 0)
                shipped = int(row.get("Items Shipped", 0) or 0)
                returns = int(row.get("Returns", 0) or 0)
                revenue = float(row.get("Revenue($)", 0) or 0)
                fee = float(row.get("Ad Fees($)", 0) or 0)
            except (ValueError, TypeError):
                continue
            rows.append({
                "category": row.get("Category", "").strip(),
                "name": row.get("Name", "").strip(),
                "asin": row.get("ASIN", "").strip(),
                "seller": row.get("Seller", "").strip(),
                "tag": row.get("Tracking ID", "").strip(),
                "date": row.get("Date Shipped", "").strip(),
                "price": price,
                "shipped": shipped,
                "returns": returns,
                "revenue": revenue,
                "fee": fee,
                "device": row.get("Device Type Group", "").strip(),
            })
    return rows


def load_bounty_csv(path):
    """Load Bounty CSV with program earnings."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        first_line = f.readline()
        if not first_line.startswith("Name"):
            pass
        else:
            f.seek(0)
        reader = csv.DictReader(f)
        for row in reader:
            try:
                qty = int(row.get("Quantity", 0) or 0)
                fee = float(row.get("Ad Fees($)", 0) or 0)
            except (ValueError, TypeError):
                continue
            rows.append({
                "name": row.get("Name", "").strip(),
                "date": row.get("Date Shipped", "").strip(),
                "tag": row.get("Tracking Id", "").strip(),
                "qty": qty,
                "fee": fee,
            })
    return rows


def deduplicate_rows(rows):
    """Remove exact duplicate rows by 6-tuple key.

    Same ASIN selling through different tags on the same day is legitimate.
    Exact row duplicates from overlapping CSV exports are not.
    """
    seen = set()
    unique = []
    dupes = 0
    for row in rows:
        key = (row["asin"], row["date"], row["qty"], row["price"],
               row["tag"], row["indirect"])
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        unique.append(row)
    return unique, dupes


def classify_rows(rows):
    """Split rows into direct and non-direct/indirect sales."""
    di = [r for r in rows if r["indirect"] == "di"]
    ndi = [r for r in rows if r["indirect"] != "di"]
    return di, ndi


def aggregate_by_asin(rows):
    """Group rows by ASIN → {asin: {revenue, qty, tags, category, name}}."""
    data = defaultdict(lambda: {
        "revenue": 0.0, "qty": 0, "tags": set(),
        "category": "", "name": "",
    })
    for r in rows:
        d = data[r["asin"]]
        d["revenue"] += r["qty"] * r["price"]
        d["qty"] += r["qty"]
        d["tags"].add(r["tag"])
        if not d["name"]:
            d["name"] = r["name"]
        if not d["category"]:
            d["category"] = r["category"]
    return dict(data)


def aggregate_by_tag(rows):
    """Group rows by tag → {tag: {revenue, qty, unique_asins}}."""
    data = defaultdict(lambda: {"revenue": 0.0, "qty": 0, "asins": set()})
    for r in rows:
        d = data[r["tag"]]
        d["revenue"] += r["qty"] * r["price"]
        d["qty"] += r["qty"]
        d["asins"].add(r["asin"])
    return dict(data)


def aggregate_by_month(rows):
    """Group rows by YYYY-MM → {month: {revenue, qty}}."""
    data = defaultdict(lambda: {"revenue": 0.0, "qty": 0})
    for r in rows:
        month = r["date"][:7] if len(r["date"]) >= 7 else "Unknown"
        d = data[month]
        d["revenue"] += r["qty"] * r["price"]
        d["qty"] += r["qty"]
    return dict(data)


def aggregate_by_category(rows):
    """Group rows by category → {category: {revenue, qty, count}}."""
    data = defaultdict(lambda: {"revenue": 0.0, "qty": 0, "asins": set()})
    for r in rows:
        cat = r["category"] or "Unknown"
        d = data[cat]
        d["revenue"] += r["qty"] * r["price"]
        d["qty"] += r["qty"]
        d["asins"].add(r["asin"])
    return dict(data)


def attribute_primary_tag(asin, rows):
    """Determine canonical tag for an ASIN based on highest DI revenue.

    Prefers recomendos-20 as tiebreaker.
    """
    tag_rev = defaultdict(float)
    for r in rows:
        if r["asin"] == asin and r["indirect"] == "di":
            tag_rev[r["tag"]] += r["qty"] * r["price"]

    if not tag_rev:
        # Fall back to any tag seen for this ASIN
        for r in rows:
            if r["asin"] == asin:
                tag_rev[r["tag"]] += r["qty"] * r["price"]

    if not tag_rev:
        return "unknown"

    max_rev = max(tag_rev.values())
    top_tags = [t for t, v in tag_rev.items() if v == max_rev]
    if "recomendos-20" in top_tags:
        return "recomendos-20"
    return top_tags[0]


def load_products():
    """Load catalog/products.json for title lookups."""
    path = CATALOG_DIR / "products.json"
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def load_featured_history():
    """Load catalog/featured_history.json for ASIN → featured date."""
    path = CATALOG_DIR / "featured_history.json"
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def get_product_title(asin, csv_name, products):
    """Get best available title for a product."""
    if asin in products and products[asin].get("title"):
        return products[asin]["title"]
    return csv_name or asin


def get_date_range(rows):
    """Extract min/max dates from rows."""
    dates = [r["date"][:10] for r in rows if r["date"]]
    if not dates:
        return "Unknown", "Unknown"
    return min(dates), max(dates)


# ---------------------------------------------------------------------------
# Report section generators
# ---------------------------------------------------------------------------

def fmt_currency(amount):
    """Format as $X,XXX (no cents)."""
    return f"${amount:,.0f}"


def fmt_pct(value):
    """Format as whole number percentage."""
    return f"{value:.0f}%"


def section_summary(di_rows, ndi_rows, dupe_count, date_min, date_max):
    """Executive summary with DI/NDI split."""
    di_rev = sum(r["qty"] * r["price"] for r in di_rows)
    ndi_rev = sum(r["qty"] * r["price"] for r in ndi_rows)
    total_rev = di_rev + ndi_rev
    di_qty = sum(r["qty"] for r in di_rows)
    ndi_qty = sum(r["qty"] for r in ndi_rows)
    di_asins = len({r["asin"] for r in di_rows})
    ndi_asins = len({r["asin"] for r in ndi_rows})
    total_asins = len({r["asin"] for r in di_rows + ndi_rows})

    lines = [
        "## Executive Summary",
        "",
        f"> Data: {date_min} to {date_max} | {dupe_count:,} duplicate rows removed",
        "",
        "| Metric | Direct (DI) | Indirect (NDI) | Total |",
        "|--------|-------------|----------------|-------|",
        f"| Revenue | {fmt_currency(di_rev)} | {fmt_currency(ndi_rev)} | {fmt_currency(total_rev)} |",
        f"| Units sold | {di_qty:,} | {ndi_qty:,} | {di_qty + ndi_qty:,} |",
        f"| Unique products | {di_asins:,} | {ndi_asins:,} | {total_asins:,} |",
        f"| Avg price | {fmt_currency(di_rev / di_qty) if di_qty else '$0'} | {fmt_currency(ndi_rev / ndi_qty) if ndi_qty else '$0'} | {fmt_currency(total_rev / (di_qty + ndi_qty)) if (di_qty + ndi_qty) else '$0'} |",
        f"| Share of revenue | {fmt_pct(di_rev / total_rev * 100) if total_rev else '0%'} | {fmt_pct(ndi_rev / total_rev * 100) if total_rev else '0%'} | 100% |",
        "",
        "> **Direct (DI)** = customer bought the exact product you linked.",
        "> **Indirect (NDI)** = customer clicked your link, then bought *other* products.",
        "",
    ]
    return "\n".join(lines)


def section_top_sellers(di_rows, all_rows, products, n=20):
    """Top N products by direct revenue."""
    asin_data = aggregate_by_asin(di_rows)
    sorted_asins = sorted(asin_data.items(), key=lambda x: x[1]["revenue"], reverse=True)[:n]

    lines = [
        f"## Top {n} Products by Direct Revenue",
        "",
        "| # | Product | DI Rev | Units | Avg Price | Tag |",
        "|---|---------|--------|-------|-----------|-----|",
    ]
    for i, (asin, d) in enumerate(sorted_asins, 1):
        title = get_product_title(asin, d["name"], products)
        short = shorten_title(title)
        avg = d["revenue"] / d["qty"] if d["qty"] else 0
        tag = attribute_primary_tag(asin, all_rows)
        lines.append(
            f"| {i} | {short} | {fmt_currency(d['revenue'])} | {d['qty']:,} | {fmt_currency(avg)} | {tag} |"
        )
    lines.append("")
    return "\n".join(lines)


def section_featured_performance(di_rows, products, featured_history):
    """Cross-reference featured products with DI sales data."""
    if not featured_history:
        return "## Featured Product Performance\n\n> No featured_history.json found.\n"

    di_by_asin = aggregate_by_asin(di_rows)
    featured_with_sales = []
    featured_no_sales = []

    for asin, date_str in featured_history.items():
        di_data = di_by_asin.get(asin)
        title = get_product_title(asin, di_data["name"] if di_data else "", products)
        short = shorten_title(title)
        featured_date = date_str[:10]

        if di_data and di_data["revenue"] > 0:
            featured_with_sales.append((asin, short, featured_date, di_data))
        else:
            featured_no_sales.append((asin, short, featured_date))

    featured_with_sales.sort(key=lambda x: x[3]["revenue"], reverse=True)

    lines = [
        "## Featured Product Performance",
        "",
        f"> {len(featured_history)} products in featured history | "
        f"{len(featured_with_sales)} with DI sales | "
        f"{len(featured_no_sales)} with no DI sales",
        "",
        "> Products shown had direct sales within the CSV date range.",
        "> Recently featured products may not yet have sales data.",
        "",
    ]

    if featured_with_sales:
        lines.extend([
            "### Featured Products with Direct Sales",
            "",
            "| # | Product | Featured | DI Rev | Units |",
            "|---|---------|----------|--------|-------|",
        ])
        for i, (asin, short, fdate, d) in enumerate(featured_with_sales[:30], 1):
            lines.append(
                f"| {i} | {short} | {fdate} | {fmt_currency(d['revenue'])} | {d['qty']:,} |"
            )
        lines.append("")

    return "\n".join(lines)


def section_zero_sellers(featured_history, di_rows, products):
    """Featured products with no direct sales."""
    di_asins = {r["asin"] for r in di_rows}
    zero = []
    for asin, date_str in featured_history.items():
        if asin not in di_asins:
            title = get_product_title(asin, "", products)
            short = shorten_title(title)
            zero.append((asin, short, date_str[:10]))

    zero.sort(key=lambda x: x[2], reverse=True)

    lines = [
        "## Featured Products with No Direct Sales",
        "",
        f"> {len(zero)} featured products had zero direct sales in the CSV date range.",
        "> Recently featured products may not yet have had time to generate sales.",
        "",
    ]
    if zero:
        lines.extend([
            "| # | Product | Featured Date |",
            "|---|---------|---------------|",
        ])
        for i, (asin, short, fdate) in enumerate(zero[:30], 1):
            lines.append(f"| {i} | {short} | {fdate} |")
        if len(zero) > 30:
            lines.append(f"| | *...and {len(zero) - 30} more* | |")
        lines.append("")

    return "\n".join(lines)


def section_by_tag(di_rows, ndi_rows):
    """Revenue breakdown per affiliate tag."""
    di_tags = aggregate_by_tag(di_rows)
    ndi_tags = aggregate_by_tag(ndi_rows)
    all_tags = sorted(
        set(list(di_tags.keys()) + list(ndi_tags.keys())),
        key=lambda t: di_tags.get(t, {}).get("revenue", 0),
        reverse=True,
    )

    lines = [
        "## Revenue by Affiliate Tag",
        "",
        "| Tag | DI Revenue | DI Units | NDI Revenue | NDI Units | DI Products |",
        "|-----|-----------|----------|-------------|-----------|-------------|",
    ]
    for tag in all_tags:
        di = di_tags.get(tag, {"revenue": 0, "qty": 0, "asins": set()})
        ndi = ndi_tags.get(tag, {"revenue": 0, "qty": 0, "asins": set()})
        lines.append(
            f"| {tag} | {fmt_currency(di['revenue'])} | {di['qty']:,} | "
            f"{fmt_currency(ndi['revenue'])} | {ndi['qty']:,} | "
            f"{len(di.get('asins', set())):,} |"
        )
    lines.append("")
    return "\n".join(lines)


def section_by_month(di_rows, ndi_rows):
    """Monthly DI + NDI trends."""
    di_months = aggregate_by_month(di_rows)
    ndi_months = aggregate_by_month(ndi_rows)
    all_months = sorted(set(list(di_months.keys()) + list(ndi_months.keys())))

    lines = [
        "## Monthly Trends",
        "",
        "| Month | DI Revenue | DI Units | NDI Revenue | NDI Units | Total |",
        "|-------|-----------|----------|-------------|-----------|-------|",
    ]
    for month in all_months:
        di = di_months.get(month, {"revenue": 0, "qty": 0})
        ndi = ndi_months.get(month, {"revenue": 0, "qty": 0})
        total = di["revenue"] + ndi["revenue"]
        lines.append(
            f"| {month} | {fmt_currency(di['revenue'])} | {di['qty']:,} | "
            f"{fmt_currency(ndi['revenue'])} | {ndi['qty']:,} | {fmt_currency(total)} |"
        )
    lines.append("")
    return "\n".join(lines)


def section_by_category(di_rows, n=15):
    """Top categories by DI revenue."""
    cat_data = aggregate_by_category(di_rows)
    sorted_cats = sorted(cat_data.items(), key=lambda x: x[1]["revenue"], reverse=True)[:n]

    lines = [
        f"## Top {n} Categories by Direct Revenue",
        "",
        "| # | Category | DI Revenue | Units | Products |",
        "|---|----------|-----------|-------|----------|",
    ]
    for i, (cat, d) in enumerate(sorted_cats, 1):
        lines.append(
            f"| {i} | {cat} | {fmt_currency(d['revenue'])} | {d['qty']:,} | {len(d['asins']):,} |"
        )
    lines.append("")
    return "\n".join(lines)


def section_commission_estimate(di_rows):
    """Estimated commission earnings using category-specific rates."""
    cat_data = aggregate_by_category(di_rows)
    rows_out = []
    total_est = 0.0
    total_rev = 0.0

    sorted_cats = sorted(cat_data.items(), key=lambda x: x[1]["revenue"], reverse=True)
    for cat, d in sorted_cats:
        rate = COMMISSION_RATES.get(cat, DEFAULT_COMMISSION_RATE)
        est = d["revenue"] * rate
        total_est += est
        total_rev += d["revenue"]
        if d["revenue"] >= 100:  # Only show categories with meaningful revenue
            rows_out.append((cat, d["revenue"], rate, est))

    lines = [
        "## Estimated Commission",
        "",
        "> These are estimates based on standard Amazon Associates rates.",
        "> Actual commission depends on your agreement terms and may differ.",
        "",
        "| Category | DI Revenue | Rate | Est. Commission |",
        "|----------|-----------|------|-----------------|",
    ]
    for cat, rev, rate, est in rows_out:
        lines.append(
            f"| {cat} | {fmt_currency(rev)} | {fmt_pct(rate * 100)} | {fmt_currency(est)} |"
        )
    avg_rate = total_est / total_rev * 100 if total_rev else 0
    lines.extend([
        f"| **Total** | **{fmt_currency(total_rev)}** | **{fmt_pct(avg_rate)}** | **{fmt_currency(total_est)}** |",
        "",
    ])
    return "\n".join(lines)


def section_data_quality(raw_count, dupe_count, di_rows, ndi_rows, all_rows):
    """Data quality and methodology notes."""
    # Count ASINs with multiple tags
    asin_tags = defaultdict(set)
    for r in all_rows:
        asin_tags[r["asin"]].add(r["tag"])
    multi_tag = sum(1 for tags in asin_tags.values() if len(tags) > 1)

    date_min, date_max = get_date_range(all_rows)

    lines = [
        "## Data Quality Notes",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Raw CSV rows | {raw_count:,} |",
        f"| Duplicate rows removed | {dupe_count:,} |",
        f"| Rows after dedup | {len(all_rows):,} |",
        f"| Direct (DI) rows | {len(di_rows):,} |",
        f"| Indirect (NDI) rows | {len(ndi_rows):,} |",
        f"| Unique ASINs | {len(asin_tags):,} |",
        f"| Products with multiple tags | {multi_tag:,} |",
        f"| Date range | {date_min} to {date_max} |",
        "",
        "### Methodology",
        "",
        "- **Deduplication**: Rows are considered duplicates if they share the same "
        "(ASIN, Date, Qty, Price, Tag, DI/NDI). Same ASIN with different tags on the "
        "same day is preserved as legitimate.",
        "- **DI vs NDI**: Direct = customer purchased the linked product. "
        "Indirect = customer clicked an affiliate link then bought other products.",
        "- **Tag attribution**: For per-product views, primary tag is the tag with "
        "highest DI revenue for that ASIN (recomendos-20 wins ties).",
        "- **Commission estimates**: Based on standard Amazon Associates rate card. "
        "Actual rates depend on your agreement.",
        "",
    ]
    return "\n".join(lines)


def section_actual_commission(earnings_rows, tag_filter=None):
    """Actual commission from Fee-Earnings data, by tag and category."""
    rows = earnings_rows
    if tag_filter:
        rows = [r for r in rows if r["tag"] == tag_filter]

    total_rev = sum(r["revenue"] for r in rows)
    total_fee = sum(r["fee"] for r in rows)
    total_shipped = sum(r["shipped"] for r in rows)
    total_returns = sum(r["returns"] for r in rows)

    # By tag
    tag_data = defaultdict(lambda: {"revenue": 0.0, "fee": 0.0, "shipped": 0, "returns": 0})
    for r in rows:
        d = tag_data[r["tag"]]
        d["revenue"] += r["revenue"]
        d["fee"] += r["fee"]
        d["shipped"] += r["shipped"]
        d["returns"] += r["returns"]
    sorted_tags = sorted(tag_data.items(), key=lambda x: x[1]["fee"], reverse=True)

    # By category
    cat_data = defaultdict(lambda: {"revenue": 0.0, "fee": 0.0})
    for r in rows:
        cat = r["category"] or "Unknown"
        cat_data[cat]["revenue"] += r["revenue"]
        cat_data[cat]["fee"] += r["fee"]
    sorted_cats = sorted(cat_data.items(), key=lambda x: x[1]["fee"], reverse=True)

    eff_rate = total_fee / total_rev * 100 if total_rev else 0
    return_rate = total_returns / total_shipped * 100 if total_shipped else 0

    lines = [
        "## Actual Commission (Fee-Earnings)",
        "",
        "> From shipped items with actual ad fees paid by Amazon.",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total revenue (shipped) | {fmt_currency(total_rev)} |",
        f"| Total commission | **${total_fee:,.2f}** |",
        f"| Effective rate | {fmt_pct(eff_rate)} |",
        f"| Items shipped | {total_shipped:,} |",
        f"| Returns | {total_returns:,} ({fmt_pct(return_rate)}) |",
        f"| Net items | {total_shipped - total_returns:,} |",
        "",
        "### Commission by Tag",
        "",
        "| Tag | Revenue | Commission | Rate | Shipped | Returns |",
        "|-----|---------|------------|------|---------|---------|",
    ]
    for tag, d in sorted_tags:
        rate = d["fee"] / d["revenue"] * 100 if d["revenue"] else 0
        lines.append(
            f"| {tag} | {fmt_currency(d['revenue'])} | ${d['fee']:,.2f} | "
            f"{fmt_pct(rate)} | {d['shipped']:,} | {d['returns']:,} |"
        )
    lines.extend([
        f"| **Total** | **{fmt_currency(total_rev)}** | **${total_fee:,.2f}** | "
        f"**{fmt_pct(eff_rate)}** | **{total_shipped:,}** | **{total_returns:,}** |",
        "",
    ])

    # By category (top 15)
    lines.extend([
        "### Commission by Category",
        "",
        "| Category | Revenue | Commission | Rate |",
        "|----------|---------|------------|------|",
    ])
    for cat, d in sorted_cats[:15]:
        rate = d["fee"] / d["revenue"] * 100 if d["revenue"] else 0
        if d["fee"] >= 1:
            lines.append(
                f"| {cat} | {fmt_currency(d['revenue'])} | ${d['fee']:,.2f} | {fmt_pct(rate)} |"
            )
    lines.append("")

    # Seller breakdown
    seller_data = defaultdict(lambda: {"revenue": 0.0, "fee": 0.0, "shipped": 0})
    for r in rows:
        s = r["seller"] or "Unknown"
        seller_data[s]["revenue"] += r["revenue"]
        seller_data[s]["fee"] += r["fee"]
        seller_data[s]["shipped"] += r["shipped"]

    lines.extend([
        "### By Seller",
        "",
        "| Seller | Revenue | Commission | Items |",
        "|--------|---------|------------|-------|",
    ])
    for seller, d in sorted(seller_data.items(), key=lambda x: x[1]["revenue"], reverse=True):
        lines.append(
            f"| {seller} | {fmt_currency(d['revenue'])} | ${d['fee']:,.2f} | {d['shipped']:,} |"
        )
    lines.append("")

    return "\n".join(lines)


def section_bounty(bounty_rows):
    """Bounty program earnings."""
    if not bounty_rows:
        return ""

    total_fee = sum(r["fee"] for r in bounty_rows)
    total_qty = sum(r["qty"] for r in bounty_rows)

    by_program = defaultdict(lambda: {"fee": 0.0, "qty": 0})
    for r in bounty_rows:
        d = by_program[r["name"]]
        d["fee"] += r["fee"]
        d["qty"] += r["qty"]

    lines = [
        "## Bounty Earnings",
        "",
        f"> {total_qty} bounty actions | ${total_fee:,.2f} total",
        "",
        "| Program | Signups | Earnings |",
        "|---------|---------|----------|",
    ]
    for name, d in sorted(by_program.items(), key=lambda x: x[1]["fee"], reverse=True):
        lines.append(f"| {name} | {d['qty']:,} | ${d['fee']:,.2f} |")
    lines.extend([
        f"| **Total** | **{total_qty:,}** | **${total_fee:,.2f}** |",
        "",
    ])
    return "\n".join(lines)


def section_total_earnings(earnings_rows, bounty_rows, tag_filter=None):
    """Grand total across all earning types."""
    e_rows = earnings_rows
    if tag_filter:
        e_rows = [r for r in e_rows if r["tag"] == tag_filter]

    commission = sum(r["fee"] for r in e_rows)
    bounty = sum(r["fee"] for r in bounty_rows) if bounty_rows else 0
    total = commission + bounty

    lines = [
        "## Total Earnings Summary",
        "",
        "| Source | Amount |",
        "|--------|--------|",
        f"| Product commission | ${commission:,.2f} |",
        f"| Bounty programs | ${bounty:,.2f} |",
        f"| **Grand total** | **${total:,.2f}** |",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Report orchestration
# ---------------------------------------------------------------------------

def generate_report(csv_path, top_n=20, tag_filter=None, featured_only=False,
                    earnings_path=None, bounty_path=None):
    """Generate the full markdown report."""
    # Load data
    raw_rows = load_csv(csv_path)
    raw_count = len(raw_rows)

    # Load optional earnings/bounty data
    earnings_rows = None
    bounty_rows = None
    if earnings_path and Path(earnings_path).exists():
        earnings_rows = load_earnings_csv(earnings_path)
    if bounty_path and Path(bounty_path).exists():
        bounty_rows = load_bounty_csv(bounty_path)

    # Deduplicate
    rows, dupe_count = deduplicate_rows(raw_rows)

    # Apply tag filter
    if tag_filter:
        rows = [r for r in rows if r["tag"] == tag_filter]

    # Classify
    di_rows, ndi_rows = classify_rows(rows)
    all_rows = di_rows + ndi_rows

    # Load catalog data
    products = load_products()
    featured_history = load_featured_history()

    # Apply featured-only filter
    if featured_only:
        featured_asins = set(featured_history.keys())
        di_rows = [r for r in di_rows if r["asin"] in featured_asins]
        ndi_rows = [r for r in ndi_rows if r["asin"] in featured_asins]
        all_rows = di_rows + ndi_rows

    date_min, date_max = get_date_range(all_rows)

    # Build header
    parts = []
    title = "# Amazon Associates Sales Report"
    if tag_filter:
        title += f" — Tag: {tag_filter}"
    if featured_only:
        title += " — Featured Products Only"
    parts.append(title)
    parts.append("")
    sources = [Path(csv_path).name]
    if earnings_rows is not None:
        sources.append(Path(earnings_path).name)
    if bounty_rows is not None:
        sources.append(Path(bounty_path).name)
    parts.append(f"*Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
                 f"Source: {', '.join(sources)}*")
    parts.append("")

    # Sections
    parts.append(section_summary(di_rows, ndi_rows, dupe_count, date_min, date_max))

    # Actual earnings before the details if available
    if earnings_rows is not None:
        parts.append(section_total_earnings(earnings_rows, bounty_rows, tag_filter))
        parts.append(section_actual_commission(earnings_rows, tag_filter))
    if bounty_rows and not tag_filter:
        parts.append(section_bounty(bounty_rows))

    parts.append(section_top_sellers(di_rows, all_rows, products, n=top_n))
    parts.append(section_by_tag(di_rows, ndi_rows))
    parts.append(section_by_month(di_rows, ndi_rows))
    parts.append(section_by_category(di_rows))

    # Show estimates only when no actual earnings data
    if earnings_rows is None:
        parts.append(section_commission_estimate(di_rows))

    parts.append(section_featured_performance(di_rows, products, featured_history))
    parts.append(section_zero_sellers(featured_history, di_rows, products))
    parts.append(section_data_quality(raw_count, dupe_count, di_rows, ndi_rows, all_rows))

    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(
        description="Generate sales report from Amazon Associates CSV data."
    )
    parser.add_argument(
        "--csv", type=str, default=str(SALES_CSV),
        help=f"Path to CSV file (default: {SALES_CSV})",
    )
    parser.add_argument(
        "--save", action="store_true",
        help="Save report to reports/sales/ directory",
    )
    parser.add_argument(
        "--top", type=int, default=20,
        help="Number of top sellers to show (default: 20)",
    )
    parser.add_argument(
        "--tag", type=str, default=None,
        help="Filter to a single affiliate tag",
    )
    parser.add_argument(
        "--featured-only", action="store_true",
        help="Only include featured products",
    )
    parser.add_argument(
        "--earnings", type=str, default=None,
        help="Path to Fee-Earnings CSV (for actual commission data)",
    )
    parser.add_argument(
        "--bounty", type=str, default=None,
        help="Path to Bounty CSV (for bounty program earnings)",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"Error: CSV file not found: {csv_path}")
        return 1

    # Auto-detect earnings/bounty if not specified
    earnings_path = args.earnings
    bounty_path = args.bounty
    if earnings_path is None and EARNINGS_CSV.exists():
        earnings_path = str(EARNINGS_CSV)
    if bounty_path is None and BOUNTY_CSV.exists():
        bounty_path = str(BOUNTY_CSV)

    report = generate_report(
        csv_path=csv_path,
        top_n=args.top,
        tag_filter=args.tag,
        featured_only=args.featured_only,
        earnings_path=earnings_path,
        bounty_path=bounty_path,
    )

    if args.save:
        SALES_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"sales-{datetime.now().strftime('%Y-%m-%d')}.md"
        out_path = SALES_REPORTS_DIR / filename
        out_path.write_text(report)
        print(f"Report saved to {out_path}")
    else:
        print(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
