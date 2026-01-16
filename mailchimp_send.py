#!/usr/bin/env python3
"""
Mailchimp integration for Recomendo Deals.

Provides the create_campaign() function used by review_deals.py
to create draft campaigns in Mailchimp.
"""

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


