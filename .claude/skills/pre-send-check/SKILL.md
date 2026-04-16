---
name: pre-send-check
description: Quality check on selected deals before sending — catches bad titles, missing benefits, URL problems, and score issues
---

Run a pre-flight check on the deals you're about to send. Catches the problems that have slipped through in past sessions.

## When to use

Run this after selecting deals in the review page but **before** confirming the Mailchimp send. Can also be run on any list of ASINs: `/pre-send-check B001XXXXX B002XXXXX ...`

## Step 1 — Get the selected deals

If ASINs were passed as arguments, use those. Otherwise, ask: "Which ASINs are you sending today?" (or read from `catalog/processed_campaigns.json` if the send just happened — look for the most recent entry).

## Step 2 — Check each deal

For each ASIN, verify:

**Title check**
- Does `short_title` or the display title contain any of: "Gifts for the", "Holiday Gift", "Best X for", a person's name, "[Maker Update", episode numbers?
- Is it shorter than 6 words? (too short)
- Is it longer than 8 words? (too long — needs shortening)

**Benefit check**
- Is `benefit_description` empty?
- Does it contain generic phrases: "specialized tools", "improve overall", "enhance your", "providing specialized", "helps improve"?
- Is it over 30 words? (too long for email display)

**Affiliate URL check**
- Does `affiliate_url` exist and start with `https://geni.us/`?
- If empty, flag for manual fix

**Deal quality check**
- Is `deal_score` below `DEAL_MIN_NEWSLETTER_SCORE` (40)?
- Is `current_price` null or 0?
- Is the product in `catalog/unavailable_tracking.json` with consecutive unavailable days > 2?

**Cooldown check**
- Is this ASIN in `catalog/processed_campaigns.json` within the last 30 days?

## Step 3 — Report

Present a table:

```
ASIN        | Title                    | Benefit | URL | Score | Issues
B00395FHRO  | Tovolo Large 2" King... | ✓       | ✓   | 43    | None
B001XXXXX   | Gifts for the Cook      | ✗       | ✓   | 61    | BAD TITLE, MISSING BENEFIT
```

Use ✓/✗ for each check. List specific issues for any failures.

## Step 4 — Fix or flag

For each issue found:

- **Bad title**: Suggest the `shorten_title()` result from the Amazon title. Ask to confirm.
- **Missing/generic benefit**: Offer to call `/generate-benefit` endpoint on the running review server, or run `generate_all_benefits.py --asin XXXXX`
- **Missing affiliate URL**: Flag — cannot auto-fix, user must check geni.us
- **Low score**: Warn but don't block — user may have a good reason
- **Cooldown hit**: Warn with days since last featured

## Step 5 — Clear to send

If all checks pass (or user acknowledged warnings): "All clear — safe to send."

If blocking issues remain: "Do not send until these are fixed: [list]"

## Notes

- Affiliate URL 404 checking requires an HTTP request — skip if offline or slow
- A score below 40 is a soft warning, not a hard block
- Title/benefit fixes should be saved back to `products.json`
