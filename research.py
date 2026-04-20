import logging
import statistics
from config import MIN_MARGIN_PERCENT, MARKUP_PERCENT, EBAY_FEE_PERCENT, MAX_LISTINGS
from cj_client import search_products, get_shipping_cost
from ebay_client import get_sold_median
from database import upsert_product, get_active_listings

logger = logging.getLogger(__name__)

DEFAULT_KEYWORDS = [
    "wireless earbuds",
    "led strip lights",
    "portable charger",
    "phone stand",
    "desk lamp",
    "kitchen gadgets",
    "yoga mat",
    "resistance bands",
    "water bottle",
    "bluetooth speaker",
    "ring light",
    "cable organizer",
    "laptop stand",
    "shower caddy",
    "storage bins",
    "car accessories",
    "gaming accessories",
    "pet supplies",
    "home decor",
    "makeup brushes",
]


def calculate_margin(cost: float, ebay_price: float) -> float:
    revenue = ebay_price * (1 - EBAY_FEE_PERCENT / 100)
    return round(((revenue - cost) / ebay_price) * 100, 2)


def research_products(keywords: list[str] = None, max_per_keyword: int = 5) -> list[dict]:
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
        ebay_median = get_sold_median(keyword)
        if not ebay_median:
            logger.info(f"  No eBay sold data for '{keyword}'")
            continue

        cj_products = search_products(keyword, page_size=15)
        if not cj_products:
            logger.info(f"  No CJ results for '{keyword}'")
            continue

        for product in cj_products:
            cj_price = product["price"]
            shipping = get_shipping_cost(product["product_id"])
            total_cost = cj_price + shipping

            if total_cost < 3 or total_cost > 150:
                continue

            # Price competitively under eBay median
            ebay_price = round(min(ebay_median * 0.93, total_cost * (1 + MARKUP_PERCENT / 100)), 2)
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
                "cj_price": cj_price,
                "shipping": shipping,
                "total_cost": total_cost,
                "ebay_price": ebay_price,
                "margin_percent": margin,
                "ebay_median": ebay_median,
                "variant_id": product["variant_id"],
                "image_url": product.get("image_url", ""),
            })

            logger.info(
                f"  ✅ '{product['title'][:50]}' "
                f"CJ=${total_cost:.2f} → eBay=${ebay_price:.2f} ({margin:.1f}%)"
            )

            if len(found) >= max_per_keyword or len(found) >= slots:
                break

    return found
