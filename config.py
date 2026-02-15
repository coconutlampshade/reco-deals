"""Configuration for Recomendo Deals."""

from pathlib import Path

# Project paths
PROJECT_ROOT = Path(__file__).parent
CATALOG_DIR = PROJECT_ROOT / "catalog"
CATALOG_FILE = CATALOG_DIR / "products.json"
SUBSTACK_EXPORT_DIR = PROJECT_ROOT / "substack_export"
DASHBOARD_DIR = PROJECT_ROOT / "dashboard"
HISTORY_DIR = PROJECT_ROOT / "history"

# Shortlink resolution
SHORTLINK_RATE_LIMIT = 1.0  # seconds between requests
SHORTLINK_TIMEOUT = 10  # seconds
SHORTLINK_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# Amazon URL patterns (domains to match)
AMAZON_DOMAINS = [
    "amazon.com",
    "amazon.co.uk",
    "amazon.ca",
    "amazon.de",
    "amazon.fr",
    "amazon.es",
    "amazon.it",
    "amazon.co.jp",
    "amazon.com.au",
]

# Shortlink domains that need resolution
SHORTLINK_DOMAINS = [
    "geni.us",
    "amzn.to",
]

# Keepa API settings
KEEPA_API_URL = "https://api.keepa.com"
KEEPA_TOKENS_PER_MINUTE = 20  # API rate limit
KEEPA_BATCH_SIZE = 20  # Products per API request (1 token each)
KEEPA_DOMAIN_ID = 1  # 1 = amazon.com (US)

# Deal thresholds
# A product is considered a "deal" if ANY of these conditions are met:
DEAL_PERCENT_BELOW_AVG = 10      # Current price is 10%+ below 90-day average
DEAL_PERCENT_BELOW_HIGH = 30    # Current price is 30%+ below 90-day high
DEAL_NEAR_LOW_PERCENT = 5       # Current price is within 5% of all-time low
DEAL_MIN_DISCOUNT_DOLLARS = 5   # Minimum dollar savings to qualify as deal

# Deal quality filters (optional - for ranking deals)
DEAL_MIN_RATING = 4.0           # Minimum product rating (if available)
DEAL_MIN_REVIEWS = 50           # Minimum number of reviews (if available)

# Deal score weights (0-100 composite score for ranking deals)
SCORE_WEIGHT_SAVINGS_PCT = 40   # Max points from % below average (2 pts per %)
SCORE_WEIGHT_REVIEWS = 10       # Max points from review count (full at 500+)
SCORE_WEIGHT_RATING = 10        # Max points from star rating (full at 4.5+)
SCORE_WEIGHT_DOLLARS = 20       # Max points from dollar savings (full at $25+)
SCORE_WEIGHT_NEAR_LOW = 20      # Bonus points if within 5% of 90-day low

# Price history settings
PRICE_HISTORY_DAYS = 90         # Days of price history to analyze

# Sales reporting
SALES_CSV = PROJECT_ROOT / "amazon-2026.csv"
SALES_REPORTS_DIR = PROJECT_ROOT / "reports" / "sales"
EARNINGS_CSV = PROJECT_ROOT / "earnings-2026.csv"
BOUNTY_CSV = PROJECT_ROOT / "bounty-2026.csv"
