---
name: review-deals
description: Pull latest deals and launch the review interface for selecting and sending the daily newsletter
disable-model-invocation: true
---

Review and send the daily deals newsletter. Follow these steps exactly:

1. **Pull latest** from git (`git pull`). If branches have diverged, stash changes, rebase, and pop stash.
2. **Launch the review server** by running `python3 create_review_page.py`
3. **Wait for completion** — the server runs at http://localhost:8765. Monitor the output for "Done!" or errors.
4. **Report results** — show the user: number of deals loaded, deals selected, Mailchimp campaign URL, preview text, and any errors.

Important:
- Do NOT run `check_deals.py` locally — it runs via GitHub Actions at 3:30am PT
- The review page opens in the browser automatically
- After the user selects deals and sends, report the full output
