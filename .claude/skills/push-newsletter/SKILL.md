---
name: push-newsletter
description: (Manual fallback) Commit and push today's newsletter and catalog updates. /dailydeals normally handles this automatically.
disable-model-invocation: true
---

> **Note:** As of May 2026, `/dailydeals` owns commit + push + Vercel deploy after a send. This skill exists as a manual fallback for cases where `/dailydeals` exited before the post-send phase ran (server crashed, session ended, etc.). Don't invoke after a normal `/dailydeals` run completed — it would no-op.

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
