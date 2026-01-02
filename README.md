# Recomendo Deals

Automated deal finder for products previously recommended in the [Recomendo newsletter](https://recomendo.com). Checks 707 Amazon products daily and generates a newsletter featuring the best current deals.

## How It Works

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Product        │     │  Keepa API      │     │  Amazon PA API  │     │  Mailchimp      │
│  Catalog        │────▶│  Price Check    │────▶│  Live Prices    │────▶│  Draft Campaign │
│  (707 items)    │     │  (find deals)   │     │  (compliance)   │     │  (ready to send)│
└─────────────────┘     └─────────────────┘     └─────────────────┘     └─────────────────┘
```

1. **Product Catalog** - 707 Amazon products extracted from 498 Recomendo issues
2. **Deal Detection** - Keepa API checks 90-day price history to find discounts
3. **Live Pricing** - Amazon PA API fetches current prices (required for Associates compliance)
4. **Newsletter** - HTML report created and uploaded to Mailchimp as a draft

## Daily Automation

GitHub Actions runs at **6:00 AM PST** daily:
- Checks all products for deals (~2-3 min)
- Generates HTML report with top 10 deals
- Creates draft campaign in Mailchimp
- You review and click Send when ready

Manual trigger: Go to Actions → "Daily Deals Report" → "Run workflow"

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
│   ├── products.json      # Product catalog (707 items)
│   ├── deals.json         # Current deals from Keepa
│   └── import_substack.py # Parser for Substack export
├── check_deals.py         # Keepa API deal checker
├── generate_report.py     # HTML report generator
├── mailchimp_send.py      # Create Mailchimp draft
├── pa_api.py              # Amazon PA API client
├── config.py              # Configuration settings
├── run_daily.sh           # Local automation script
├── .github/workflows/
│   └── daily-deals.yml    # GitHub Actions workflow
└── reports/               # Generated HTML reports
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
- `--top N` - Initial pool of deals to check (default: 50, filtered to top 10)
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

### Run Full Pipeline

```bash
./run_daily.sh
```

Or trigger via GitHub Actions.

## Adding New Products

### From a New Recomendo Issue

```bash
python harvest.py
```

Paste the newsletter content and follow prompts to extract Amazon links.

### Re-import All Issues

Place Substack export in `substack_export/` and run:

```bash
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

## License

Private repository - Cool Tools Lab
