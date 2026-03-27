#!/usr/bin/env python3
"""Mailchimp campaign analytics report.

Fetches campaign performance data (open rates, click rates, unsubscribes)
and cross-references with featured products to reveal engagement patterns.

Usage:
    python3 campaign_report.py              # Report on all campaigns
    python3 campaign_report.py --last 10    # Last 10 campaigns only
    python3 campaign_report.py --save       # Save to reports/
"""

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import config
from mailchimp_send import (
    check_config,
    get_recent_campaigns,
    get_campaign_report,
    get_campaign_click_details,
)


def load_campaign_history():
    """Load local campaign history (ASIN-to-campaign mapping)."""
    path = config.PROJECT_ROOT / "catalog" / "campaign_history.json"
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def build_url_to_asin_map(history, catalog=None):
    """Build a reverse lookup from affiliate URL → ASIN.

    Sources (in priority order):
    1. affiliate_urls saved in campaign_history.json entries
    2. affiliate_url field in catalog/products.json
    """
    url_map = {}

    # From catalog (broad coverage, lower priority)
    if catalog:
        for asin, entry in catalog.items():
            aff_url = entry.get("affiliate_url", "")
            if isinstance(aff_url, str) and aff_url:
                url_map[aff_url] = asin

    # From campaign history (higher priority, campaign-specific)
    for h in history:
        for asin, url in h.get("affiliate_urls", {}).items():
            if url:
                url_map[url] = asin

    return url_map


def fmt_pct(value):
    """Format as percentage."""
    return f"{value:.1f}%"


def fmt_date(iso_str):
    """Extract YYYY-MM-DD from ISO datetime string."""
    if not iso_str:
        return "—"
    return iso_str[:10]


def extract_asin_from_url(url):
    """Try to extract an ASIN from an Amazon URL."""
    m = re.search(r'/dp/([A-Z0-9]{10})', url)
    if m:
        return m.group(1)
    m = re.search(r'amazon\.com.*?/([A-Z0-9]{10})(?:[/?]|$)', url)
    if m:
        return m.group(1)
    return None


def fetch_all_reports(campaigns, verbose=True):
    """Fetch detailed reports for a list of campaigns."""
    reports = []
    total = len(campaigns)
    for i, c in enumerate(campaigns):
        cid = c.get("id")
        status = c.get("status", "")
        if status not in ("sent", "sending"):
            continue
        if verbose:
            print(f"  Fetching report {i+1}/{total}: {c.get('settings', {}).get('subject_line', cid)[:50]}...")
        report = get_campaign_report(cid)
        if report:
            reports.append(report)
    return reports


def fetch_click_details_for_campaigns(reports, history_lookup, verbose=True):
    """Fetch click details for campaigns that have local history."""
    click_data = {}
    for report in reports:
        cid = report["campaign_id"]
        if cid not in history_lookup:
            continue
        if verbose:
            print(f"  Fetching clicks: {report['subject'][:50]}...")
        details = get_campaign_click_details(cid)
        if details:
            click_data[cid] = details
    return click_data


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------

def section_summary(reports):
    """Overall campaign performance summary."""
    if not reports:
        return "## Summary\n\n> No sent campaigns found.\n"

    total = len(reports)
    total_sent = sum(r["emails_sent"] for r in reports)
    total_opens = sum(r["opens"]["unique"] for r in reports)
    total_clicks = sum(r["clicks"]["unique"] for r in reports)
    total_unsubs = sum(r["unsubscribes"] for r in reports)

    avg_open_rate = sum(r["opens"]["rate"] for r in reports) / total * 100
    avg_click_rate = sum(r["clicks"]["rate"] for r in reports) / total * 100
    avg_unsubs = total_unsubs / total

    # Date range
    dates = [r["send_time"] for r in reports if r["send_time"]]
    date_range = f"{fmt_date(min(dates))} to {fmt_date(max(dates))}" if dates else "—"

    lines = [
        "## Summary",
        "",
        f"> {total} campaigns | {date_range}",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Campaigns sent | {total} |",
        f"| Total emails sent | {total_sent:,} |",
        f"| Avg open rate | {fmt_pct(avg_open_rate)} |",
        f"| Avg click rate | {fmt_pct(avg_click_rate)} |",
        f"| Total unsubscribes | {total_unsubs:,} |",
        f"| Avg unsubs/campaign | {avg_unsubs:.1f} |",
        "",
    ]
    return "\n".join(lines)


def section_campaign_table(reports):
    """Detailed per-campaign performance table."""
    lines = [
        "## Campaign Performance",
        "",
        "| Date | Subject | Sent | Opens | Open% | Clicks | Click% | Unsubs |",
        "|------|---------|------|-------|-------|--------|--------|--------|",
    ]
    for r in reports:
        date = fmt_date(r["send_time"])
        subject = r["subject"][:40]
        sent = r["emails_sent"]
        opens = r["opens"]["unique"]
        open_rate = r["opens"]["rate"] * 100
        clicks = r["clicks"]["unique"]
        click_rate = r["clicks"]["rate"] * 100
        unsubs = r["unsubscribes"]

        lines.append(
            f"| {date} | {subject} | {sent:,} | {opens:,} | {fmt_pct(open_rate)} "
            f"| {clicks:,} | {fmt_pct(click_rate)} | {unsubs} |"
        )
    lines.append("")
    return "\n".join(lines)


def section_top_campaigns(reports, n=10):
    """Top campaigns by click rate."""
    sorted_reports = sorted(reports, key=lambda r: r["clicks"]["rate"], reverse=True)[:n]

    lines = [
        f"## Top {n} Campaigns by Click Rate",
        "",
        "| # | Date | Subject | Click% | Open% | Unsubs |",
        "|---|------|---------|--------|-------|--------|",
    ]
    for i, r in enumerate(sorted_reports, 1):
        lines.append(
            f"| {i} | {fmt_date(r['send_time'])} | {r['subject'][:40]} "
            f"| {fmt_pct(r['clicks']['rate'] * 100)} | {fmt_pct(r['opens']['rate'] * 100)} "
            f"| {r['unsubscribes']} |"
        )
    lines.append("")
    return "\n".join(lines)


def section_worst_campaigns(reports, n=10):
    """Worst campaigns by unsub count, then lowest click rate."""
    # Sort by unsubs desc, then click rate asc
    sorted_reports = sorted(
        reports,
        key=lambda r: (-r["unsubscribes"], r["clicks"]["rate"])
    )[:n]

    lines = [
        f"## Bottom {n} Campaigns (Highest Unsubs / Lowest Clicks)",
        "",
        "| # | Date | Subject | Unsubs | Click% | Open% |",
        "|---|------|---------|--------|--------|-------|",
    ]
    for i, r in enumerate(sorted_reports, 1):
        lines.append(
            f"| {i} | {fmt_date(r['send_time'])} | {r['subject'][:40]} "
            f"| {r['unsubscribes']} | {fmt_pct(r['clicks']['rate'] * 100)} "
            f"| {fmt_pct(r['opens']['rate'] * 100)} |"
        )
    lines.append("")
    return "\n".join(lines)


def section_product_clicks(click_data, history_lookup, url_to_asin=None):
    """Cross-reference click URLs with product ASINs from campaign history."""
    if url_to_asin is None:
        url_to_asin = {}

    # Aggregate clicks per ASIN across all campaigns
    asin_clicks = defaultdict(lambda: {"total": 0, "unique": 0, "title": "", "campaigns": 0})
    asin_campaigns = defaultdict(set)

    for cid, urls in click_data.items():
        hist = history_lookup.get(cid, {})
        titles = hist.get("titles", {})
        campaign_asins = set(hist.get("asins", []))

        for url_info in urls:
            url = url_info["url"]
            asin = extract_asin_from_url(url)

            # Match geni.us / amzn.to links via reverse lookup
            if not asin:
                asin = url_to_asin.get(url)

            if not asin:
                continue

            if asin in campaign_asins:
                d = asin_clicks[asin]
                d["total"] += url_info["total_clicks"]
                d["unique"] += url_info["unique_clicks"]
                if not d["title"]:
                    d["title"] = titles.get(asin, asin)
                asin_campaigns[asin].add(cid)

    if not asin_clicks:
        return "## Product Click-Through Analysis\n\n> No product click data available yet. Product-level tracking starts after campaign history is saved.\n"

    # Finalize campaign counts
    for asin in asin_clicks:
        asin_clicks[asin]["campaigns"] = len(asin_campaigns[asin])

    sorted_asins = sorted(asin_clicks.items(), key=lambda x: x[1]["unique"], reverse=True)

    lines = [
        "## Product Click-Through Analysis",
        "",
        f"> {len(sorted_asins)} products with click data across {len(click_data)} campaigns",
        "",
        "| # | Product | Unique Clicks | Total Clicks | Campaigns |",
        "|---|---------|---------------|--------------|-----------|",
    ]
    for i, (asin, d) in enumerate(sorted_asins[:30], 1):
        title = d["title"][:45] if d["title"] else asin
        lines.append(
            f"| {i} | {title} | {d['unique']:,} | {d['total']:,} | {d['campaigns']} |"
        )
    lines.append("")
    return "\n".join(lines)


def section_unsub_trend(reports):
    """Unsubscribe trend over time (by week)."""
    if not reports:
        return ""

    # Group by week
    weekly = defaultdict(lambda: {"unsubs": 0, "campaigns": 0, "sent": 0})
    for r in reports:
        send_time = r["send_time"]
        if not send_time:
            continue
        try:
            dt = datetime.fromisoformat(send_time.replace("Z", "+00:00"))
            # ISO week
            week_key = dt.strftime("%Y-W%W")
        except (ValueError, TypeError):
            continue
        weekly[week_key]["unsubs"] += r["unsubscribes"]
        weekly[week_key]["campaigns"] += 1
        weekly[week_key]["sent"] += r["emails_sent"]

    if not weekly:
        return ""

    sorted_weeks = sorted(weekly.items())

    lines = [
        "## Unsubscribe Trend (by Week)",
        "",
        "| Week | Campaigns | Unsubs | Unsubs/Campaign | Sent |",
        "|------|-----------|--------|-----------------|------|",
    ]
    for week, d in sorted_weeks:
        avg = d["unsubs"] / d["campaigns"] if d["campaigns"] else 0
        lines.append(
            f"| {week} | {d['campaigns']} | {d['unsubs']} | {avg:.1f} | {d['sent']:,} |"
        )
    lines.append("")
    return "\n".join(lines)


def section_engagement_trend(reports):
    """Open and click rate trend over time."""
    if len(reports) < 3:
        return ""

    # Compare first half vs second half
    mid = len(reports) // 2
    recent = reports[:mid]  # newest first
    older = reports[mid:]

    recent_open = sum(r["opens"]["rate"] for r in recent) / len(recent) * 100
    older_open = sum(r["opens"]["rate"] for r in older) / len(older) * 100
    recent_click = sum(r["clicks"]["rate"] for r in recent) / len(recent) * 100
    older_click = sum(r["clicks"]["rate"] for r in older) / len(older) * 100
    recent_unsub = sum(r["unsubscribes"] for r in recent) / len(recent)
    older_unsub = sum(r["unsubscribes"] for r in older) / len(older)

    open_delta = recent_open - older_open
    click_delta = recent_click - older_click
    unsub_delta = recent_unsub - older_unsub

    def trend(delta, invert=False):
        """Arrow indicator for trend direction."""
        if abs(delta) < 0.1:
            return "→"
        if invert:
            return "↓" if delta > 0 else "↑"
        return "↑" if delta > 0 else "↓"

    lines = [
        "## Engagement Trend",
        "",
        f"> Comparing recent {len(recent)} campaigns vs older {len(older)} campaigns",
        "",
        "| Metric | Recent | Older | Change |",
        "|--------|--------|-------|--------|",
        f"| Open rate | {fmt_pct(recent_open)} | {fmt_pct(older_open)} | {open_delta:+.1f}pp {trend(open_delta)} |",
        f"| Click rate | {fmt_pct(recent_click)} | {fmt_pct(older_click)} | {click_delta:+.1f}pp {trend(click_delta)} |",
        f"| Unsubs/campaign | {recent_unsub:.1f} | {older_unsub:.1f} | {unsub_delta:+.1f} {trend(unsub_delta, invert=True)} |",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_report(last_n=None):
    """Fetch data and generate the full markdown report."""
    print("Fetching campaigns from Mailchimp...")
    count = last_n or 100
    campaigns = get_recent_campaigns(count=count)
    if not campaigns:
        return "# Campaign Analytics Report\n\n> No campaigns found. Check your Mailchimp API key.\n"

    print(f"Found {len(campaigns)} campaigns")

    # Fetch detailed reports for sent campaigns
    print("Fetching campaign reports...")
    reports = fetch_all_reports(campaigns)
    if not reports:
        return "# Campaign Analytics Report\n\n> No sent campaigns with report data found.\n"

    if last_n:
        reports = reports[:last_n]
    print(f"Got reports for {len(reports)} campaigns")

    # Load local campaign history
    history = load_campaign_history()
    history_lookup = {h["campaign_id"]: h for h in history}
    print(f"Local campaign history: {len(history)} campaigns")

    # Load product catalog for affiliate URL lookups
    catalog = None
    catalog_path = config.CATALOG_FILE
    if catalog_path.exists():
        with open(catalog_path) as f:
            catalog = json.load(f)
        print(f"Loaded catalog: {len(catalog)} products")

    # Build URL→ASIN reverse map for matching geni.us/amzn.to clicks
    url_to_asin = build_url_to_asin_map(history, catalog)
    print(f"URL→ASIN map: {len(url_to_asin)} affiliate URLs")

    # Fetch click details for campaigns with local history
    click_data = {}
    if history_lookup:
        print("Fetching click details for tracked campaigns...")
        click_data = fetch_click_details_for_campaigns(reports, history_lookup)
        print(f"Got click details for {len(click_data)} campaigns")

    # Build report
    parts = [
        f"# Mailchimp Campaign Analytics Report",
        "",
        f"*Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        "",
    ]
    parts.append(section_summary(reports))
    parts.append(section_engagement_trend(reports))
    parts.append(section_campaign_table(reports))
    parts.append(section_top_campaigns(reports))
    parts.append(section_worst_campaigns(reports))
    parts.append(section_product_clicks(click_data, history_lookup, url_to_asin))
    parts.append(section_unsub_trend(reports))

    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(
        description="Generate Mailchimp campaign analytics report."
    )
    parser.add_argument(
        "--last", type=int, default=None,
        help="Only report on last N campaigns (default: all)",
    )
    parser.add_argument(
        "--save", action="store_true",
        help="Save report to reports/ directory",
    )
    args = parser.parse_args()

    if not check_config():
        return 1

    report = generate_report(last_n=args.last)

    if args.save:
        reports_dir = config.PROJECT_ROOT / "reports"
        reports_dir.mkdir(exist_ok=True)
        filename = f"campaign-report-{datetime.now().strftime('%Y-%m-%d')}.md"
        out_path = reports_dir / filename
        out_path.write_text(report)
        print(f"\nReport saved to {out_path}")
    else:
        print()
        print(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
