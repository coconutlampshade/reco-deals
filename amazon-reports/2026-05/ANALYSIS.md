# Recomendo Deals — May 2026 earnings attribution

**Generated:** 2026-06-01
**Source reports** (Amazon Associates, period 05-01-2026 → 05-31-2026):
- `linked-product.xlsx` — ASIN-level sales (used for attribution)
- `tracking-id.xlsx` — earnings by affiliate tag
- `category.xlsx` — clicks/sales by category
- `bounty.xlsx` — Audible/Prime bounty events

## Headline

**Best estimate: ~$2,260 in commissions** on ~$74,600 tracked sales (3,466 items),
from 19 daily deals emails in May.

| Method | Commissions | Sales | Items |
|--------|------------|-------|-------|
| **Tight** — only the 94 ASINs featured in May deals emails | **$2,259** | $74,592 | 3,466 |
| **Broad** — every product in the deals catalog (~3K ASINs) | $3,035 | $100,110 | — |
| Whole Amazon account (all tags, all ASINs) — context | $7,287 | $229,281 | — |

Deals accounted for **~31% of total account ASIN earnings** in May (tight),
or ~42% (broad).

## Big caveat — one product is half the total

The **SUNMORY floor lamp (B0CQY5RK52)** alone did **$1,096 in commissions
(1,392 units)** — 49% of the tight number. Without that single outlier, "normal"
May deals earnings are about **$1,160**. May was an unusually strong month driven
by one runaway hit.

Top May deals-email earners:

| Earnings | Units | ASIN | Product |
|---------:|------:|------|---------|
| $1,096.28 | 1392 | B0CQY5RK52 | SUNMORY Floor Lamp 32W/300 |
| $190.96 | 409 | B07CVX3516 | Syntech USB-C to USB Adapter (2-pack) |
| $57.02 | 92 | B09XQ9DMXN | CQR Men's Tactical Pants |
| $56.02 | 26 | B01LXFSJY7 | GUM Folding Travel Toothbrush |
| $46.61 | 92 | B07DMZF4ZZ | Intermediate Spanish Short Stories |
| $38.21 | 58 | B0F2M1KYR7 | CMF Buds 2a Wireless Earbuds |

## Method & limitations

1. **Tag overlap.** Deals GeniusLinks embed `tag=recomendos-20` — the *same* tag
   the main Recomendo newsletter uses (confirmed in `convert_to_geniuslinks.py`,
   groupId 113260, productUrl `…tag=recomendos-20`). Amazon cannot split deals
   from the main newsletter automatically, so attribution is done by matching the
   specific ASINs featured in deals emails (`reports/deals-2026-05-*.html`).
2. **Upper vs. lower bound.** A featured ASIN's earnings could partly come from the
   main newsletter or Cool Tools if the product was promoted in more than one place
   (makes tight an *over*-count for those ASINs). Conversely, older deals favorites
   that keep selling fall outside the tight set (makes it an *under*-count of total
   deals influence). True figure sits between tight and broad.
3. **ASIN resolution.** May emails referenced 94 unique ASINs (direct `/dp/` links
   plus 74 `geni.us` short codes mapped back to ASINs via `catalog/products.json`).
   90 of 94 matched actual May sales.

## Reconciliation with earlier Jan–Feb estimate

Jan–Feb (from per-order CSVs) showed deals at 4% tight / 19% broad of earnings.
May (full-month ASIN report) shows 31% tight / 42% broad — but May is inflated by
the floor-lamp hit. Treat **~$2,300/strong month** or **~$1,200/typical month** as
the working range until more full months are collected.
