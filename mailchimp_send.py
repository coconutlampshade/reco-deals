#!/usr/bin/env python3
"""
Mailchimp integration for Recomendo Deals.

Creates a draft campaign in Mailchimp with the deals report.
You review and send manually.

Usage:
    python mailchimp_send.py                    # Create draft from latest report
    python mailchimp_send.py --html report.html # Create draft from specific file
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# Mailchimp Configuration
MAILCHIMP_API_KEY = os.getenv("MAILCHIMP_API_KEY")
MAILCHIMP_LIST_ID = os.getenv("MAILCHIMP_LIST_ID")  # Audience/List ID
MAILCHIMP_FROM_NAME = os.getenv("MAILCHIMP_FROM_NAME", "Recomendo Deals")
MAILCHIMP_REPLY_TO = os.getenv("MAILCHIMP_REPLY_TO")

# Extract data center from API key (e.g., "us5" from "xxx-us5")
def get_mailchimp_dc():
    if MAILCHIMP_API_KEY and "-" in MAILCHIMP_API_KEY:
        return MAILCHIMP_API_KEY.split("-")[-1]
    return "us5"

MAILCHIMP_DC = get_mailchimp_dc()
MAILCHIMP_API_URL = f"https://{MAILCHIMP_DC}.api.mailchimp.com/3.0"


def check_config():
    """Verify Mailchimp configuration."""
    missing = []
    if not MAILCHIMP_API_KEY:
        missing.append("MAILCHIMP_API_KEY")
    if not MAILCHIMP_LIST_ID:
        missing.append("MAILCHIMP_LIST_ID")
    if not MAILCHIMP_REPLY_TO:
        missing.append("MAILCHIMP_REPLY_TO")

    if missing:
        print(f"Error: Missing environment variables: {', '.join(missing)}")
        print("\nAdd these to your .env file:")
        print("  MAILCHIMP_API_KEY=your-api-key-us5")
        print("  MAILCHIMP_LIST_ID=your-audience-id")
        print("  MAILCHIMP_REPLY_TO=your@email.com")
        print("\nGet your API key from: https://us5.admin.mailchimp.com/account/api/")
        print("Get your Audience ID from: Audience > Settings > Audience name and defaults")
        return False
    return True


def mailchimp_request(method, endpoint, data=None):
    """Make authenticated request to Mailchimp API."""
    url = f"{MAILCHIMP_API_URL}{endpoint}"
    auth = ("anystring", MAILCHIMP_API_KEY)
    headers = {"Content-Type": "application/json"}

    if method == "GET":
        response = requests.get(url, auth=auth, headers=headers)
    elif method == "POST":
        response = requests.post(url, auth=auth, headers=headers, json=data)
    elif method == "PUT":
        response = requests.put(url, auth=auth, headers=headers, json=data)
    else:
        raise ValueError(f"Unknown method: {method}")

    if response.status_code >= 400:
        print(f"Mailchimp API error: {response.status_code}")
        print(response.text)
        return None

    return response.json()


def get_list_info():
    """Get information about the mailing list."""
    result = mailchimp_request("GET", f"/lists/{MAILCHIMP_LIST_ID}")
    if result:
        print(f"List: {result.get('name')}")
        print(f"Subscribers: {result.get('stats', {}).get('member_count', 0)}")
    return result


def create_campaign(subject, html_content, preview_text=None):
    """Create a draft campaign in Mailchimp."""

    # Step 1: Create the campaign
    campaign_data = {
        "type": "regular",
        "recipients": {
            "list_id": MAILCHIMP_LIST_ID
        },
        "settings": {
            "subject_line": subject,
            "from_name": MAILCHIMP_FROM_NAME,
            "reply_to": MAILCHIMP_REPLY_TO,
            "title": f"Recomendo Deals - {datetime.now().strftime('%Y-%m-%d')}",
        }
    }

    # Add preview text if provided
    if preview_text:
        campaign_data["settings"]["preview_text"] = preview_text

    print("Creating campaign...")
    campaign = mailchimp_request("POST", "/campaigns", campaign_data)

    if not campaign:
        print("Failed to create campaign")
        return None

    campaign_id = campaign.get("id")
    print(f"Campaign created: {campaign_id}")

    # Step 2: Set the campaign content
    content_data = {
        "html": html_content
    }

    print("Setting campaign content...")
    content_result = mailchimp_request("PUT", f"/campaigns/{campaign_id}/content", content_data)

    if not content_result:
        print("Failed to set campaign content")
        return None

    print("Content set successfully")

    # Get the web URL for the campaign
    web_id = campaign.get("web_id")
    campaign_url = f"https://{MAILCHIMP_DC}.admin.mailchimp.com/campaigns/edit?id={web_id}"

    return {
        "campaign_id": campaign_id,
        "web_id": web_id,
        "url": campaign_url
    }


def load_html_report(html_path=None):
    """Load HTML report from file."""
    if html_path:
        path = Path(html_path)
    else:
        # Find the most recent report
        reports_dir = Path(__file__).parent / "reports"
        if not reports_dir.exists():
            print(f"Reports directory not found: {reports_dir}")
            return None

        html_files = list(reports_dir.glob("*.html"))
        if not html_files:
            print("No HTML reports found")
            return None

        # Get most recent
        path = max(html_files, key=lambda p: p.stat().st_mtime)

    if not path.exists():
        print(f"File not found: {path}")
        return None

    print(f"Loading report: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def main():
    parser = argparse.ArgumentParser(description="Create Mailchimp draft campaign")
    parser.add_argument("--html", type=str, help="Path to HTML report file")
    parser.add_argument("--subject", type=str, help="Email subject line")
    parser.add_argument("--test", action="store_true", help="Test API connection only")
    args = parser.parse_args()

    if not check_config():
        sys.exit(1)

    if args.test:
        print("Testing Mailchimp API connection...")
        print(f"Data Center: {MAILCHIMP_DC}")
        get_list_info()
        return

    # Load HTML content
    html_content = load_html_report(args.html)
    if not html_content:
        sys.exit(1)

    # Generate subject line
    today = datetime.now().strftime("%B %d, %Y")
    subject = args.subject or f"Recomendo Deals - {today}"

    # Create the draft campaign
    result = create_campaign(subject, html_content)

    if result:
        print("\n" + "=" * 50)
        print("Draft campaign created successfully!")
        print("=" * 50)
        print(f"\nReview and send here:\n{result['url']}")
        print("\nThe campaign is saved as a DRAFT. Review it and click Send when ready.")
    else:
        print("\nFailed to create campaign")
        sys.exit(1)


if __name__ == "__main__":
    main()
