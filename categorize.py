"""Lightweight keyword-based product categorizer.

The catalog has no category field, so we infer a coarse bucket from the product
title. This is deliberately simple and transparent — its only job is to keep a
day's newsletter from being, say, five kitchen gadgets in a row. It does not
need to be perfect; "unknown" is an acceptable answer.

Buckets are checked in order; the first whose keywords match wins. Order matters
where terms overlap (e.g. "camera bag" should read as photo, not bags), so more
specific buckets come first.
"""

import re

# (category, [keywords]) — keywords matched as whole words, case-insensitive.
_CATEGORIES = [
    ("photo", ["camera", "lens", "tripod", "dslr", "mirrorless", "gopro", "photography", "shutter", "flash", "gimbal"]),
    ("audio", ["headphone", "earbud", "speaker", "microphone", "mic", "soundbar", "turntable", "amplifier", "earphone"]),
    ("computing", ["laptop", "keyboard", "mouse", "monitor", "usb", "ssd", "hard drive", "router", "webcam", "hub", "charger", "cable", "powerbank", "power bank", "adapter", "dvd", "cd-rom"]),
    ("phone", ["iphone", "android", "phone case", "screen protector", "airpods", "smartphone"]),
    ("kitchen", ["kitchen", "knife", "pan", "pot", "skillet", "cookware", "spatula", "cutting board", "blender", "coffee", "espresso", "kettle", "mug", "utensil", "bakeware", "grater", "whisk", "thermometer", "cast iron", "dish", "towel", "strainer", "slicer", "peeler"]),
    ("tools", ["drill", "wrench", "screwdriver", "hammer", "pliers", "saw", "sander", "tool", "clamp", "soldering", "multimeter", "tape measure", "level", "vise", "bit set", "ratchet"]),
    ("outdoor", ["tent", "backpack", "hiking", "camping", "flashlight", "headlamp", "knife", "survival", "fishing", "kayak", "cooler", "lantern", "carabiner", "trekking", "gps", "tracker", "satellite"]),
    ("home", ["lamp", "light", "led", "vacuum", "broom", "storage", "organizer", "shelf", "hanger", "clock", "bedding", "pillow", "blanket", "curtain", "mat", "rug", "hook", "bin", "furniture", "desk", "chair", "table"]),
    ("health", ["supplement", "vitamin", "massage", "fitness", "yoga", "muscle", "posture", "first aid", "thermometer", "toothbrush", "skincare", "razor", "trimmer", "pharmacy", "capsule"]),
    ("office", ["pen", "pencil", "notebook", "marker", "stapler", "paper", "binder", "calendar", "planner", "sticky", "eraser", "ruler", "scissors", "tape"]),
    ("toys", ["game", "puzzle", "lego", "toy", "dice", "card game", "board game", "stem kit", "building"]),
    ("books", ["book", "guide", "encyclopedia", "novel", "cookbook", "manual", "almanac", "atlas"]),
    ("bags", ["bag", "wallet", "purse", "tote", "luggage", "duffel", "pouch", "sling"]),
    ("apparel", ["shirt", "jacket", "shoe", "boot", "sock", "glove", "hat", "belt", "watch", "sunglasses"]),
    ("auto", ["car ", "automotive", "tire", "vehicle", "motorcycle", "dash cam", "jump starter", "air duster"]),
    ("crafts", ["craft", "sewing", "needle", "yarn", "paint", "brush", "glue", "mold", "clay", "crayon", "sugru"]),
]

# ISBN-style ASINs (10-digit, often starting with a digit) are almost always books.
_ISBN_RE = re.compile(r"^\d{9}[\dxX]$")


def categorize(title: str, asin: str = "") -> str:
    """Return a coarse category string for a product, or 'other' if none match."""
    if asin and _ISBN_RE.match(asin):
        return "books"
    if not title:
        return "other"
    text = " " + title.lower() + " "
    for category, keywords in _CATEGORIES:
        for kw in keywords:
            # Whole-word-ish match: keyword bounded by non-alphanumerics.
            if re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", text):
                return category
    return "other"


def diversify(items, key_fn, category_fn, limit):
    """Pick up to `limit` items spread across categories.

    Greedy round-robin: walk the ranked `items` (already in priority order) and
    take the highest-priority item from each not-yet-used category before
    allowing a second item from any category. Preserves the incoming ranking as
    the tiebreaker so the best deal in each category surfaces first.

    `key_fn(item)` -> unique id (for de-dup), `category_fn(item)` -> category.
    Returns the chosen items in pick order.
    """
    chosen = []
    used_categories = set()
    used_ids = set()
    # First pass: one per category.
    for it in items:
        if len(chosen) >= limit:
            return chosen
        cat = category_fn(it)
        if cat in used_categories:
            continue
        chosen.append(it)
        used_categories.add(cat)
        used_ids.add(key_fn(it))
    # Second pass: fill remaining slots with the best leftovers, any category.
    for it in items:
        if len(chosen) >= limit:
            break
        if key_fn(it) in used_ids:
            continue
        chosen.append(it)
        used_ids.add(key_fn(it))
    return chosen
