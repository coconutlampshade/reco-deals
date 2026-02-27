---
name: push-newsletter
description: Commit and push today's newsletter and catalog updates to git
disable-model-invocation: true
---

Commit and push the newsletter changes. Follow these steps:

1. **Check status** with `git status --short`
2. **Stage only newsletter-related files**: catalog changes, public/ newsletters, archive, feed, and review_deals.py updates. Specifically:
   - `catalog/campaign_history.json`
   - `catalog/featured_history.json`
   - `catalog/products.json`
   - `public/archive.html`
   - `public/feed.xml`
   - `public/newsletter-*.html` (new newsletter files)
   - Any other modified tracked files related to the newsletter pipeline
3. **Do NOT stage**: `.obsidian/`, `skills.md`, or other unrelated files
4. **Commit** with message: "Send newsletter YYYY-MM-DD"
5. **Push** to remote

If there are also staged code changes (`.py` files, `.yml`, etc.), ask the user whether to include them or commit separately.
