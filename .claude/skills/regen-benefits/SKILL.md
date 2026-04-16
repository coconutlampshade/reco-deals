---
name: regen-benefits
description: Regenerate missing or bad benefit descriptions in bulk using generate_all_benefits.py
---

Batch-regenerate benefit descriptions for products that are missing them or have generic/wrong ones.

## Step 1 — Dry run

Show what would be processed:

```bash
CATALOG_MODE=full python3 generate_all_benefits.py --dry-run
```

Report: total products without benefits, breakdown by source (recomendo vs cooltools).

## Step 2 — Choose mode

Ask the user which mode:

- **Missing only** (default): `--limit 50` — process products with no benefit yet
- **Fix bad ones too**: `--force --source cooltools --limit 50` — regenerate all Cool Tools entries (common source of article-title victims)
- **Specific ASIN**: `--asin B00XXXXX` — regenerate one product
- **Custom limit**: user specifies N

If user just says "go" or "yes", use the default (missing only, limit 50).

## Step 3 — Run

```bash
CATALOG_MODE=full python3 generate_all_benefits.py [flags]
```

Stream output live. Watch for:
- Lines starting with `Generated:` — success
- Lines with `Warning:` or `Error:` — failures
- Lines with `Benefit rejected` — validation failures that triggered retry

## Step 4 — Report

After completion, summarize:
- How many benefits generated successfully
- How many failed (and why, if visible in output)
- Any ASINs that consistently failed (suggest `--asin` retry with PA API fallback)

## Step 5 — Commit

```bash
git add catalog/products.json
git commit -m "Regenerate benefit descriptions for catalog products"
```

## Notes

- Each product costs ~1-2 Claude API calls (article fetch + generation)
- ~50 products takes about 5-10 minutes
- Run after `/catalog-cleanup` to fill in the cleared benefits
- If article fetch fails, the script falls back to PA API product features
