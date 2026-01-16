#!/usr/bin/env python3
"""
Generate benefit descriptions for all products in the catalog.

This is a one-time script to pre-populate the benefit_description field
for all products. It fetches the original article HTML and uses Claude API
to generate a one-sentence benefit description.

Usage:
    python generate_all_benefits.py                 # Process all products without benefits
    python generate_all_benefits.py --limit 100    # Process at most 100 products
    python generate_all_benefits.py --dry-run      # Show what would be processed
"""

import argparse
import json
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

import config

# Anthropic client for benefit generation
try:
    import anthropic
    ANTHROPIC_CLIENT = anthropic.Anthropic()
except Exception as e:
    print(f"Warning: Could not initialize Anthropic client: {e}")
    ANTHROPIC_CLIENT = None


def load_catalog() -> dict:
    """Load the full product catalog."""
    with open(config.CATALOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_catalog(catalog: dict):
    """Save the product catalog."""
    with open(config.CATALOG_FILE, "w", encoding="utf-8") as f:
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

    # Find the product mention - look for ASIN, product title, or Amazon link
    patterns = [
        rf'amazon\.com/dp/{asin}',
        rf'amazon\.com.*?{asin}',
        rf'amzn\.to/\w+',
        rf'geni\.us/\w+',
    ]

    # Also search for product title words
    if product_title:
        # Use first few significant words of title
        title_words = [w for w in product_title.split()[:4] if len(w) > 3]
        if title_words:
            patterns.append(r'\b' + r'\b.*?\b'.join(re.escape(w) for w in title_words) + r'\b')

    # Search HTML for link context (more reliable for finding the exact mention)
    link_match = re.search(rf'<a[^>]*href=["\'][^"\']*{asin}[^"\']*["\'][^>]*>([^<]+)</a>', html, re.IGNORECASE)
    link_text = link_match.group(1) if link_match else ""

    # Find position in text
    best_pos = -1
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            best_pos = match.start()
            break

    # If we found it in HTML link, search for link text in plain text
    if best_pos < 0 and link_text:
        match = re.search(re.escape(link_text[:30]), text, re.IGNORECASE)
        if match:
            best_pos = match.start()

    # Extract surrounding context (about 500 chars before and after)
    if best_pos >= 0:
        start = max(0, best_pos - 500)
        end = min(len(text), best_pos + 500)

        # Try to start/end at sentence boundaries
        if start > 0:
            sentence_start = text.rfind('.', start - 200, start)
            if sentence_start > 0:
                start = sentence_start + 1
        if end < len(text):
            sentence_end = text.find('.', end, end + 200)
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


def generate_benefit_description(asin: str, product: dict) -> str:
    """
    Generate a one-sentence benefit description for a product.

    Returns empty string if generation fails.
    """
    if not ANTHROPIC_CLIENT:
        print(f"    Warning: Anthropic client not available for {asin}")
        return ""

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

    product_title = product.get("title", "")

    # Fetch article HTML
    html = fetch_article_html(article_url)
    if not html:
        return ""

    # Extract context around product mention
    context = extract_product_context(html, asin, product_title)
    if not context:
        print(f"    Warning: Could not extract context for {asin}")
        return ""

    # Generate benefit description using Claude
    try:
        prompt = f"""Given this excerpt from a product review, write ONE sentence describing the key benefit of this product. Focus on what makes it useful or special. Be specific and concrete.

Rules:
- Do NOT mention the product name or brand
- Do NOT mention the price
- Start directly with what the product does or why it's useful

Product: {product_title}
Review excerpt: {context}

Write only the benefit sentence, no preamble."""

        response = ANTHROPIC_CLIENT.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )

        benefit = response.content[0].text.strip()
        return benefit

    except Exception as e:
        print(f"    Warning: Claude API error for {asin}: {e}")
        return ""


def main():
    parser = argparse.ArgumentParser(description="Generate benefit descriptions for all products")
    parser.add_argument("--limit", type=int, help="Maximum number of products to process")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed without making changes")
    parser.add_argument("--save-interval", type=int, default=50, help="Save progress every N products (default: 50)")
    args = parser.parse_args()

    print("Loading catalog...")
    catalog = load_catalog()
    total_products = len(catalog)
    print(f"Total products in catalog: {total_products}")

    # Find products without benefit descriptions
    products_without_benefits = [
        (asin, product) for asin, product in catalog.items()
        if not product.get("benefit_description")
    ]
    print(f"Products without benefit_description: {len(products_without_benefits)}")

    if args.dry_run:
        print("\nDry run - would process these products:")
        for i, (asin, product) in enumerate(products_without_benefits[:10]):
            print(f"  {i+1}. {asin}: {product.get('title', 'No title')[:50]}")
        if len(products_without_benefits) > 10:
            print(f"  ... and {len(products_without_benefits) - 10} more")
        return

    if not ANTHROPIC_CLIENT:
        print("Error: Anthropic client not available. Set ANTHROPIC_API_KEY environment variable.")
        return

    # Limit if requested
    to_process = products_without_benefits
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

        benefit = generate_benefit_description(asin, product)
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
