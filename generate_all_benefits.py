#!/usr/bin/env python3
"""
Generate benefit descriptions for all products in the catalog.

This is a one-time script to pre-populate the benefit_description field
for all products. It fetches the original article HTML and uses Claude API
to generate a one-sentence benefit description.

Usage:
    python generate_all_benefits.py                             # Process all products without benefits
    python generate_all_benefits.py --limit 100                # Process at most 100 products
    python generate_all_benefits.py --dry-run                  # Show what would be processed
    python generate_all_benefits.py --force --source cooltools  # Regenerate all Cool Tools descriptions
    python generate_all_benefits.py --asin B00MQ5KU7U          # Regenerate a specific product
"""

import argparse
import json
import os
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

import config
from utils import call_claude


def load_deals() -> dict:
    """Load deals.json with Keepa-sourced titles and prices."""
    deals_path = config.CATALOG_DIR / "deals.json"
    if not deals_path.exists():
        return {}
    with open(deals_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("deals", {})


# Article titles that got stored as product titles during Cool Tools import
ARTICLE_TITLE_KEYWORDS = [
    'gift guide', 'holiday', 'picks', 'cool tools', 'untried',
    'videocast', 'podcast', 'show and tell', 'complete 20',
    "what's in my bag", "what's in my survival kit",
    "what’s in my bag", "what’s in my survival kit",
]


def is_article_title(title: str) -> bool:
    """Check if a title looks like an article title rather than a product name."""
    lower = (title or "").lower()
    return any(kw in lower for kw in ARTICLE_TITLE_KEYWORDS)


def fix_catalog_titles(catalog: dict, deals: dict) -> int:
    """Fix catalog entries that have article titles instead of product titles.

    Uses Keepa titles from deals.json when available.
    """
    fixed = 0
    for asin, product in catalog.items():
        if is_article_title(product.get("title", "")):
            keepa_title = deals.get(asin, {}).get("title", "")
            if keepa_title and not is_article_title(keepa_title):
                catalog[asin]["title"] = keepa_title
                fixed += 1
    return fixed


def load_catalog() -> dict:
    """Load the full product catalog.

    Always uses the full catalog (products.json), never the dev sample,
    since benefit generation must cover the real 2900-item catalog.
    """
    full_path = config.CATALOG_DIR / "products.json"
    with open(full_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_catalog(catalog: dict):
    """Save back to products.json (full catalog), not the dev sample."""
    full_path = config.CATALOG_DIR / "products.json"
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2)


def fetch_article_html(url: str) -> str:
    """Fetch article HTML with proper headers."""
    import requests

    headers = {
        "User-Agent": config.SHORTLINK_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"    Warning: Failed to fetch article: {e}")
        return ""


def extract_product_context(html: str, asin: str, product_title: str) -> str:
    """Extract text around the Amazon product link from article HTML."""
    import re
    from html.parser import HTMLParser

    if not html:
        return ""

    # Simple HTML to text conversion
    class HTMLTextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text_parts = []
            self.in_script = False
            self.in_style = False

        def handle_starttag(self, tag, attrs):
            if tag in ('script', 'style'):
                self.in_script = True
            elif tag in ('p', 'br', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'):
                self.text_parts.append('\n')

        def handle_endtag(self, tag):
            if tag in ('script', 'style'):
                self.in_script = False
            elif tag in ('p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'):
                self.text_parts.append('\n')

        def handle_data(self, data):
            if not self.in_script:
                self.text_parts.append(data)

        def get_text(self):
            return ''.join(self.text_parts)

    try:
        extractor = HTMLTextExtractor()
        extractor.feed(html)
        text = extractor.get_text()
    except Exception:
        # Fallback: just strip tags
        text = re.sub(r'<[^>]+>', ' ', html)

    # Clean up whitespace
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\n\s*\n', '\n\n', text)

    # Strategy 1: Find ASIN link in HTML (most reliable for Amazon affiliate links)
    link_match = re.search(rf'<a[^>]*href=["\'][^"\']*{asin}[^"\']*["\'][^>]*>([^<]+)</a>', html, re.IGNORECASE)
    link_text = link_match.group(1).strip() if link_match else ""

    best_pos = -1
    if link_text:
        match = re.search(re.escape(link_text[:40]), text, re.IGNORECASE)
        if match:
            best_pos = match.start()

    # Strategy 2: Search plain text for ASIN URLs or product title words
    if best_pos < 0:
        patterns = [
            rf'amazon\.com/dp/{asin}',
            rf'amazon\.com.*?{asin}',
        ]
        if product_title:
            title_words = [w for w in product_title.split()[:4] if len(w) > 3]
            if title_words:
                patterns.append(r'\b' + r'\b.*?\b'.join(re.escape(w) for w in title_words) + r'\b')

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                best_pos = match.start()
                break

    # Extract surrounding context (narrower window to avoid adjacent product bleed)
    if best_pos >= 0:
        start = max(0, best_pos - 300)
        end = min(len(text), best_pos + 400)

        # Try to start/end at sentence boundaries
        if start > 0:
            sentence_start = text.rfind('.', start - 100, start)
            if sentence_start > 0:
                start = sentence_start + 1
        if end < len(text):
            sentence_end = text.find('.', end, end + 100)
            if sentence_end > 0:
                end = sentence_end + 1

        return text[start:end].strip()

    # Fallback: return first ~1000 chars of article body
    # Try to find start of article content
    body_markers = ['<article', '<main', 'class="post"', 'class="content"', '<body']
    for marker in body_markers:
        pos = html.lower().find(marker)
        if pos >= 0:
            return text[:1500].strip()

    return text[:1500].strip()


def generate_benefit_description(asin: str, product: dict, deals: dict = None) -> str:
    """
    Generate a one-sentence benefit description for a product.

    Returns empty string if generation fails.
    """
    # Get source article URL - prefer Recomendo over Cool Tools
    issues = product.get("issues", [])
    if not issues:
        print(f"    Warning: No source article for {asin}")
        return ""

    recomendo_issues = [i for i in issues if i.get("source") != "cooltools"]
    source_issue = recomendo_issues[0] if recomendo_issues else issues[0]
    article_url = source_issue.get("url", "")

    if not article_url:
        return ""

    # Use best available product title (Keepa > catalog, skip article titles)
    product_title = product.get("title", "")
    keepa_title = deals.get(asin, {}).get("title", "") if deals else ""
    if keepa_title and (is_article_title(product_title) or not product_title):
        product_title = keepa_title

    # Check for ASIN reassignment (catalog title doesn't match Amazon title)
    title_mismatch = False
    if keepa_title and product_title:
        from difflib import SequenceMatcher
        similarity = SequenceMatcher(None, keepa_title.lower(), product.get("title", "").lower()).ratio()
        if similarity < 0.3:
            title_mismatch = True
            product_title = keepa_title  # Use Amazon title
            print(f"    Title mismatch — using Amazon title: {keepa_title[:50]}")

    # Try source article (skip if title mismatch — article is about wrong product)
    context = None
    if not title_mismatch:
        html = fetch_article_html(article_url)
        if html:
            context = extract_product_context(html, asin, product_title)
            if not context:
                print(f"    Warning: Could not extract context in article")

    # Fall back to PA API features if no article context
    features = []
    if not context:
        try:
            from pa_api import get_prices_for_asins
            pa_data = get_prices_for_asins([asin])
            info = pa_data.get(asin, {})
            features = info.get("product_features", [])
            if features:
                print(f"    Using PA API features ({len(features)} features)")
        except Exception as e:
            print(f"    PA API fallback failed: {e}")

    if not context and not features:
        print(f"    Warning: No article context or product features for {asin}")
        return ""

    # Build prompt — truncate features to keep prompt reasonable
    features_text = ""
    if features:
        truncated = [f[:150] for f in features[:3]]
        features_text = "\nAmazon product features:\n" + "\n".join(f"- {f}" for f in truncated)

    if context:
        prompt = f"""Given this excerpt from a product review page, write ONE sentence describing the key benefit of "{product_title}". The page may review multiple products — ONLY describe "{product_title}", ignore any other products mentioned.

Rules:
- Do NOT mention the product name or brand
- Do NOT mention the price
- Start directly with what the product does or why it's useful
- Be specific and concrete

Product: {product_title}
Review excerpt: {context}{features_text}

Write only the benefit sentence, no preamble."""
    else:
        prompt = f"""Based on the Amazon product listing below, write ONE sentence describing the key benefit of "{product_title}".

Rules:
- Do NOT mention the product name or brand
- Do NOT mention the price
- Start directly with what the product does or why it's useful
- Be specific and concrete

Product: {product_title}
{features_text}

Write only the benefit sentence, no preamble."""

    try:
        benefit = call_claude(prompt, model="haiku")

        # Reject non-descriptions (Claude couldn't match the product)
        if benefit.lower().startswith("i cannot") or benefit.lower().startswith("i'm unable"):
            print(f"    Warning: Claude couldn't match product in context")
            return ""

        return benefit

    except Exception as e:
        print(f"    Warning: Claude API error for {asin}: {e}")
        return ""


def main():
    parser = argparse.ArgumentParser(description="Generate benefit descriptions for all products")
    parser.add_argument("--limit", type=int, help="Maximum number of products to process")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed without making changes")
    parser.add_argument("--save-interval", type=int, default=50, help="Save progress every N products (default: 50)")
    parser.add_argument("--force", action="store_true", help="Regenerate existing benefit descriptions")
    parser.add_argument("--source", choices=["cooltools", "recomendo"], help="Only process products from this source")
    parser.add_argument("--asin", help="Regenerate for a specific ASIN")
    parser.add_argument("--claude-cli", action="store_true", help="Use claude CLI (Max subscription) instead of Anthropic API")
    args = parser.parse_args()

    if args.claude_cli:
        os.environ["USE_CLAUDE_CLI"] = "1"
        print("Using claude CLI (Max subscription)")

    print("Loading catalog...")
    catalog = load_catalog()
    total_products = len(catalog)
    print(f"Total products in catalog: {total_products}")

    # Load deals.json for Keepa-sourced titles
    deals = load_deals()
    if deals:
        fixed = fix_catalog_titles(catalog, deals)
        if fixed:
            print(f"Fixed {fixed} article-style titles from Keepa data")
            save_catalog(catalog)

    # Build candidate list
    if args.asin:
        if args.asin not in catalog:
            print(f"ASIN {args.asin} not found in catalog")
            return
        candidates = [(args.asin, catalog[args.asin])]
    else:
        candidates = list(catalog.items())

    # Filter by source
    if args.source:
        if args.source == "cooltools":
            candidates = [
                (asin, p) for asin, p in candidates
                if any(i.get("source") == "cooltools" for i in p.get("issues", []))
            ]
        else:
            candidates = [
                (asin, p) for asin, p in candidates
                if any(i.get("source") != "cooltools" for i in p.get("issues", []))
            ]
        print(f"Filtered to {len(candidates)} {args.source} products")

    # Filter by benefit status (--asin always regenerates, --force regenerates all)
    if args.asin or args.force:
        to_process = candidates
        if not args.asin:
            has_existing = sum(1 for _, p in to_process if p.get("benefit_description"))
            print(f"Regenerating {has_existing} existing + {len(to_process) - has_existing} missing descriptions")
    else:
        to_process = [(asin, p) for asin, p in candidates if not p.get("benefit_description")]
        print(f"Products without benefit_description: {len(to_process)}")

    if args.dry_run:
        print(f"\nDry run - would process {len(to_process)} products:")
        for i, (asin, product) in enumerate(to_process[:10]):
            existing = "  [has description]" if product.get("benefit_description") else ""
            print(f"  {i+1}. {asin}: {product.get('title', 'No title')[:50]}{existing}")
        if len(to_process) > 10:
            print(f"  ... and {len(to_process) - 10} more")
        return


    # Limit if requested
    if args.limit:
        to_process = to_process[:args.limit]
        print(f"Processing limited to {args.limit} products")

    print(f"\nProcessing {len(to_process)} products...")
    print(f"Estimated time: ~{len(to_process) * 1.5 / 60:.1f} minutes\n")

    processed = 0
    success = 0
    failed = 0

    for i, (asin, product) in enumerate(to_process):
        processed += 1
        title = product.get("title", "No title")[:40]
        print(f"[{i+1}/{len(to_process)}] {asin}: {title}...")

        # Rate limit
        if i > 0:
            time.sleep(0.5)

        benefit = generate_benefit_description(asin, product, deals)
        if benefit:
            catalog[asin]["benefit_description"] = benefit
            success += 1
            print(f"    OK: {benefit[:60]}...")
        else:
            failed += 1
            print(f"    FAILED")

        # Save progress incrementally
        if processed % args.save_interval == 0:
            print(f"\n  Saving progress ({processed} processed)...")
            save_catalog(catalog)
            print(f"  Saved. Success: {success}, Failed: {failed}\n")

    # Final save
    print(f"\nSaving final results...")
    save_catalog(catalog)

    print(f"\nDone!")
    print(f"  Processed: {processed}")
    print(f"  Success: {success}")
    print(f"  Failed: {failed}")

    # Show updated stats
    final_with_benefits = sum(1 for p in catalog.values() if p.get("benefit_description"))
    print(f"\nProducts with benefit_description: {final_with_benefits}/{total_products} ({100*final_with_benefits/total_products:.1f}%)")


if __name__ == "__main__":
    main()
