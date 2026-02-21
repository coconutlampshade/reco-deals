#!/usr/bin/env python3
"""
Fetch Cool Tools newsletter campaigns from Mailchimp and extract Amazon products.

Replaces the RSS-based fetch_latest_issue.py with a more reliable Mailchimp API approach.
The Cool Tools Weekly Newsletter (list bb73681436) contains products from both
Recomendo and Cool Tools, with geni.us affiliate links.

Usage:
    python fetch_cooltools_campaigns.py           # Fetch and process new campaigns
    python fetch_cooltools_campaigns.py --dry-run  # Show what would be added without saving
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# Mailchimp config
MAILCHIMP_API_KEY = os.getenv("MAILCHIMP_API_KEY")
COOLTOOLS_LIST_ID = "bb73681436"

# Project paths
PROJECT_DIR = Path(__file__).parent
CATALOG_FILE = PROJECT_DIR / "catalog" / "products.json"
PROCESSED_FILE = PROJECT_DIR / "catalog" / "processed_campaigns.json"


def get_mailchimp_dc():
    """Extract data center from API key."""
    if MAILCHIMP_API_KEY and "-" in MAILCHIMP_API_KEY:
        return MAILCHIMP_API_KEY.split("-")[-1]
    return "us5"


MAILCHIMP_API_URL = f"https://{get_mailchimp_dc()}.api.mailchimp.com/3.0"


def mailchimp_request(method, endpoint):
    """Make authenticated request to Mailchimp API."""
    url = f"{MAILCHIMP_API_URL}{endpoint}"
    auth = ("anystring", MAILCHIMP_API_KEY)
    headers = {"Content-Type": "application/json"}

    if method == "GET":
        response = requests.get(url, auth=auth, headers=headers, timeout=30)
    else:
        raise ValueError(f"Unsupported method: {method}")

    if response.status_code >= 400:
        print(f"Mailchimp API error: {response.status_code}")
        print(response.text)
        return None

    return response.json()


def get_cooltools_campaigns(since_days=30):
    """Fetch recent Cool Tools campaigns from Mailchimp."""
    since_date = (datetime.utcnow() - timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    result = mailchimp_request(
        "GET",
        f"/campaigns?list_id={COOLTOOLS_LIST_ID}&status=sent"
        f"&sort_field=send_time&sort_dir=DESC&count=10"
        f"&since_send_time={since_date}"
    )
    if not result:
        return []

    campaigns = result.get("campaigns", [])
    print(f"Found {len(campaigns)} sent Cool Tools campaigns in last {since_days} days")
    return campaigns


def get_campaign_content(campaign_id):
    """Fetch HTML content of a campaign."""
    result = mailchimp_request("GET", f"/campaigns/{campaign_id}/content")
    if not result:
        return None
    return result.get("html", "")


def load_processed():
    """Load set of already-processed campaign IDs."""
    if PROCESSED_FILE.exists():
        with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data.get("processed", []))
    return set()


def save_processed(processed_ids):
    """Save processed campaign IDs."""
    data = {
        "processed": sorted(processed_ids),
        "last_checked": datetime.now().isoformat()
    }
    with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


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


def extract_amazon_links(html_content):
    """Extract all Amazon-related URLs from HTML content."""
    soup = BeautifulSoup(html_content, "html.parser")
    links = []

    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href", "")
        text = a_tag.get_text(strip=True)

        if any(domain in href.lower() for domain in ["amazon.com", "amzn.to", "geni.us", "amzn.com"]):
            links.append({
                "url": href,
                "text": text
            })

    return links


def resolve_shortlink(url):
    """Follow redirects to get the final Amazon URL."""
    if "amazon.com" in url and "/dp/" in url:
        return url

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        session = requests.Session()
        # GET works more reliably than HEAD for geni.us redirects
        response = session.get(url, allow_redirects=True, timeout=15, headers=headers)
        return response.url

    except Exception as e:
        print(f"  Warning: Could not resolve {url}: {e}")
        return url


def extract_asin(url):
    """Extract ASIN from an Amazon URL."""
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


def process_campaign(campaign, catalog, dry_run=False):
    """Process a single campaign and extract new products."""
    campaign_id = campaign["id"]
    subject = campaign.get("settings", {}).get("subject_line", "Unknown")
    send_time = campaign.get("send_time", "")
    archive_url = campaign.get("archive_url", "")

    # Parse send date
    try:
        send_date = datetime.strptime(send_time[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except (ValueError, IndexError):
        send_date = datetime.now().strftime("%Y-%m-%d")

    print(f"\nProcessing campaign: {subject}")
    print(f"  Sent: {send_date}")
    print(f"  ID: {campaign_id}")

    # Fetch campaign HTML
    html_content = get_campaign_content(campaign_id)
    if not html_content:
        print("  Error: Could not fetch campaign content")
        return 0

    # Extract Amazon links
    links = extract_amazon_links(html_content)
    print(f"  Found {len(links)} Amazon/affiliate links")

    if not links:
        return 0

    new_products = 0
    for link_info in links:
        url = link_info["url"]
        text = link_info["text"]

        display_text = f"{text[:50]}..." if len(text) > 50 else text
        print(f"\n    Link: {display_text}")

        # Resolve shortlinks
        if "geni.us" in url or "amzn.to" in url:
            print(f"      Resolving shortlink...")
            time.sleep(0.5)  # Rate limit
            final_url = resolve_shortlink(url)
            affiliate_url = url
        else:
            final_url = url
            affiliate_url = None

        # Extract ASIN
        asin = extract_asin(final_url)
        if not asin:
            print(f"      Could not extract ASIN from: {final_url[:80]}")
            continue

        print(f"      ASIN: {asin}")

        # Issue metadata for this campaign
        issue_entry = {
            "url": archive_url,
            "title": subject,
            "date": send_date,
            "source": "cooltools"
        }

        if asin in catalog:
            # Add this campaign as an issue to existing product
            existing_urls = [i.get("url") for i in catalog[asin].get("issues", [])]
            if archive_url not in existing_urls:
                catalog[asin]["issues"].append(issue_entry)
                print(f"      Added issue to existing product")
            else:
                print(f"      Already tracked for this campaign")
        else:
            # New product
            catalog[asin] = {
                "asin": asin,
                "title": text,
                "amazon_url": f"https://www.amazon.com/dp/{asin}",
                "affiliate_url": affiliate_url,
                "issues": [issue_entry],
                "first_featured": send_date,
                "added_at": datetime.now().isoformat()
            }
            new_products += 1
            print(f"      NEW PRODUCT ADDED")

    return new_products


def main():
    parser = argparse.ArgumentParser(description="Fetch Cool Tools campaigns from Mailchimp")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be added without saving")
    parser.add_argument("--days", type=int, default=30,
                        help="How many days back to look for campaigns (default: 30)")
    args = parser.parse_args()

    # Check API key
    if not MAILCHIMP_API_KEY:
        print("Error: MAILCHIMP_API_KEY not set")
        print("Add it to your .env file or set it as an environment variable")
        sys.exit(1)

    # Load processed campaigns and catalog
    processed = load_processed()
    catalog = load_catalog()
    original_count = len(catalog)
    print(f"Existing catalog: {original_count} products")
    print(f"Previously processed campaigns: {len(processed)}")

    # Fetch recent campaigns
    campaigns = get_cooltools_campaigns(since_days=args.days)
    if not campaigns:
        print("No campaigns found")
        sys.exit(0)

    # Filter out already-processed campaigns
    new_campaigns = [c for c in campaigns if c["id"] not in processed]
    print(f"\nNew campaigns to process: {len(new_campaigns)}")

    if not new_campaigns:
        print("No new campaigns to process")
        save_processed(processed)  # Update last_checked timestamp
        sys.exit(0)

    # Process each new campaign
    total_new = 0
    newly_processed = set()
    for campaign in new_campaigns:
        new_count = process_campaign(campaign, catalog, args.dry_run)
        total_new += new_count
        newly_processed.add(campaign["id"])

    # Save results
    if not args.dry_run:
        if total_new > 0:
            save_catalog(catalog)
        processed.update(newly_processed)
        save_processed(processed)

        print(f"\n{'='*50}")
        print(f"Processed {len(newly_processed)} new campaign(s)")
        print(f"Added {total_new} new products")
        print(f"Catalog now has {len(catalog)} products")
    else:
        print(f"\n{'='*50}")
        print(f"DRY RUN - Would process {len(newly_processed)} campaign(s)")
        print(f"DRY RUN - Would add {total_new} new products")


if __name__ == "__main__":
    main()
