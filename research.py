import logging
import time
from config import MIN_MARGIN_PERCENT, MARKUP_PERCENT, EBAY_FEE_PERCENT, MAX_LISTINGS
from cj_client import search_products, get_shipping_cost
from database import upsert_product, get_active_listings

logger = logging.getLogger(__name__)

DEFAULT_KEYWORDS = [
    "wireless earbuds",
    "led strip lights",
    "portable charger",
    "phone stand",
    "desk lamp",
    "yoga mat",
    "resistance bands",
    "water bottle",
    "bluetooth speaker",
    "ring light",
    "cable organizer",
    "laptop stand",
    "storage bins",
    "car accessories",
    "gaming accessories",
    "pet supplies",
    "home decor",
    "makeup brushes",
    "kitchen gadgets",
    "phone case",
]


def calculate_margin(cost: float, ebay_price: float) -> float:
    revenue = ebay_price * (1 - EBAY_FEE_PERCENT / 100)
    return round(((revenue - cost) / ebay_price) * 100, 2)


def research_products(keywords: list[str] = None, max_per_keyword: int = 3) -> list[dict]:
    active_count = len(get_active_listings())
    slots = MAX_LISTINGS - active_count
    if slots <= 0:
        logger.info("Max listings reached — skipping research")
        return []

    keywords = keywords or DEFAULT_KEYWORDS
    found = []

    for keyword in keywords:
        if len(found) >= slots:
            break

        logger.info(f"Researching: '{keyword}'")
        cj_products = search_products(keyword, page_size=15)
        if not cj_products:
            logger.info(f"  No CJ results for '{keyword}'")
            continue

        count = 0
        for product in cj_products:
            if count >= max_per_keyword or len(found) >= slots:
                break

            cj_price = product["price"]
            if cj_price < 3 or cj_price > 150:
                continue

            shipping = get_shipping_cost(product["product_id"])
            total_cost = round(cj_price + shipping, 2)
            ebay_price = round(total_cost * (1 + MARKUP_PERCENT / 100), 2)
            margin = calculate_margin(total_cost, ebay_price)

            if margin < MIN_MARGIN_PERCENT:
                continue

            product_id = upsert_product(
                walmart_url=product["url"],
                walmart_item_id=product["variant_id"],
                title=product["title"],
                walmart_price=total_cost,
                ebay_price=ebay_price,
                margin_percent=margin,
                category=product.get("category", ""),
                image_url=product.get("image_url", ""),
            )

            found.append({
                "product_id": product_id,
                "title": product["title"],
                "total_cost": total_cost,
                "ebay_price": ebay_price,
                "margin_percent": margin,
                "variant_id": product["variant_id"],
                "image_url": product.get("image_url", ""),
            })

            logger.info(
                f"  ✅ '{product['title'][:50]}' "
                f"cost=${total_cost:.2f} → eBay=${ebay_price:.2f} ({margin:.1f}%)"
            )
            count += 1
            time.sleep(0.5)

    return found
