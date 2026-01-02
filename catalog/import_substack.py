#!/usr/bin/env python3
"""
Parse Substack export files to extract Amazon product links.

Handles:
- HTML export files (individual post files)
- CSV export format
- geni.us and amzn.to shortlinks (resolves to get ASIN)
- Direct Amazon URLs with various formats
"""

import csv
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs

# Flush output immediately for progress visibility
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

# Add parent to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
import config


# ASIN patterns in Amazon URLs
ASIN_PATTERNS = [
    r"/dp/([A-Z0-9]{10})",
    r"/gp/product/([A-Z0-9]{10})",
    r"/gp/aw/d/([A-Z0-9]{10})",
    r"/product/([A-Z0-9]{10})",
    r"/d/([A-Z0-9]{10})",
    r"/product-reviews/([A-Z0-9]{10})",
    r"/customer-reviews/([A-Z0-9]{10})",
    r"/exec/obidos/ASIN/([A-Z0-9]{10})",
    r"/gp/video/detail/([A-Z0-9]{10})",  # Prime Video
    r"/detail/([A-Z0-9]{10})",  # Generic detail page
]

# URL paths to skip (not products)
SKIP_URL_PATTERNS = [
    r"kdp\.amazon\.com",  # Kindle Direct Publishing
    r"/help/",  # Help pages
    r"/gp/help/",  # Help pages
    r"/b\?",  # Browse nodes
    r"/s\?",  # Search pages
]

# Cache for resolved shortlinks
_shortlink_cache: dict[str, Optional[str]] = {}


def extract_asin(url: str) -> Optional[str]:
    """Extract ASIN from an Amazon URL."""
    for pattern in ASIN_PATTERNS:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return None


def should_skip_url(url: str) -> bool:
    """Check if URL should be skipped (not a product page)."""
    for pattern in SKIP_URL_PATTERNS:
        if re.search(pattern, url, re.IGNORECASE):
            return True
    return False


def is_amazon_url(url: str) -> bool:
    """Check if URL is a direct Amazon product URL."""
    try:
        parsed = urlparse(url)
        if not any(domain in parsed.netloc for domain in config.AMAZON_DOMAINS):
            return False
        # Skip non-product URLs
        if should_skip_url(url):
            return False
        return True
    except Exception:
        return False


def is_shortlink(url: str) -> bool:
    """Check if URL is a shortlink that needs resolution."""
    try:
        parsed = urlparse(url)
        return any(domain in parsed.netloc for domain in config.SHORTLINK_DOMAINS)
    except Exception:
        return False


def resolve_shortlink(url: str) -> Optional[str]:
    """
    Resolve a shortlink (geni.us, amzn.to) to its final Amazon URL.
    Returns None if resolution fails or doesn't lead to Amazon.
    """
    if url in _shortlink_cache:
        return _shortlink_cache[url]

    try:
        headers = {"User-Agent": config.SHORTLINK_USER_AGENT}

        # For geni.us, we need to follow the /opt/0 pattern
        if "geni.us" in url:
            # First try the /opt/0 direct redirect
            opt_url = url.rstrip("/") + "/opt/0"
            response = requests.get(
                opt_url,
                allow_redirects=True,
                timeout=config.SHORTLINK_TIMEOUT,
                headers=headers,
            )
            if is_amazon_url(response.url):
                _shortlink_cache[url] = response.url
                return response.url

        # Standard redirect following for amzn.to and others
        response = requests.get(
            url,
            allow_redirects=True,
            timeout=config.SHORTLINK_TIMEOUT,
            headers=headers,
        )
        final_url = response.url

        # Check if final URL is Amazon
        if is_amazon_url(final_url):
            _shortlink_cache[url] = final_url
            return final_url

        _shortlink_cache[url] = None
        return None

    except requests.RequestException as e:
        print(f"  Warning: Failed to resolve {url}: {e}")
        _shortlink_cache[url] = None
        return None


def extract_amazon_links(html_content: str) -> list[dict]:
    """
    Extract all Amazon-related links from HTML content.

    Returns list of dicts with:
    - url: original URL found
    - link_text: text of the link
    - is_shortlink: whether it needs resolution
    """
    soup = BeautifulSoup(html_content, "lxml")
    links = []

    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href", "")
        link_text = a_tag.get_text(strip=True)

        if is_amazon_url(href) or is_shortlink(href):
            links.append({
                "url": href,
                "link_text": link_text,
                "is_shortlink": is_shortlink(href),
            })

    return links


def parse_substack_html(file_path: Path) -> Optional[dict]:
    """
    Parse a single Substack HTML export file.

    Returns dict with:
    - issue_url: URL of the issue (if found)
    - issue_title: title of the issue
    - issue_date: publication date
    - links: list of Amazon links found
    """
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return None

    soup = BeautifulSoup(content, "lxml")

    # Extract issue metadata
    title = None
    date = None
    issue_url = None

    # Try to find title
    title_tag = soup.find("title")
    if title_tag:
        title = title_tag.get_text(strip=True)

    # Try h1 if no title
    if not title:
        h1_tag = soup.find("h1")
        if h1_tag:
            title = h1_tag.get_text(strip=True)

    # Try to find date - look for common patterns
    # Substack often has meta tags or time elements
    time_tag = soup.find("time")
    if time_tag:
        date_str = time_tag.get("datetime") or time_tag.get_text(strip=True)
        try:
            date = date_parser.parse(date_str).strftime("%Y-%m-%d")
        except Exception:
            pass

    # Try meta tags for date
    if not date:
        for meta in soup.find_all("meta"):
            prop = meta.get("property", "") or meta.get("name", "")
            if "date" in prop.lower() or "published" in prop.lower():
                try:
                    date = date_parser.parse(meta.get("content", "")).strftime("%Y-%m-%d")
                    break
                except Exception:
                    pass

    # Try to find canonical URL
    canonical = soup.find("link", rel="canonical")
    if canonical:
        issue_url = canonical.get("href")

    # Also check og:url
    if not issue_url:
        og_url = soup.find("meta", property="og:url")
        if og_url:
            issue_url = og_url.get("content")

    # Extract Amazon links
    links = extract_amazon_links(content)

    if not links:
        return None

    return {
        "issue_url": issue_url,
        "issue_title": title or file_path.stem,
        "issue_date": date,
        "links": links,
        "source_file": str(file_path.name),
    }


def load_posts_metadata(csv_path: Path) -> dict[str, dict]:
    """
    Load post metadata from posts.csv.

    Returns dict keyed by post_id (the filename prefix) with metadata.
    """
    metadata = {}

    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            for row in reader:
                # post_id column contains "{id}.{slug}"
                post_id_full = row.get("post_id", "")
                if not post_id_full:
                    continue

                # Extract slug for URL building
                parts = post_id_full.split(".", 1)
                post_id = parts[0] if parts else post_id_full
                slug = parts[1] if len(parts) > 1 else ""

                # Parse date
                date_str = row.get("post_date", "")
                date = None
                if date_str:
                    try:
                        date = date_parser.parse(date_str).strftime("%Y-%m-%d")
                    except Exception:
                        pass

                # Build issue URL from slug
                issue_url = f"https://recomendo.substack.com/p/{slug}" if slug else None

                metadata[post_id] = {
                    "post_id": post_id,
                    "slug": slug,
                    "title": row.get("title", ""),
                    "subtitle": row.get("subtitle", ""),
                    "date": date,
                    "issue_url": issue_url,
                }

    except Exception as e:
        print(f"Error loading posts.csv: {e}")

    return metadata


def parse_substack_export(export_dir: Path) -> list[dict]:
    """
    Parse Substack export with posts.csv metadata + posts/*.html content.

    Returns list of issues with their Amazon links.
    """
    issues = []

    # Load metadata from posts.csv
    posts_csv = export_dir / "posts.csv"
    if not posts_csv.exists():
        print(f"posts.csv not found in {export_dir}")
        return issues

    metadata = load_posts_metadata(posts_csv)
    print(f"Loaded metadata for {len(metadata)} posts")

    # Find HTML files in posts/ subdirectory
    posts_dir = export_dir / "posts"
    if not posts_dir.exists():
        print(f"posts/ directory not found in {export_dir}")
        return issues

    html_files = list(posts_dir.glob("*.html"))
    print(f"Found {len(html_files)} HTML files in posts/")

    for html_file in html_files:
        # Extract post_id from filename (e.g., "112077967.ninja-creami..." -> "112077967")
        post_id = html_file.stem.split(".")[0]

        # Get metadata for this post
        meta = metadata.get(post_id, {})

        # Read and parse HTML content
        try:
            content = html_file.read_text(encoding="utf-8")
        except Exception as e:
            print(f"Error reading {html_file}: {e}")
            continue

        # Extract Amazon links
        links = extract_amazon_links(content)

        if links:
            issues.append({
                "issue_url": meta.get("issue_url"),
                "issue_title": meta.get("title") or html_file.stem,
                "issue_date": meta.get("date"),
                "links": links,
                "source_file": html_file.name,
            })

    return issues


def parse_substack_csv(file_path: Path) -> list[dict]:
    """
    Parse Substack CSV export (legacy format with body_html column).

    CSV typically has columns like: title, post_date, subtitle, body_html, etc.
    Returns list of parsed issues.
    """
    issues = []

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            for row in reader:
                # Check if this CSV has body content (older export format)
                body = row.get("body_html") or row.get("body") or row.get("content") or ""
                if not body:
                    continue  # Skip if no body - use HTML files instead

                title = row.get("title") or row.get("Title") or ""
                date_str = row.get("post_date") or row.get("date") or row.get("published_at") or ""
                url = row.get("post_url") or row.get("url") or row.get("canonical_url") or ""

                # Parse date
                date = None
                if date_str:
                    try:
                        date = date_parser.parse(date_str).strftime("%Y-%m-%d")
                    except Exception:
                        pass

                # Extract links from body HTML
                links = extract_amazon_links(body)

                if links:
                    issues.append({
                        "issue_url": url,
                        "issue_title": title,
                        "issue_date": date,
                        "links": links,
                        "source_file": str(file_path.name),
                    })

    except Exception as e:
        print(f"Error parsing CSV {file_path}: {e}")

    return issues


def build_product_entry(
    asin: str,
    amazon_url: str,
    affiliate_url: Optional[str],
    title: str,
    issue: dict,
) -> dict:
    """Create a product entry for the catalog."""
    now = datetime.now().isoformat()

    return {
        "asin": asin,
        "title": title,
        "amazon_url": amazon_url,
        "affiliate_url": affiliate_url,
        "issues": [
            {
                "url": issue.get("issue_url"),
                "title": issue.get("issue_title"),
                "date": issue.get("issue_date"),
            }
        ],
        "first_featured": issue.get("issue_date"),
        "added_at": now,
    }


def merge_product(existing: dict, new_issue: dict) -> dict:
    """Merge a new issue into an existing product entry."""
    # Check if issue already exists
    existing_urls = {i.get("url") for i in existing["issues"]}
    new_url = new_issue.get("url")

    if new_url and new_url not in existing_urls:
        existing["issues"].append(new_issue)

        # Update first_featured if this issue is older
        new_date = new_issue.get("date")
        if new_date and existing.get("first_featured"):
            if new_date < existing["first_featured"]:
                existing["first_featured"] = new_date
        elif new_date and not existing.get("first_featured"):
            existing["first_featured"] = new_date

    return existing


def import_all(export_dir: Path = None, output_path: Path = None) -> dict:
    """
    Process all Substack export files and build the product catalog.

    Args:
        export_dir: Directory containing export files (default: config.SUBSTACK_EXPORT_DIR)
        output_path: Where to save catalog (default: config.CATALOG_FILE)

    Returns:
        The complete product catalog dict
    """
    export_dir = export_dir or config.SUBSTACK_EXPORT_DIR
    output_path = output_path or config.CATALOG_FILE

    if not export_dir.exists():
        print(f"Export directory not found: {export_dir}")
        print(f"Please add your Substack export files to: {export_dir}")
        return {}

    catalog: dict[str, dict] = {}
    shortlinks_to_resolve: list[tuple[str, dict, dict]] = []  # (url, link_info, issue)

    # Collect all issues
    all_issues = []

    # Try new format first: posts.csv + posts/*.html
    posts_csv = export_dir / "posts.csv"
    posts_dir = export_dir / "posts"

    if posts_csv.exists() and posts_dir.exists():
        print("Detected Substack export format: posts.csv + posts/*.html")
        all_issues = parse_substack_export(export_dir)
    else:
        # Fallback to legacy format: standalone HTML/CSV files
        print("Using legacy format: standalone HTML/CSV files")

        # Process HTML files in root
        html_files = list(export_dir.glob("*.html")) + list(export_dir.glob("*.htm"))
        print(f"Found {len(html_files)} HTML files")

        for html_file in html_files:
            result = parse_substack_html(html_file)
            if result:
                all_issues.append(result)

        # Process CSV files with body content
        csv_files = [f for f in export_dir.glob("*.csv") if f.name != "posts.csv"]
        print(f"Found {len(csv_files)} CSV files")

        for csv_file in csv_files:
            issues = parse_substack_csv(csv_file)
            all_issues.extend(issues)

    print(f"Total issues with Amazon links: {len(all_issues)}")

    # First pass: collect all shortlinks that need resolution
    for issue in all_issues:
        for link in issue["links"]:
            if link["is_shortlink"]:
                shortlinks_to_resolve.append((link["url"], link, issue))

    # Resolve shortlinks with rate limiting
    unique_shortlinks = list(set(url for url, _, _ in shortlinks_to_resolve))
    print(f"Resolving {len(unique_shortlinks)} unique shortlinks...")

    for i, url in enumerate(unique_shortlinks):
        if i > 0:
            time.sleep(config.SHORTLINK_RATE_LIMIT)
        print(f"  [{i+1}/{len(unique_shortlinks)}] {url[:50]}...")
        resolve_shortlink(url)  # Populates cache

    # Second pass: build catalog
    print("Building catalog...")
    stats = {"products": 0, "links_processed": 0, "failed_resolutions": 0}

    for issue in all_issues:
        for link in issue["links"]:
            stats["links_processed"] += 1
            original_url = link["url"]
            affiliate_url = None
            amazon_url = None
            asin = None

            if link["is_shortlink"]:
                # Use cached resolution
                resolved = _shortlink_cache.get(original_url)
                if resolved:
                    amazon_url = resolved
                    asin = extract_asin(resolved)
                    # Keep original as affiliate URL if it's geni.us
                    if "geni.us" in original_url:
                        affiliate_url = original_url
                else:
                    stats["failed_resolutions"] += 1
                    continue
            else:
                amazon_url = original_url
                asin = extract_asin(original_url)

            if not asin:
                print(f"  Warning: Could not extract ASIN from {amazon_url}")
                continue

            # Clean up amazon_url and add our affiliate tag
            parsed = urlparse(amazon_url)
            clean_url = f"https://{parsed.netloc}/dp/{asin}?tag=recomendos-20"

            # Build or merge product entry
            if asin in catalog:
                issue_info = {
                    "url": issue.get("issue_url"),
                    "title": issue.get("issue_title"),
                    "date": issue.get("issue_date"),
                }
                catalog[asin] = merge_product(catalog[asin], issue_info)
                # Update affiliate_url if we found a geni.us link
                if affiliate_url and not catalog[asin].get("affiliate_url"):
                    catalog[asin]["affiliate_url"] = affiliate_url
            else:
                catalog[asin] = build_product_entry(
                    asin=asin,
                    amazon_url=clean_url,
                    affiliate_url=affiliate_url,
                    title=link["link_text"] or f"Product {asin}",
                    issue={
                        "issue_url": issue.get("issue_url"),
                        "issue_title": issue.get("issue_title"),
                        "issue_date": issue.get("issue_date"),
                    },
                )
                stats["products"] += 1

    # Save catalog
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)

    print(f"\nDone!")
    print(f"  Products found: {stats['products']}")
    print(f"  Links processed: {stats['links_processed']}")
    print(f"  Failed resolutions: {stats['failed_resolutions']}")
    print(f"  Catalog saved to: {output_path}")

    return catalog


def load_catalog(path: Path = None) -> dict:
    """Load existing catalog from disk."""
    path = path or config.CATALOG_FILE
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_catalog(catalog: dict, path: Path = None):
    """Save catalog to disk."""
    path = path or config.CATALOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    import_all()
