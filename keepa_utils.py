"""Shared Keepa API price parsing and deal scoring utilities."""


def _safe_get_stat(stat_data, idx):
    """Safely extract a stat value from Keepa stats, handling various formats."""
    if not stat_data:
        return None
    if isinstance(stat_data, list):
        if len(stat_data) > idx:
            val = stat_data[idx]
            if isinstance(val, list):
                return val[-1] if val and val[-1] and val[-1] > 0 else None
            return val if val and val > 0 else None
    return None


def parse_keepa_current_price(product_data: dict, stats: dict | None) -> tuple[float | None, str | None]:
    """Extract current price from Keepa data.

    Tries stats.current first (most reliable), then CSV history as fallback.
    Prefers Amazon price, falls back to New 3rd party.

    Returns:
        (current_price_dollars, price_source) where price_source is
        "amazon", "new_3rd_party", or None if no price found.
    """
    current_price = None
    price_source = None

    # Prefer stats.current which reflects Keepa's latest knowledge of availability
    current_stats = stats.get("current", []) if stats else []
    if isinstance(current_stats, list):
        if len(current_stats) > 0:
            val = current_stats[0]
            if val is not None and isinstance(val, (int, float)) and val > 0:
                current_price = val / 100.0
                price_source = "amazon"
        if current_price is None and len(current_stats) > 1:
            val = current_stats[1]
            if val is not None and isinstance(val, (int, float)) and val > 0:
                current_price = val / 100.0
                price_source = "new_3rd_party"

    # Fall back to CSV history if stats.current unavailable
    if current_price is None:
        csv = product_data.get("csv", [])
        if csv and len(csv) > 0 and csv[0]:
            amazon_csv = csv[0]
            if amazon_csv and len(amazon_csv) >= 2:
                last_price = amazon_csv[-1]
                if last_price is not None and last_price > 0:
                    current_price = last_price / 100.0
                    price_source = "amazon"
        if current_price is None and csv and len(csv) > 1 and csv[1]:
            new_csv = csv[1]
            if new_csv and len(new_csv) >= 2:
                last_price = new_csv[-1]
                if last_price is not None and last_price > 0:
                    current_price = last_price / 100.0
                    price_source = "new_3rd_party"

    return current_price, price_source


def parse_keepa_stats(stats: dict | None, price_source: str | None) -> dict:
    """Extract 90-day price statistics from Keepa stats.

    Returns dict with avg_90_day, high_90_day, low_90_day, all_time_low (all in dollars).
    """
    result = {
        "avg_90_day": None,
        "high_90_day": None,
        "low_90_day": None,
        "all_time_low": None,
    }
    if not stats:
        return result

    price_idx = 0 if price_source == "amazon" else 1

    avg_val = _safe_get_stat(stats.get("avg"), price_idx)
    if avg_val:
        result["avg_90_day"] = avg_val / 100.0

    min_val = _safe_get_stat(stats.get("min"), price_idx)
    if min_val:
        result["low_90_day"] = min_val / 100.0

    max_val = _safe_get_stat(stats.get("max"), price_idx)
    if max_val:
        result["high_90_day"] = max_val / 100.0

    at_low_val = _safe_get_stat(stats.get("atLow"), price_idx)
    if at_low_val:
        result["all_time_low"] = at_low_val / 100.0

    return result


def parse_keepa_rating(product_data: dict) -> tuple[float | None, int | None]:
    """Extract rating and review count from Keepa CSV data.

    Returns:
        (rating, review_count) — rating is float like 4.5, review_count is int.
    """
    rating = None
    review_count = None
    csv = product_data.get("csv", [])

    if csv and len(csv) > 16:
        rating_csv = csv[16]
        if rating_csv and len(rating_csv) >= 2 and rating_csv[-1]:
            rating = rating_csv[-1] / 10.0  # Keepa stores 45 for 4.5

    if csv and len(csv) > 17:
        review_csv = csv[17]
        if review_csv and len(review_csv) >= 2 and review_csv[-1]:
            review_count = review_csv[-1]

    return rating, review_count


def calculate_deal_metrics(current_price: float, avg_90_day: float | None, high_90_day: float | None) -> dict:
    """Calculate deal metrics (percent below avg/high, savings dollars).

    Returns dict with percent_below_avg, percent_below_high, savings_dollars.
    """
    result = {
        "percent_below_avg": None,
        "percent_below_high": None,
        "savings_dollars": None,
    }

    if avg_90_day and avg_90_day > 0:
        result["percent_below_avg"] = ((avg_90_day - current_price) / avg_90_day) * 100
        result["savings_dollars"] = avg_90_day - current_price

    if high_90_day and high_90_day > 0:
        result["percent_below_high"] = ((high_90_day - current_price) / high_90_day) * 100

    return result


def calculate_deal_score(
    current_price: float,
    percent_below_avg: float | None,
    savings_dollars: float | None,
    low_90_day: float | None,
    rating: float | None,
    review_count: int | None,
) -> int:
    """Calculate composite deal score (0-100) for ranking deals.

    Components:
    - Savings % (0-40): 2 points per percent below average
    - Review count (0-10): scales linearly up to 500 reviews
    - Star rating (0-10): scales from 3.5 to 5.0 stars
    - Dollar savings (0-20): scales linearly up to $25
    - Near 90-day low (0-20): bonus if within 5% of low
    """
    import config

    score = 0.0

    # Savings % component
    pct = percent_below_avg or 0
    if pct > 0:
        score += min(pct * 2, config.SCORE_WEIGHT_SAVINGS_PCT)

    # Review count component
    rc = review_count or 0
    if rc > 0:
        score += min(rc / 500, 1.0) * config.SCORE_WEIGHT_REVIEWS

    # Star rating component
    r = rating or 0
    if r > 3.5:
        score += min((r - 3.5) / 1.5, 1.0) * config.SCORE_WEIGHT_RATING

    # Dollar savings component
    ds = savings_dollars or 0
    if ds > 0:
        score += min(ds / 25, 1.0) * config.SCORE_WEIGHT_DOLLARS

    # Near 90-day low bonus
    if low_90_day and low_90_day > 0 and current_price <= low_90_day * 1.05:
        score += config.SCORE_WEIGHT_NEAR_LOW

    return round(score)
