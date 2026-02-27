# Recomendo Deals Newsletter

## Rules
- Don't run `check_deals.py` locally — it takes 2+ hours and runs automatically via GitHub Actions at 3:30am PT
- Always pull latest before generating reports (automated via SessionStart hook)

## Key References
- @config.py for all constants, thresholds, and score weights
- @catalog/products.json for the full product catalog (~2900 items)

## Daily Workflow
Use `/review-deals` to pull latest and launch the review interface, then `/push-newsletter` to commit and push.
