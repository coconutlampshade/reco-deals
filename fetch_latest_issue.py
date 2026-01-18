#!/usr/bin/env python3
"""
Fetch the latest Recomendo issue and extract Amazon products.

Runs automatically every Sunday at 5am to catch the new weekly issue.

Usage:
    python fetch_latest_issue.py          # Fetch latest issue
    python fetch_latest_issue.py --dry-run # Show what would be added without saving
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

# RSS feed URL
RECOMENDO_FEED = "https://recomendo.substack.com/feed"

# Project paths
PROJECT_DIR = Path(__file__).parent
CATALOG_FILE = PROJECT_DIR / "catalog" / "products.json"


def fetch_rss_feed():
    """Fetch and parse the Recomendo RSS feed."""
    print(f"Fetching RSS feed: {RECOMENDO_FEED}")

    # Use comprehensive browser headers to avoid 403 blocks
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    # Create a session and try the request
    session = requests.Session()

    # First visit the main site to get any cookies
    try:
        session.get("https://recomendo.substack.com", headers=headers, timeout=10)
    except:
        pass  # Continue even if this fails

    response = session.get(RECOMENDO_FEED, headers=headers, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.content, "xml")
    items = soup.find_all("item")

    print(f"Found {len(items)} items in feed")
    return items


def get_latest_issue(items):
    """Get the most recent issue from RSS items."""
    if not items:
        return None

    # First item is the latest
    item = items[0]

    title = item.find("title").text if item.find("title") else "Unknown"
    link = item.find("link").text if item.find("link") else ""
    pub_date = item.find("pubDate").text if item.find("pubDate") else ""
    content = item.find("content:encoded")

    if content:
        content_html = content.text
    else:
        # Fallback to description
        desc = item.find("description")
        content_html = desc.text if desc else ""

    return {
        "title": title,
        "url": link,
        "date": pub_date,
        "content": content_html
    }


def extract_amazon_links(html_content):
    """Extract all Amazon-related URLs from HTML content."""
    soup = BeautifulSoup(html_content, "html.parser")
    links = []

    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href", "")
        text = a_tag.get_text(strip=True)

        # Check if it's an Amazon-related link
        if any(domain in href.lower() for domain in ["amazon.com", "amzn.to", "geni.us", "amzn.com"]):
            links.append({
                "url": href,
                "text": text
            })

    return links


def resolve_shortlink(url, max_redirects=5):
    """Follow redirects to get the final Amazon URL."""
    if "amazon.com" in url and "/dp/" in url:
        return url  # Already a direct Amazon link

    try:
        session = requests.Session()
        response = session.head(url, allow_redirects=True, timeout=10)
        final_url = response.url

        # Check if we ended up at Amazon
        if "amazon.com" in final_url:
            return final_url

        # Try GET if HEAD didn't follow redirects properly
        response = session.get(url, allow_redirects=True, timeout=10)
        return response.url

    except Exception as e:
        print(f"  Warning: Could not resolve {url}: {e}")
        return url


def extract_asin(url):
    """Extract ASIN from an Amazon URL."""
    # Pattern: /dp/ASIN or /gp/product/ASIN or /gp/aw/d/ASIN
    patterns = [
        r'/dp/([A-Z0-9]{10})',
        r'/gp/product/([A-Z0-9]{10})',
        r'/gp/aw/d/([A-Z0-9]{10})',
        r'/product/([A-Z0-9]{10})',
    ]

    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            return match.group(1).upper()

    return None


def load_catalog():
    """Load existing product catalog."""
    if CATALOG_FILE.exists():
        with open(CATALOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_catalog(catalog):
    """Save product catalog."""
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)


def parse_issue_date(pub_date_str):
    """Parse RSS pubDate to YYYY-MM-DD format."""
    try:
        # RSS format: "Sun, 29 Dec 2024 12:00:00 GMT"
        dt = datetime.strptime(pub_date_str, "%a, %d %b %Y %H:%M:%S %Z")
        return dt.strftime("%Y-%m-%d")
    except:
        return datetime.now().strftime("%Y-%m-%d")


def process_issue(issue, catalog, dry_run=False):
    """Process an issue and extract new products."""
    print(f"\nProcessing: {issue['title']}")
    print(f"URL: {issue['url']}")
    print(f"Date: {issue['date']}")

    # Extract Amazon links
    links = extract_amazon_links(issue["content"])
    print(f"Found {len(links)} Amazon links")

    if not links:
        return 0

    # Parse issue date
    issue_date = parse_issue_date(issue["date"])

    # Process each link
    new_products = 0
    for link_info in links:
        url = link_info["url"]
        text = link_info["text"]

        print(f"\n  Link: {text[:50]}..." if len(text) > 50 else f"\n  Link: {text}")

        # Resolve shortlinks
        if "geni.us" in url or "amzn.to" in url:
            print(f"    Resolving shortlink...")
            time.sleep(0.5)  # Rate limit
            final_url = resolve_shortlink(url)
            affiliate_url = url
        else:
            final_url = url
            affiliate_url = None

        # Extract ASIN
        asin = extract_asin(final_url)
        if not asin:
            print(f"    Could not extract ASIN from: {final_url[:60]}...")
            continue

        print(f"    ASIN: {asin}")

        # Check if already in catalog
        if asin in catalog:
            # Add this issue to existing product
            existing_issues = [i["url"] for i in catalog[asin].get("issues", [])]
            if issue["url"] not in existing_issues:
                catalog[asin]["issues"].append({
                    "url": issue["url"],
                    "title": issue["title"],
                    "date": issue_date
                })
                print(f"    Added issue to existing product")
        else:
            # New product
            catalog[asin] = {
                "asin": asin,
                "title": text,
                "amazon_url": f"https://www.amazon.com/dp/{asin}",
                "affiliate_url": affiliate_url,
                "issues": [{
                    "url": issue["url"],
                    "title": issue["title"],
                    "date": issue_date
                }],
                "first_featured": issue_date,
                "added_at": datetime.now().isoformat()
            }
            new_products += 1
            print(f"    NEW PRODUCT ADDED")

    return new_products


def main():
    parser = argparse.ArgumentParser(description="Fetch latest Recomendo issue")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be added without saving")
    parser.add_argument("--all", action="store_true",
                        help="Process all items in feed (not just latest)")
    args = parser.parse_args()

    # Fetch RSS feed
    try:
        items = fetch_rss_feed()
    except Exception as e:
        print(f"Error fetching feed: {e}")
        sys.exit(1)

    if not items:
        print("No items found in feed")
        sys.exit(1)

    # Load existing catalog
    catalog = load_catalog()
    original_count = len(catalog)
    print(f"\nExisting catalog: {original_count} products")

    # Process issue(s)
    if args.all:
        issues_to_process = [get_latest_issue([item]) for item in items]
    else:
        issues_to_process = [get_latest_issue(items)]

    total_new = 0
    for issue in issues_to_process:
        if issue:
            new_count = process_issue(issue, catalog, args.dry_run)
            total_new += new_count

    # Save catalog
    if not args.dry_run and total_new > 0:
        save_catalog(catalog)
        print(f"\n{'='*50}")
        print(f"Added {total_new} new products")
        print(f"Catalog now has {len(catalog)} products")
        print(f"Saved to: {CATALOG_FILE}")
    elif args.dry_run:
        print(f"\n{'='*50}")
        print(f"DRY RUN - Would add {total_new} new products")
    else:
        print(f"\n{'='*50}")
        print("No new products found")


if __name__ == "__main__":
    main()
