---
name: performance-report
description: Run campaign analytics and sales report to see which products are driving opens, clicks, and Amazon revenue
---

Get a combined view of newsletter performance: Mailchimp engagement + Amazon Associates revenue, cross-referenced by product.

## Step 1 — Campaign performance (Mailchimp)

```bash
python3 campaign_report.py --last 10
```

Show the user:
- Open rate and click rate for each of the last 10 campaigns
- Which products had the most clicks
- Any trend (improving / declining engagement)

## Step 2 — Sales revenue (Amazon Associates)

```bash
python3 sales_report.py --featured-only --top 20
```

Show the user:
- Top 20 products by revenue (direct sales = DI, indirect = NDI)
- Which featured products are actually converting to purchases
- Total earnings for the period

## Step 3 — Cross-reference

Identify:
- **High click + high revenue**: Best performers — feature these again when deals recur
- **High click + low revenue**: People clicked but didn't buy — price too high, or product discontinued?
- **Low click + any revenue**: Surprising earners — consider better placement
- **Zero click + zero revenue**: Consider hiding these from the review page (use `hidden_products.json`)

Present this as a simple table: Product | Clicks | Revenue | Verdict

## Step 4 — Actionable recommendations

Based on the data, suggest:
- Up to 3 products to prioritize in upcoming newsletters (good deals + proven converters)
- Up to 3 products to add to `catalog/hidden_products.json` (consistently non-performing)

Ask the user: "Want me to hide any of the non-performers? (list ASINs or 'no')"

If yes, add them to `catalog/hidden_products.json` and commit.

## Notes

- `campaign_report.py` requires `MAILCHIMP_API_KEY` in `.env`
- `sales_report.py` reads `amazon-2026.csv` — make sure it's up to date
- Run this weekly, ideally on Mondays before the week's sends
