#!/usr/bin/env python3
"""
Harvest Amazon products from a new Recomendo issue.

Usage:
    python harvest.py                    # Interactive mode (paste content)
    python harvest.py --file issue.html  # From file
    python harvest.py --url https://...  # Fetch from URL (future)
"""

import argparse
import sys
import time
from datetime import datetime

import config
from catalog.import_substack import (
    extract_amazon_links,
    resolve_shortlink,
    extract_asin,
    is_shortlink,
    load_catalog,
    save_catalog,
    merge_product,
    build_product_entry,
)


def harvest_from_text(
    content: str,
    issue_url: str,
    issue_title: str,
    issue_date: str,
) -> dict:
    """
    Extract products from text/HTML content and add to catalog.

    Returns stats dict with counts.
    """
    catalog = load_catalog()
    initial_count = len(catalog)

    # Extract links
    links = extract_amazon_links(content)
    print(f"Found {len(links)} Amazon-related links")

    if not links:
        print("No Amazon links found in content.")
        return {"new_products": 0, "updated_products": 0, "failed": 0}

    stats = {"new_products": 0, "updated_products": 0, "failed": 0}

    # Process each link
    for i, link in enumerate(links):
        original_url = link["url"]
        affiliate_url = None
        amazon_url = None
        asin = None

        print(f"\n[{i+1}/{len(links)}] Processing: {original_url[:60]}...")

        if link["is_shortlink"]:
            # Rate limit
            if i > 0:
                time.sleep(config.SHORTLINK_RATE_LIMIT)

            resolved = resolve_shortlink(original_url)
            if resolved:
                amazon_url = resolved
                asin = extract_asin(resolved)
                if "geni.us" in original_url:
                    affiliate_url = original_url
                print(f"  Resolved to: {amazon_url}")
            else:
                print(f"  Failed to resolve shortlink")
                stats["failed"] += 1
                continue
        else:
            amazon_url = original_url
            asin = extract_asin(original_url)

        if not asin:
            print(f"  Could not extract ASIN")
            stats["failed"] += 1
            continue

        print(f"  ASIN: {asin}")

        # Clean URL
        from urllib.parse import urlparse
        parsed = urlparse(amazon_url)
        clean_url = f"https://{parsed.netloc}/dp/{asin}"

        # Add or merge into catalog
        issue_info = {
            "url": issue_url,
            "title": issue_title,
            "date": issue_date,
        }

        if asin in catalog:
            catalog[asin] = merge_product(catalog[asin], issue_info)
            if affiliate_url and not catalog[asin].get("affiliate_url"):
                catalog[asin]["affiliate_url"] = affiliate_url
            stats["updated_products"] += 1
            print(f"  Updated existing product (now in {len(catalog[asin]['issues'])} issues)")
        else:
            catalog[asin] = build_product_entry(
                asin=asin,
                amazon_url=clean_url,
                affiliate_url=affiliate_url,
                title=link["link_text"] or f"Product {asin}",
                issue=issue_info,
            )
            stats["new_products"] += 1
            print(f"  Added new product: {link['link_text'][:50] if link['link_text'] else asin}")

    # Save catalog
    save_catalog(catalog)

    print(f"\n{'='*50}")
    print(f"Harvest complete!")
    print(f"  New products: {stats['new_products']}")
    print(f"  Updated products: {stats['updated_products']}")
    print(f"  Failed: {stats['failed']}")
    print(f"  Total catalog size: {len(catalog)}")

    return stats


def interactive_mode():
    """Run harvest in interactive mode with prompts."""
    print("=" * 50)
    print("Recomendo Deals - Harvest New Issue")
    print("=" * 50)

    # Get issue metadata
    print("\nEnter issue metadata:")
    issue_url = input("Issue URL (e.g., https://recomendo.substack.com/p/...): ").strip()
    issue_title = input("Issue title: ").strip()
    issue_date = input("Issue date (YYYY-MM-DD): ").strip()

    if not issue_date:
        issue_date = datetime.now().strftime("%Y-%m-%d")
        print(f"  Using today's date: {issue_date}")

    # Get content
    print("\nPaste the issue content (HTML or plain text).")
    print("When done, enter a line with just '---' and press Enter:")
    print()

    lines = []
    while True:
        try:
            line = input()
            if line.strip() == "---":
                break
            lines.append(line)
        except EOFError:
            break

    content = "\n".join(lines)

    if not content.strip():
        print("No content provided. Exiting.")
        return

    print(f"\nReceived {len(content)} characters of content.")

    # Process
    harvest_from_text(content, issue_url, issue_title, issue_date)


def main():
    parser = argparse.ArgumentParser(
        description="Harvest Amazon products from a new Recomendo issue"
    )
    parser.add_argument(
        "--file", "-f",
        help="Path to HTML file containing the issue"
    )
    parser.add_argument(
        "--url", "-u",
        help="Issue URL on Substack (for metadata)"
    )
    parser.add_argument(
        "--title", "-t",
        help="Issue title"
    )
    parser.add_argument(
        "--date", "-d",
        help="Issue date (YYYY-MM-DD)"
    )

    args = parser.parse_args()

    if args.file:
        # File mode
        from pathlib import Path
        file_path = Path(args.file)
        if not file_path.exists():
            print(f"File not found: {file_path}")
            sys.exit(1)

        content = file_path.read_text(encoding="utf-8")
        issue_url = args.url or ""
        issue_title = args.title or file_path.stem
        issue_date = args.date or datetime.now().strftime("%Y-%m-%d")

        harvest_from_text(content, issue_url, issue_title, issue_date)
    else:
        # Interactive mode
        interactive_mode()


if __name__ == "__main__":
    main()
