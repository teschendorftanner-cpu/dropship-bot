import logging
import time
from config import MIN_MARGIN_PERCENT, MARKUP_PERCENT, EBAY_FEE_PERCENT, MAX_LISTINGS
from cj_client import search_products, get_shipping_cost, get_product_images
from ebay_client import get_sold_median
from database import upsert_product, get_active_listings

logger = logging.getLogger(__name__)


DEFAULT_KEYWORDS = [
    # Car accessories — high volume, strong margins
    "car phone holder mount",
    "car seat back organizer",
    "car led interior lights",
    "car air freshener vent",
    "car trash can mini",
    # Electronics & accessories
    "wireless earbuds bluetooth",
    "portable bluetooth speaker",
    "power bank fast charge",
    "usb c charging cable",
    "led strip lights bedroom",
    "ring light selfie phone",
    "wireless charging pad",
    # Phone accessories
    "phone stand desk holder",
    "phone lens camera kit",
    "pop socket phone grip",
    # Home & office
    "cable management clips",
    "desk organizer set",
    "monitor stand riser",
    "laptop stand adjustable",
    "under desk storage",
    "reusable water bottle",
    # Health & wellness
    "jade roller gua sha",
    "face massager roller",
    "posture corrector back",
    "neck back massager",
    "sleep eye mask",
    "back stretcher posture",
    # Fitness
    "resistance band set",
    "ab roller wheel",
    "jump rope speed",
    "foam roller massage",
    "gym water bottle",
    # Jewelry & accessories
    "stainless steel necklace women",
    "hoop earrings set",
    "layered bracelet set",
    "minimalist ring set",
    "polarized sunglasses",
    # Beauty
    "makeup brush set professional",
    "nail art kit",
    "false eyelashes natural",
    "hair scrunchie set",
    "eyebrow stamp stencil",
    # Kitchen
    "silicone kitchen utensils",
    "meal prep containers set",
    "garlic press stainless",
    "reusable silicone bags",
    # Pets
    "dog harness no pull",
    "cat toy interactive",
    "pet water bottle portable",
    # Kids
    "fidget toy sensory",
    "kids art craft kit",
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

    if keywords is None:
        from database import get_keywords
        db_keywords = get_keywords()
        keywords = db_keywords if db_keywords else DEFAULT_KEYWORDS
        logger.info(f"Using {'custom DB' if db_keywords else 'built-in'} keyword list ({len(keywords)} keywords)")
    found = []

    for keyword in keywords:
        if len(found) >= slots:
            break

        logger.info(f"Researching: '{keyword}'")
        cj_products = search_products(keyword, page_size=15)
        if not cj_products:
            logger.info(f"  No CJ results for '{keyword}'")
            continue

        # Check eBay demand once per keyword — this sets our price ceiling
        # and confirms buyers are actually paying for this category
        try:
            ebay_median = get_sold_median(keyword)
        except Exception:
            ebay_median = None

        if ebay_median is not None and ebay_median < 8:
            logger.info(f"  Skipping '{keyword}' — eBay median ${ebay_median:.2f} too low (no real demand)")
            time.sleep(0.3)
            continue

        if ebay_median:
            logger.info(f"  eBay median for '{keyword}': ${ebay_median:.2f}")

        count = 0
        for product in cj_products:
            if count >= max_per_keyword or len(found) >= slots:
                break

            cj_price = product["price"]
            # Sweet spot: cheap enough to have margin, not so expensive competition is fierce
            if cj_price < 5 or cj_price > 50:
                continue

            # Skip products with no image — kills the listing quality
            if not product.get("image_url"):
                continue

            shipping = get_shipping_cost(product["product_id"])
            total_cost = round(cj_price + shipping, 2)
            ebay_price = round(total_cost * (1 + MARKUP_PERCENT / 100), 2)

            # If eBay market data exists, price competitively below the median.
            # If our calculated price is already below median, keep it (more margin = better).
            if ebay_median is not None and ebay_price > ebay_median * 0.92:
                ebay_price = round(ebay_median * 0.92, 2)

            margin = calculate_margin(total_cost, ebay_price)
            if margin < MIN_MARGIN_PERCENT:
                continue

            extra_imgs = get_product_images(product["product_id"])
            extra_images_str = ",".join(extra_imgs[1:])

            result = upsert_product(
                cj_url=product["url"],
                cj_variant_id=product["variant_id"],
                title=product["title"],
                cj_price=total_cost,
                ebay_price=ebay_price,
                margin_percent=margin,
                category=product.get("category", ""),
                image_url=product.get("image_url", "") or (extra_imgs[0] if extra_imgs else ""),
                extra_images=extra_images_str,
            )

            if not result["ready"]:
                continue

            found.append({
                "product_id": result["id"],
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
