#!/usr/bin/env python3
"""
Import Amazon products from Cool Tools WordPress export.

Parses the WordPress XML export, extracts Amazon and geni.us links,
and adds products to the catalog with Cool Tools post metadata.

Usage:
    python catalog/import_cooltools.py cooltools.WordPress.2026-01-03.xml
    python catalog/import_cooltools.py cooltools.WordPress.2026-01-03.xml --dry-run
"""

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests

# Project paths
PROJECT_DIR = Path(__file__).parent.parent
CATALOG_FILE = PROJECT_DIR / "catalog" / "products.json"

# XML namespaces used in WordPress export
NAMESPACES = {
    'content': 'http://purl.org/rss/1.0/modules/content/',
    'wp': 'http://wordpress.org/export/1.2/',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'excerpt': 'http://wordpress.org/export/1.2/excerpt/',
}


def extract_asin(url):
    """Extract ASIN from various Amazon URL formats."""
    patterns = [
        r'/dp/([A-Z0-9]{10})',
        r'/gp/product/([A-Z0-9]{10})',
        r'/gp/aw/d/([A-Z0-9]{10})',
        r'/product/([A-Z0-9]{10})',
        r'/ASIN/([A-Z0-9]{10})',
        r'/exec/obidos/ASIN/([A-Z0-9]{10})',
    ]

    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            return match.group(1).upper()

    return None


def extract_amazon_links(content):
    """Extract all Amazon-related URLs from HTML content."""
    if not content:
        return []

    links = []

    # Find all href attributes
    href_pattern = r'href=["\']([^"\']+)["\']'
    urls = re.findall(href_pattern, content, re.IGNORECASE)

    for url in urls:
        # Check if it's an Amazon-related link
        if any(domain in url.lower() for domain in ['amazon.com', 'amzn.to', 'geni.us', 'amzn.com']):
            links.append(url)

    # Also find plain Amazon URLs not in href
    amazon_pattern = r'https?://(?:www\.)?amazon\.com[^\s<>"\']*'
    plain_urls = re.findall(amazon_pattern, content)
    for url in plain_urls:
        if url not in links:
            links.append(url)

    return links


def resolve_shortlink(url, max_retries=3):
    """Follow redirects to get the final Amazon URL."""
    if 'amazon.com' in url and ('/dp/' in url or '/ASIN/' in url or '/gp/product/' in url):
        return url  # Already a direct Amazon link

    for attempt in range(max_retries):
        try:
            # Try HEAD request first
            response = requests.head(url, allow_redirects=True, timeout=10)
            final_url = response.url

            if 'amazon.com' in final_url:
                return final_url

            # Try GET if HEAD didn't work
            response = requests.get(url, allow_redirects=True, timeout=10)
            return response.url

        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                return url

    return url


def parse_wordpress_date(date_str):
    """Parse WordPress date to YYYY-MM-DD format."""
    try:
        # Format: "2003-04-17 19:42:29"
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%d")
    except:
        return None


def parse_wordpress_export(xml_path):
    """Parse WordPress XML export and yield posts with Amazon links."""
    print(f"Parsing WordPress export: {xml_path}")

    # Parse XML
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Find all items (posts)
    channel = root.find('channel')
    items = channel.findall('item')

    print(f"Found {len(items)} items in export")

    for item in items:
        # Get post type and status
        post_type = item.find('wp:post_type', NAMESPACES)
        status = item.find('wp:status', NAMESPACES)

        # Only process published posts
        if post_type is None or post_type.text != 'post':
            continue
        if status is None or status.text != 'publish':
            continue

        # Get post metadata
        title = item.find('title').text if item.find('title') is not None else ''
        link = item.find('link').text if item.find('link') is not None else ''
        pub_date = item.find('wp:post_date', NAMESPACES)
        pub_date = pub_date.text if pub_date is not None else ''

        # Get content
        content = item.find('content:encoded', NAMESPACES)
        content = content.text if content is not None else ''

        # Extract Amazon links
        amazon_links = extract_amazon_links(content)

        if amazon_links:
            yield {
                'title': title,
                'url': link,
                'date': parse_wordpress_date(pub_date),
                'links': amazon_links,
            }


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


def main():
    parser = argparse.ArgumentParser(description="Import Cool Tools WordPress export")
    parser.add_argument("xml_file", help="Path to WordPress XML export")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be added without saving")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit number of posts to process (0 = all)")
    parser.add_argument("--skip-shortlinks", action="store_true",
                        help="Skip resolving shortlinks (faster but misses some products)")
    args = parser.parse_args()

    xml_path = Path(args.xml_file)
    if not xml_path.exists():
        print(f"Error: File not found: {xml_path}")
        sys.exit(1)

    # Load existing catalog
    catalog = load_catalog()
    original_count = len(catalog)
    print(f"Existing catalog: {original_count} products")

    # Track stats
    posts_processed = 0
    links_found = 0
    new_products = 0
    updated_products = 0
    shortlinks_resolved = 0
    shortlinks_skipped = 0

    # Collect shortlinks to resolve in batch later
    shortlinks_to_resolve = []

    # First pass: process direct Amazon links
    print("\nPass 1: Processing direct Amazon links...")
    for post in parse_wordpress_export(xml_path):
        posts_processed += 1

        if args.limit and posts_processed > args.limit:
            break

        if posts_processed % 500 == 0:
            print(f"  Processed {posts_processed} posts, found {new_products} new products...")

        for link_url in post['links']:
            links_found += 1

            # Check if it's a shortlink
            is_shortlink = 'geni.us' in link_url or 'amzn.to' in link_url

            if is_shortlink:
                if args.skip_shortlinks:
                    shortlinks_skipped += 1
                    continue
                # Queue for later resolution
                shortlinks_to_resolve.append((post, link_url))
                continue

            # Direct Amazon link - extract ASIN immediately
            asin = extract_asin(link_url)
            if not asin:
                continue

            # Build source info for this Cool Tools post
            source_info = {
                'url': post['url'],
                'title': post['title'],
                'date': post['date'],
                'source': 'cooltools'
            }

            # Check if already in catalog
            if asin in catalog:
                existing_sources = catalog[asin].get('issues', [])
                existing_urls = [s.get('url') for s in existing_sources]

                if post['url'] not in existing_urls:
                    catalog[asin]['issues'].append(source_info)
                    updated_products += 1
            else:
                catalog[asin] = {
                    'asin': asin,
                    'title': post['title'],
                    'amazon_url': f"https://www.amazon.com/dp/{asin}",
                    'affiliate_url': None,
                    'issues': [source_info],
                    'first_featured': post['date'],
                    'added_at': datetime.now().isoformat(),
                }
                new_products += 1

    # Second pass: resolve shortlinks
    if shortlinks_to_resolve and not args.skip_shortlinks:
        print(f"\nPass 2: Resolving {len(shortlinks_to_resolve)} shortlinks...")
        for i, (post, link_url) in enumerate(shortlinks_to_resolve):
            if i % 50 == 0 and i > 0:
                print(f"  Resolved {i}/{len(shortlinks_to_resolve)} shortlinks...")

            time.sleep(0.2)  # Rate limit
            final_url = resolve_shortlink(link_url)
            shortlinks_resolved += 1

            asin = extract_asin(final_url)
            if not asin:
                continue

            source_info = {
                'url': post['url'],
                'title': post['title'],
                'date': post['date'],
                'source': 'cooltools'
            }

            if asin in catalog:
                existing_sources = catalog[asin].get('issues', [])
                existing_urls = [s.get('url') for s in existing_sources]

                if post['url'] not in existing_urls:
                    catalog[asin]['issues'].append(source_info)
                    updated_products += 1
            else:
                catalog[asin] = {
                    'asin': asin,
                    'title': post['title'],
                    'amazon_url': f"https://www.amazon.com/dp/{asin}",
                    'affiliate_url': link_url,
                    'issues': [source_info],
                    'first_featured': post['date'],
                    'added_at': datetime.now().isoformat(),
                }
                new_products += 1

    # Summary
    print(f"\n{'='*50}")
    print(f"Posts processed: {posts_processed}")
    print(f"Amazon links found: {links_found}")
    print(f"Shortlinks resolved: {shortlinks_resolved}")
    print(f"Shortlinks skipped: {shortlinks_skipped}")
    print(f"New products: {new_products}")
    print(f"Updated products: {updated_products}")

    # Save catalog
    if not args.dry_run:
        save_catalog(catalog)
        print(f"\nCatalog now has {len(catalog)} products")
        print(f"Saved to: {CATALOG_FILE}")
    else:
        print(f"\nDRY RUN - Would have {len(catalog)} products")


if __name__ == "__main__":
    main()
