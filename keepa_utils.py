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


def _extract_stat(current_stats: list, index: int) -> float | None:
    """Extract a price from stats.current at the given index, returning dollars or None."""
    if not isinstance(current_stats, list) or len(current_stats) <= index:
        return None
    val = current_stats[index]
    if val is not None and isinstance(val, (int, float)) and val > 0:
        return val / 100.0
    return None


def parse_keepa_current_price(product_data: dict, stats: dict | None) -> tuple[float | None, str | None]:
    """Extract current price from Keepa data.

    Priority: Buy Box (what customer sees) > Amazon direct > New 3rd party.
    The Buy Box price is what's shown on the product page. The raw "new 3rd
    party" price can be a cheap obscure seller the customer never sees.

    Returns:
        (current_price_dollars, price_source) where price_source is
        "amazon", "buy_box", "new_3rd_party", or None if no price found.
    """
    current_stats = stats.get("current", []) if stats else []

    # Prefer Buy Box price — this is what the customer actually sees
    buy_box_price = _extract_stat(current_stats, 18)
    if buy_box_price is not None:
        return buy_box_price, "buy_box"

    # Then Amazon-direct price
    amazon_price = _extract_stat(current_stats, 0)
    if amazon_price is not None:
        return amazon_price, "amazon"

    # Last resort: 3rd party (may not reflect what customer sees)
    new_3p_price = _extract_stat(current_stats, 1)
    if new_3p_price is not None:
        return new_3p_price, "new_3rd_party"

    # Fall back to CSV history — same priority order
    csv = product_data.get("csv", [])
    for csv_idx, source_name in [(18, "buy_box"), (0, "amazon"), (1, "new_3rd_party")]:
        if csv and len(csv) > csv_idx and csv[csv_idx]:
            csv_data = csv[csv_idx]
            if csv_data and len(csv_data) >= 2:
                last_price = csv_data[-1]
                if last_price is not None and last_price > 0:
                    return last_price / 100.0, source_name

    return None, None


def parse_keepa_stats(stats: dict | None, price_source: str | None) -> dict:
    """Extract 90-day price statistics from Keepa stats.

    Returns dict with avg_90_day, high_90_day, low_90_day, all_time_low, list_price (all in dollars).
    """
    result = {
        "avg_90_day": None,
        "high_90_day": None,
        "low_90_day": None,
        "all_time_low": None,
        "list_price": None,
    }
    if not stats:
        return result

    # Buy Box stats use index 18, Amazon uses 0, 3rd party uses 1
    if price_source and price_source.startswith("buy_box"):
        price_idx = 18
    elif price_source == "amazon":
        price_idx = 0
    else:
        price_idx = 1

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

    # List price / MSRP is at stats.current[4]
    list_price = _extract_stat(stats.get("current", []), 4)
    if list_price is not None:
        result["list_price"] = list_price

    return result


def parse_keepa_buybox_price(product_data: dict) -> tuple[float | None, str | None]:
    """Extract best available price from Keepa offers data.

    Requires the product to have been fetched with offers=20 parameter.

    Checks two things:
    1. Buy Box winner's offer price (from buyBoxSellerIdHistory + offers)
    2. Any Prime-exclusive offer from any FBA seller (these show as the
       Prime member price on Amazon, even if from a non-Buy Box seller)

    Returns the lowest price found, preferring Prime-exclusive deals.

    Returns:
        (price_dollars, source) where source is "buy_box_prime" or "buy_box_offer",
        or (None, None) if not available.
    """
    offers = product_data.get("offers", [])
    if not offers:
        return None, None

    # Get the current Buy Box winner seller ID
    bb_history = product_data.get("buyBoxSellerIdHistory")
    current_seller = None
    if bb_history and len(bb_history) >= 2:
        current_seller = bb_history[-1]

    best_prime_price = None
    bb_regular_price = None

    for offer in offers:
        seller = offer.get("sellerId")
        condition = offer.get("condition", 0)

        # Only consider new condition offers (condition=1)
        if condition != 1:
            continue

        # Check for Prime-exclusive price from ANY FBA seller
        if offer.get("isPrimeExcl") and offer.get("isFBA") and offer.get("primeExclCSV"):
            csv_data = offer["primeExclCSV"]
            if len(csv_data) >= 2:
                last_price = csv_data[-1]
                if last_price is not None and last_price > 0:
                    price = last_price / 100.0
                    if best_prime_price is None or price < best_prime_price:
                        best_prime_price = price

        # Track Buy Box winner's regular offer price
        if seller == current_seller and bb_regular_price is None:
            offer_csv = offer.get("offerCSV")
            if offer_csv and len(offer_csv) >= 2:
                # offerCSV is triplets: [time, price, shipping, time, price, shipping, ...]
                last_price = offer_csv[-2]
                if last_price is not None and last_price > 0:
                    bb_regular_price = last_price / 100.0

    # Prefer Prime-exclusive price when it's lower
    if best_prime_price is not None:
        if bb_regular_price is None or best_prime_price <= bb_regular_price:
            return best_prime_price, "buy_box_prime"
    if bb_regular_price is not None:
        return bb_regular_price, "buy_box_offer"

    return None, None


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
