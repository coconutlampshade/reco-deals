---
name: catalog-cleanup
description: Scan products.json for article-title contamination and bad benefit descriptions, then fix them with user confirmation
---

Fix catalog data quality issues — the recurring problem where Cool Tools article titles ("Gifts for the Cook", "Chris Anderson, 3D Robotics CEO") end up stored as product titles and benefit descriptions.

## Step 1 — Audit

Run this scan and report results before touching anything:

```python
import json, sys
sys.path.insert(0, '.')

with open('catalog/products.json') as f:
    cat = json.load(f)
with open('catalog/deals.json') as f:
    deals = json.load(f)['deals']

skip_words = {'the','a','an','and','or','for','of','in','on','to','with','by','at','is','its','be'}

article_title_victims = []
empty_benefits = []
generic_benefits = []

GENERIC_PHRASES = [
    "specialized tools that enhance", "enhance food preparation",
    "improve cooking quality", "providing specialized tools",
    "enhances your overall", "improve your overall",
    "helps improve", "by providing specialized",
]

for asin, p in cat.items():
    amazon_title = deals.get(asin, {}).get('title', '') or p.get('title', '')
    catalog_title = p.get('title', '')
    short_title = p.get('short_title', '')
    benefit = p.get('benefit_description', '')

    # Article-title contamination
    if short_title and short_title == catalog_title and amazon_title and amazon_title != catalog_title:
        words_cs = {w.lower().strip('.,!?-') for w in short_title.split() if len(w) > 2} - skip_words
        words_ft = {w.lower().strip('.,!?-') for w in amazon_title.split() if len(w) > 2} - skip_words
        if not (words_cs & words_ft):
            article_title_victims.append((asin, catalog_title, amazon_title[:60]))

    # Missing benefits (deals only)
    if asin in deals and deals[asin].get('is_deal') and not benefit:
        empty_benefits.append(asin)

    # Generic/wrong benefits
    if benefit:
        lower = benefit.lower()
        for phrase in GENERIC_PHRASES:
            if phrase in lower:
                generic_benefits.append((asin, benefit[:80]))
                break

print(f"Article-title contamination: {len(article_title_victims)} products")
for asin, ct, at in article_title_victims[:10]:
    print(f"  {asin}: catalog={ct!r} vs amazon={at!r}")
if len(article_title_victims) > 10:
    print(f"  ... and {len(article_title_victims) - 10} more")

print(f"\nActive deals missing benefit: {len(empty_benefits)}")
print(f"Generic/wrong benefits: {len(generic_benefits)}")
for asin, b in generic_benefits[:5]:
    print(f"  {asin}: {b!r}")
```

Show the output to the user.

## Step 2 — Confirm

Ask: "Found X article-title victims, Y empty benefits, Z generic benefits. Clear the bad data and run benefit regeneration? (y/n)"

If yes, proceed. If no, stop.

## Step 3 — Fix

Clear bad `short_title` and `benefit_description` for all article-title victims:

```python
cleared = 0
for asin, _, _ in article_title_victims:
    p = cat.get(asin, {})
    p.pop('short_title', None)
    p.pop('benefit_description', None)
    cleared += 1

with open('catalog/products.json', 'w') as f:
    json.dump(cat, f, indent=2)
print(f"Cleared {cleared} entries")
```

## Step 4 — Regenerate benefits (optional)

If there are empty or cleared benefits, offer to run:

```bash
CATALOG_MODE=full python3 generate_all_benefits.py --limit 50
```

Stream the output. Report how many were generated vs failed.

## Step 5 — Commit

```bash
git add catalog/products.json
git commit -m "Fix catalog title/benefit contamination ($(date +%Y-%m-%d))"
```

Report final counts: how many fixed, how many benefits regenerated.
