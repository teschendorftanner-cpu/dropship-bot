import logging
import time
from config import MIN_MARGIN_PERCENT, MARKUP_PERCENT, EBAY_FEE_PERCENT, MAX_LISTINGS
from cj_client import search_products, get_shipping_cost, get_product_images
from ebay_client import get_sold_median
from database import upsert_product, get_active_listings

logger = logging.getLogger(__name__)


DEFAULT_KEYWORDS = [
    # ── Automotive (passionate buyers, fitment = less price competition) ──────
    "LED interior car lights RGB",
    "car phone mount magnetic dashboard",
    "dash cam front rear 1080p",
    "car seat cover waterproof universal",
    "car blind spot mirror convex",
    "LED license plate light truck",
    "leather steering wheel cover grip",
    "all weather car floor mat",
    "wireless backup camera rear view",
    "car seat back organizer pocket",
    "truck bed LED light strip",
    "car window sunshade foldable",
    "car air freshener vent clip",
    # ── Tools & hardware ──────────────────────────────────────────────────────
    "magnetic wristband screw holder",
    "LED rechargeable work light",
    "self leveling laser level green",
    "stud finder wall scanner digital",
    "tool belt pouch organizer",
    "cable management clips desk",
    # ── Gaming & PC ───────────────────────────────────────────────────────────
    "gaming headset RGB surround sound",
    "extended gaming mouse pad XXL",
    "controller charging station dock",
    "gaming chair lumbar support cushion",
    "monitor light bar screen lamp",
    "PC cable management kit sleeve",
    # ── Pet ───────────────────────────────────────────────────────────────────
    "dog car seat cover waterproof",
    "dog anxiety calming vest shirt",
    "automatic pet feeder programmable",
    "pet grooming deshedding glove",
    "cat window perch hammock seat",
    "retractable dog leash heavy duty",
    "dog paw cleaner portable muddy",
    # ── Home & office ─────────────────────────────────────────────────────────
    "solar garden pathway lights outdoor",
    "under cabinet LED light strip",
    "high pressure handheld shower head",
    "outlet wall shelf organizer",
    "door draft stopper bottom seal",
    "adjustable monitor riser desk stand",
    "foldable laptop stand aluminum",
    "bamboo desk organizer drawer set",
    # ── Outdoor & survival ────────────────────────────────────────────────────
    "rechargeable tactical flashlight 1000lm",
    "pocket multi tool folding knife",
    "solar rechargeable camping lantern",
    "collapsible water bottle hiking",
    # ── Health & wellness ─────────────────────────────────────────────────────
    "posture corrector upper back brace",
    "compression knee sleeve support brace",
    "electric heating pad back lumbar",
    "acupressure mat pillow set",
    "3D contour sleep eye mask",
    # ── Phone & electronics ───────────────────────────────────────────────────
    "wireless earbuds active noise cancelling",
    "20000mah portable power bank fast",
    "USB C braided fast charging cable",
    "ring light tripod stand selfie",
    "15W wireless charging pad fast",
    # ── Beauty ────────────────────────────────────────────────────────────────
    "jade roller gua sha facial set",
    "professional vegan makeup brush set",
    "nail art stamping kit set",
    # ── Kids ──────────────────────────────────────────────────────────────────
    "LCD writing tablet kids drawing",
    "sensory fidget toy pack set",
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
