# Recomendo Deals Newsletter

## Workflow

### "review deals" or "send newsletter"
1. Pull latest from git first (`git pull`)
2. Run `python3 create_review_page.py` to open the review interface
3. User selects deals and clicks "Review & Edit"
4. Edit titles, benefit descriptions, and affiliate URLs on the editing page
5. Drag to reorder deals, then click "Send to Mailchimp" to create draft

### Daily schedule
- **3:30am PT** — GitHub Actions checks all ~2900 products via Keepa (takes ~2.5 hours)
- **~6:00am PT** — Fresh prices are committed to `catalog/deals.json`
- User pulls latest and runs `create_review_page.py` to review and send

## Preferences
- Don't re-run check_deals.py locally (takes 2+ hours, runs automatically via GitHub Actions)
- Always pull latest before generating reports
