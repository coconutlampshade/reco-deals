# Recomendo Deals

A daily deals newsletter featuring products previously recommended in [Recomendo](https://recomendo.com) and [Cool Tools](https://kk.org/cooltools/). The system automatically finds deals and creates ready-to-send email campaigns.

## Quick Start: Sending a Newsletter

**Step 1:** Open Terminal and navigate to the project folder:
```
cd ~/Desktop/claude-code-apps/reco-deals
```

**Step 2:** Run the review tool:
```
python3 review_deals.py
```

**Step 3:** A browser window opens showing today's deals. Select the ones you want to include (usually 5-6), then click **"Confirm & Send"**.

**Step 4:** Check Mailchimp for your draft campaign. Review it and click Send when ready.

That's it! The system handles everything else automatically.

---

## What Happens Automatically

The system runs on its own every day via GitHub:

| Time | What Happens |
|------|--------------|
| **Sunday 5am** | Checks for new products from the latest Recomendo issue |
| **Daily 6am** | Scans all 700+ products for deals and saves the results |

By morning, fresh deal data is ready for you to review.

---

## The Two Versions of Each Newsletter

When you click "Confirm & Send", two versions are created:

### 1. Email Version (Mailchimp)
- Sent to subscribers
- Prices are locked at send time
- Includes link to view online with live prices
- Footer links to the archive for past deals

### 2. Web Version (Vercel)
- Live at: **https://reco-deals.vercel.app/**
- Prices update in real-time from Amazon
- Archive of all past issues

---

## How Deals Are Found

The system uses two services to find genuine deals:

1. **Keepa** - Tracks Amazon price history. Identifies products currently below their 90-day average.

2. **Amazon Product Advertising API** - Confirms the current price and discount percentage directly from Amazon.

A product only appears as a deal if both services agree it's discounted.

### Deal Ranking

Deals are scored and sorted by:
- **Discount percentage** (primary factor)
- **Popularity** (review count)
- **Quality** (star rating)

### Automatic Filters
- **30-day cooldown** - Products won't repeat within 30 days
- **Media limit** - Max 1 book/movie per issue (keeps variety)

---

## Project Files

| File/Folder | What It Does |
|-------------|--------------|
| `review_deals.py` | The main tool you use to select deals and send newsletters |
| `catalog/products.json` | Database of 3000 products we've recommended |
| `catalog/deals.json` | Today's deals (updated daily by automatically consulting with Keepa) |
| `public/` | Web versions of newsletters (deployed to Vercel) |
| `reports/` | Email versions of newsletters |

---

## Troubleshooting

### "Address already in use" error
The review tool is already running somewhere. Either:
- Find the browser tab that's already open, or
- Use a different port: `python3 review_deals.py --port 8081`

### No deals showing up
This can happen if Amazon prices haven't changed recently. The system only shows products with confirmed discounts.

### Mailchimp draft not appearing
Check that your Mailchimp API key is set correctly in the `.env` file.

---

## The Archive

All past newsletters with live pricing are available at:

**https://reco-deals.vercel.app/**

This is linked from every email newsletter footer, so subscribers can browse past deals with up-to-date prices.

---

## Need Help?

For technical issues or questions, the codebase is documented in detail. Ask Claude Code for help with:
- "review deals" or "send newsletter" - walks through the process
- Explaining how any part of the system works
- Making changes or improvements

---

© 2026 Cool Tools Lab, LLC
