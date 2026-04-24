import logging
from database import get_ready_products, save_listing, mark_product_listed
from ebay_client import create_listing

logger = logging.getLogger(__name__)

CATEGORY_MAP = {
    "dress": "63861",
    "blazer": "63861",
    "sandal": "55793",
    "shoe": "55793",
    "yoga": "26350",
    "fitness": "26350",
    "resistance": "26350",
    "massage": "26350",
    "beauty": "26395",
    "makeup": "26395",
    "hair": "26395",
    "skin": "26395",
    "face": "26395",
    "kitchen": "20625",
    "home": "11700",
    "pet": "1281",
    "car": "6030",
    "lamp": "112581",
    "light": "112581",
    "speaker": "14969",
    "earbuds": "14969",
    "headset": "14969",
    "watch": "31387",
    "default": "11700",
}


def _guess_category(title: str) -> str:
    title_lower = title.lower()
    for keyword, cat_id in CATEGORY_MAP.items():
        if keyword in title_lower:
            return cat_id
    return CATEGORY_MAP["default"]


def _build_description(title: str) -> str:
    return f"""<div style="font-family:Arial,sans-serif;max-width:600px">
<h2>{title}</h2>
<p><strong>Brand new item.</strong> Fast shipping from US warehouse.</p>
<ul>
  <li>Ships within 1-3 business days</li>
  <li>30-day hassle-free returns</li>
  <li>Tracked shipping with delivery confirmation</li>
</ul>
<p>Questions? Message us — we respond within hours.</p>
</div>"""


async def list_ready_products(limit: int = 10) -> list[dict]:
    products = get_ready_products(limit=limit)
    listed = []

    for p in products:
        logger.info(f"Listing: {p['title'][:60]}")
        category_id = _guess_category(p["title"])
        description = _build_description(p["title"])

        ebay_item_id = await create_listing(
            title=p["title"],
            description=description,
            price=p["ebay_price"],
            image_url=p.get("image_url", ""),
            category_id=category_id,
        )

        if not ebay_item_id:
            logger.warning(f"  Failed to list: {p['title'][:50]}")
            continue

        save_listing(
            product_id=p["id"],
            ebay_item_id=ebay_item_id,
            ebay_price=p["ebay_price"],
            walmart_price=p["walmart_price"],
        )
        mark_product_listed(p["id"])

        listed.append({
            "title": p["title"],
            "ebay_item_id": ebay_item_id,
            "ebay_price": p["ebay_price"],
            "walmart_price": p["walmart_price"],
            "margin_percent": p["margin_percent"],
        })
        logger.info(f"  ✅ Listed as eBay item {ebay_item_id} @ ${p['ebay_price']:.2f}")

    return listed
