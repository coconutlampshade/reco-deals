# Recomendo Deals Newsletter

## Rules
- Don't run `check_deals.py` locally — it takes 2+ hours and runs automatically via GitHub Actions at 3:30am PT
- Always pull latest before generating reports (automated via SessionStart hook)

## Key References
- @config.py for all constants, thresholds, and score weights
- catalog/products.json for the full product catalog (~2900 items) — do NOT load this file into context

## Daily Workflow
Use `/review-deals` to pull latest and launch the review interface, then `/push-newsletter` to commit and push.

## Communication Style

- Explain technical steps in plain English first; the user is non-technical. Avoid jargon unless asked.

## Context & Compaction

- After context compaction or `/clear`, always re-verify source article texts and URLs before writing — never fabricate URLs or paraphrase from memory.

## Git Workflow

- When committing, verify the git repo root matches the intended project — do not stage files from sibling directories sharing a parent.

## Tooling Preferences

- Prefer direct API access (e.g., WordPress REST API with stored credentials) over asking the user to run browser scripts or manual steps.
