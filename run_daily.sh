#!/bin/bash
#
# Daily Recomendo Deals automation script
# Checks for deals, generates report, creates Mailchimp draft
#
# Run manually: ./run_daily.sh
# Or via cron:  0 8 * * * /Users/mark/Desktop/reco-deals/run_daily.sh
#

set -e  # Exit on error

# Configuration
PROJECT_DIR="/Users/mark/Desktop/reco-deals"
LOG_FILE="$PROJECT_DIR/logs/daily-$(date +%Y-%m-%d).log"
PYTHON="/usr/bin/env python3"

# Create logs directory if needed
mkdir -p "$PROJECT_DIR/logs"

# Log start
echo "======================================" >> "$LOG_FILE"
echo "Starting daily run: $(date)" >> "$LOG_FILE"
echo "======================================" >> "$LOG_FILE"

cd "$PROJECT_DIR"

# Step 1: Check for deals using Keepa
echo "[$(date +%H:%M:%S)] Checking for deals..." >> "$LOG_FILE"
$PYTHON check_deals.py >> "$LOG_FILE" 2>&1

# Step 2: Generate HTML report with live PA API prices
echo "[$(date +%H:%M:%S)] Generating report..." >> "$LOG_FILE"
REPORT_FILE="$PROJECT_DIR/reports/deals-$(date +%Y-%m-%d).html"
$PYTHON generate_report.py --top 50 --output "$REPORT_FILE" >> "$LOG_FILE" 2>&1

# Step 3: Create Mailchimp draft campaign
echo "[$(date +%H:%M:%S)] Creating Mailchimp draft..." >> "$LOG_FILE"
$PYTHON mailchimp_send.py --html "$REPORT_FILE" >> "$LOG_FILE" 2>&1

# Log completion
echo "[$(date +%H:%M:%S)] Daily run completed successfully" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"

# Optional: Send yourself a notification that draft is ready
# You could add a simple email or Slack notification here

echo "Done! Check Mailchimp for draft campaign."
