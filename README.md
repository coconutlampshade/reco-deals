# Recomendo Deals

Automated deal finder for products previously recommended in the [Recomendo newsletter](https://recomendo.com). Checks 707+ Amazon products daily and generates a newsletter featuring the best current deals.

## How It Works

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Product        │     │  Keepa API      │     │  Amazon PA API  │     │  Mailchimp      │
│  Catalog        │────▶│  Price Check    │────▶│  Live Prices    │────▶│  Draft Campaign │
│  (707+ items)   │     │  (find deals)   │     │  (compliance)   │     │  (ready to send)│
└─────────────────┘     └─────────────────┘     └─────────────────┘     └─────────────────┘
```

1. **Product Catalog** - Amazon products extracted from 498 Recomendo issues
2. **Deal Detection** - Keepa API checks 90-day price history to find discounts
3. **Live Pricing** - Amazon PA API fetches current prices (required for Associates compliance)
4. **Deal Ranking** - Scores deals by savings %, popularity (reviews), and quality (star rating)
5. **Newsletter** - HTML report created and uploaded to Mailchimp as a draft

## Deal Selection

Each newsletter features the **top 10 deals** selected by:

**Ranking Formula:**
```
score = savings_percentage + (popularity × 0.5) + (quality × 0.5)
```

- **Savings %** - Primary factor (0-100 points)
- **Popularity** - Based on review count, log scale (0-15 points)
- **Quality** - Bonus for 4.0+ star ratings (0-5 points)

**Filters:**
- **Media limit** - Max 1 book/movie/TV show per issue
- **30-day cooldown** - Items won't repeat for 30 days after being featured
- **PA API confirmation** - Only includes items Amazon confirms are discounted

## Automation

Everything runs automatically via GitHub Actions:

| Workflow | Schedule | What it does |
|----------|----------|--------------|
| **Weekly Catalog Update** | Sunday 5:00 AM PST | Fetches latest Recomendo issue from RSS, extracts Amazon links, adds new products to catalog |
| **Daily Deals Report** | Daily 6:00 AM PST | Checks for deals, generates report, creates Mailchimp draft |

**Weekly flow:**
- **Sunday 5am** - New products from latest issue added to catalog
- **Sunday 6am** - Deals report includes any new products
- **Mon-Sat 6am** - Daily deals report as usual

By the time you check email in the morning, there's a draft campaign waiting in Mailchimp. All you need to do is review and click Send.

Manual trigger: Go to Actions → select workflow → "Run workflow"

## APIs Used

| API | Purpose | Cost | Documentation |
|-----|---------|------|---------------|
| **Keepa** | Historical price data, deal detection | ~$55/month | [keepa.com](https://keepa.com/#!api) |
| **Amazon PA API** | Live prices, product info, affiliate links | Free (Associates) | [webservices.amazon.com](https://webservices.amazon.com/paapi5/documentation/) |
| **Mailchimp** | Email campaign creation | Free tier available | [mailchimp.com/developer](https://mailchimp.com/developer/) |

## Project Structure

```
reco-deals/
├── catalog/
│   ├── products.json          # Product catalog (707+ items)
│   ├── deals.json             # Current deals from Keepa
│   ├── featured_history.json  # 30-day cooldown tracking
│   └── import_substack.py     # Parser for Substack export
├── check_deals.py             # Keepa API deal checker
├── generate_report.py         # HTML report generator
├── mailchimp_send.py          # Create Mailchimp draft
├── pa_api.py                  # Amazon PA API client
├── fetch_latest_issue.py      # Fetch new issue from RSS
├── config.py                  # Configuration settings
├── .github/workflows/
│   ├── daily-deals.yml        # Daily deals workflow
│   └── weekly-update.yml      # Weekly catalog update
└── reports/                   # Generated HTML reports
```

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env` and add your API keys:

```bash
cp .env.example .env
```

Required keys:
- `KEEPA_API_KEY` - From [keepa.com](https://keepa.com/#!api)
- `PA_API_ACCESS_KEY` - From [Amazon Associates](https://affiliate-program.amazon.com/assoc_credentials/home)
- `PA_API_SECRET_KEY` - From Amazon Associates
- `PA_API_PARTNER_TAG` - Your Associates tag (e.g., `mytag-20`)
- `MAILCHIMP_API_KEY` - From [Mailchimp Account Settings](https://admin.mailchimp.com/account/api/)
- `MAILCHIMP_LIST_ID` - Your Audience ID

### 3. GitHub Secrets (for automation)

Add the same keys as repository secrets at:
`Settings → Secrets and variables → Actions`

## Usage

### Fetch Latest Issue

```bash
python fetch_latest_issue.py           # Add products from latest issue
python fetch_latest_issue.py --dry-run # Preview without saving
python fetch_latest_issue.py --all     # Process all items in RSS feed
```

### Check for Deals

```bash
python check_deals.py
```

Scans all products using Keepa API and saves deals to `catalog/deals.json`.

### Generate Report

```bash
python generate_report.py --top 50
```

Creates HTML report with live PA API prices. Options:
- `--top N` - Initial pool of deals to check (filtered to top 10)
- `--output FILE` - Custom output path
- `--format html|text|markdown` - Output format

### Create Mailchimp Draft

```bash
python mailchimp_send.py
```

Creates a draft campaign from the latest report. Options:
- `--html FILE` - Specify report file
- `--subject "..."` - Custom subject line
- `--test` - Test API connection only

### Full Import (One-time Setup)

To rebuild catalog from a Substack export:

```bash
# Place export zip in project directory
unzip substack_export.zip -d substack_export/
python catalog/import_substack.py
```

## Amazon Associates Compliance

The newsletter follows Amazon Associates Program requirements:

- ✅ Prices fetched dynamically from PA API (not cached/static)
- ✅ Links use PA API's `DetailPageURL` with proper tracking
- ✅ Timestamp shown: "Prices accurate as of [time]"
- ✅ Required disclaimer included
- ✅ Affiliate disclosure: "As an Amazon Associate we earn..."
- ✅ No price tracking/alerting functionality exposed to users

## Troubleshooting

**Keepa API errors**: Check your token balance at keepa.com. Each product check costs ~2 tokens.

**PA API "ItemId not accessible"**: Some products are restricted. They're automatically skipped.

**Mailchimp errors**: Verify your API key includes the data center suffix (e.g., `-us5`).

**No deals found**: This can happen if prices haven't changed. The system only shows items where Amazon confirms a discount (list_price > current_price).

---

© 2026 Cool Tools Lab, LLC
