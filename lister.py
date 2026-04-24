import logging
import re
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
    "watch": "31387",
    "default": "112581",
}


_BRAND_PATTERNS = re.compile(
    r'\b(shein|temu|aliexpress|amazon|walmart|alibaba|cross.?border)\b',
    re.IGNORECASE,
)
_FILLER_PATTERNS = re.compile(
    r'[-–]\s*(one pack|1 pack|1 pair|boxed|bag)\s*$',
    re.IGNORECASE,
)


def _clean_title(title: str) -> str:
    title = _BRAND_PATTERNS.sub('', title)
    title = _FILLER_PATTERNS.sub('', title)
    return ' '.join(title.split()).strip(' -–,')[:80]


def _guess_category(title: str) -> str:
    title_lower = title.lower()
    for keyword, cat_id in CATEGORY_MAP.items():
        if keyword in title_lower:
            return cat_id
    return CATEGORY_MAP["default"]


def _extract_features(title: str) -> list[str]:
    t = title.lower()
    features = []
    if "bluetooth" in t or "wireless" in t:
        features.append("Wireless Bluetooth — completely cable-free")
    if "led" in t:
        features.append("Energy-efficient LED technology — bright and long-lasting")
    if "rechargeable" in t or "battery" in t:
        features.append("Built-in rechargeable battery — charge once, use all day")
    if "waterproof" in t or "water resistant" in t:
        features.append("Waterproof / water-resistant — built for any condition")
    if "adjustable" in t:
        features.append("Fully adjustable — customise to your exact needs")
    if "stainless" in t:
        features.append("Premium stainless steel — rust-proof and long-lasting")
    if "portable" in t or "compact" in t:
        features.append("Lightweight and portable — perfect on the go")
    if "usb" in t:
        features.append("Universal USB compatibility — works with all devices")
    if "touch" in t:
        features.append("Intuitive touch controls for effortless operation")
    if "noise" in t or "cancell" in t:
        features.append("Active noise cancellation for immersive listening")
    if "smart" in t:
        features.append("Smart design with advanced built-in technology")
    if "solar" in t:
        features.append("Solar-powered — eco-friendly and self-charging")
    features += [
        "Brand new — never used, in original packaging",
        "Premium quality materials built to last",
        "Makes a perfect gift for any occasion",
    ]
    return features[:7]


def _build_description(title: str, image_urls: list = None) -> str:
    features = _extract_features(title)
    feat_items = "".join(f"<li>{f}</li>" for f in features)

    img_html = ""
    if image_urls:
        imgs = "".join(
            f'<img src="{u}" style="max-width:280px;margin:4px;border-radius:6px;border:1px solid #eee">'
            for u in image_urls[:6]
        )
        img_html = f'<div style="text-align:center;padding:12px 0">{imgs}</div>'

    return f"""<div style="font-family:Arial,sans-serif;max-width:720px;margin:0 auto;color:#222;padding:16px">
  <h1 style="font-size:20px;border-bottom:3px solid #e63946;padding-bottom:8px;margin-bottom:12px">{title}</h1>
  {img_html}
  <h3 style="color:#e63946;margin-top:16px">✨ Product Highlights</h3>
  <ul style="line-height:1.9;padding-left:20px">{feat_items}</ul>

  <h3 style="color:#e63946">📦 Shipping &amp; Delivery</h3>
  <ul style="line-height:1.9;padding-left:20px">
    <li><strong>Fast dispatch:</strong> Ships within 1–3 business days</li>
    <li><strong>Free shipping:</strong> No hidden fees — ever</li>
    <li><strong>Full tracking:</strong> Track your order every step of the way</li>
    <li><strong>Estimated delivery:</strong> 7–15 business days to your door</li>
  </ul>

  <h3 style="color:#e63946">🔒 Our Guarantee</h3>
  <ul style="line-height:1.9;padding-left:20px">
    <li>✅ 30-day hassle-free returns — money-back guarantee</li>
    <li>✅ 100% authentic product — brand new in original packaging</li>
    <li>✅ Protected by eBay buyer guarantee</li>
    <li>✅ Responsive support — we reply within hours</li>
  </ul>

  <p style="background:#fff8f8;border-left:4px solid #e63946;padding:10px 14px;border-radius:4px;margin-top:16px">
    <strong>Have a question?</strong> Message us through eBay — we're happy to help before or after your purchase!
  </p>
</div>"""


async def list_ready_products(limit: int = 10) -> list[dict]:
    products = get_ready_products(limit=limit)
    listed = []

    for p in products:
        title = _clean_title(p["title"])
        logger.info(f"Listing: {title[:60]}")
        category_id = _guess_category(title)

        # Build image list: main image + any extras stored during research
        image_urls = []
        if p.get("image_url"):
            image_urls.append(p["image_url"])
        if p.get("extra_images"):
            image_urls += [u.strip() for u in p["extra_images"].split(",") if u.strip()]
        image_urls = image_urls[:12]  # eBay max

        description = _build_description(title, image_urls)

        ebay_item_id = await create_listing(
            title=title,
            description=description,
            price=p["ebay_price"],
            image_urls=image_urls,
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
