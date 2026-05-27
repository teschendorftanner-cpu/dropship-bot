import logging
import re
from database import get_ready_products, save_listing, mark_product_listed
from ebay_client import create_listing, DuplicateListing, InvalidCategory, get_suggested_category

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
        features.append("Wireless Bluetooth — completely cable-free freedom")
    if "led" in t:
        features.append("Energy-efficient LED technology — bright, vivid, long-lasting")
    if "rechargeable" in t or "battery" in t:
        features.append("Built-in rechargeable battery — charge once, use all day")
    if "waterproof" in t or "water resistant" in t:
        features.append("IPX waterproof / water-resistant — built for any condition")
    if "adjustable" in t:
        features.append("Fully adjustable design — customise to your exact needs")
    if "stainless" in t:
        features.append("Premium stainless steel construction — rust-proof and long-lasting")
    if "portable" in t or "compact" in t:
        features.append("Ultra-lightweight and portable — perfect for travel and on-the-go")
    if "usb" in t or "charging" in t:
        features.append("Universal USB compatibility — works with all modern devices")
    if "touch" in t:
        features.append("Intuitive one-touch controls for effortless operation")
    if "noise" in t or "cancell" in t:
        features.append("Active noise cancellation — immersive, distraction-free experience")
    if "smart" in t:
        features.append("Smart technology built-in — intelligent and responsive")
    if "solar" in t:
        features.append("Solar-powered — eco-friendly and self-charging in sunlight")
    if "silicone" in t or "soft" in t:
        features.append("Soft silicone material — gentle, flexible, and durable")
    if "gold" in t or "silver" in t or "steel" in t:
        features.append("Elegant metallic finish — premium look and feel")
    if "kids" in t or "child" in t or "baby" in t:
        features.append("Child-safe materials — tested and safe for all ages")
    if "cat" in t or "dog" in t or "pet" in t:
        features.append("Durable and pet-safe — designed for active animals")
    features += [
        "100% brand new — unused, in original packaging",
        "Premium quality materials — built to last",
        "Makes a thoughtful gift for any occasion",
    ]
    return features[:9]


def _build_item_specifics(title: str, category_id: str = "112581") -> list[tuple[str, str]]:
    t = title.lower()
    specs = [
        ("Brand", "Unbranded"),
        ("Type", "Other"),
        ("Country/Region of Manufacture", "China"),
    ]

    # Clothing & shoes categories require Size, Department, Style
    APPAREL_CATS = {
        "63861", "55793", "26350", "11554", "15687", "11555",
        "3002", "63862", "63863", "63864",
    }
    if category_id in APPAREL_CATS:
        specs += [
            ("Department", "Unisex Adults"),
            ("Size", "One Size"),
            ("Style", "Casual"),
        ]

    if "bluetooth" in t or "wireless" in t:
        specs.append(("Connectivity", "Bluetooth / Wireless"))
    if "usb" in t:
        specs.append(("Interface", "USB"))
    if "waterproof" in t:
        specs.append(("Water Resistance Level", "Waterproof"))
    if "led" in t:
        specs.append(("Light Source", "LED"))
    if "rechargeable" in t:
        specs.append(("Power Source", "Rechargeable Battery"))
    for color in ("gold", "silver", "black", "white", "pink", "blue", "red", "green", "rose"):
        if color in t:
            specs.append(("Color", color.capitalize()))
            break
    else:
        specs.append(("Color", "As Shown"))
    return specs


def _build_description(title: str, image_urls: list = None) -> str:
    features = _extract_features(title)
    feat_items = "".join(f"<li style='margin-bottom:6px'>{f}</li>" for f in features)

    img_html = ""
    if image_urls:
        imgs = "".join(
            f'<img src="{u}" style="max-width:260px;margin:6px;border-radius:8px;border:1px solid #ddd;box-shadow:0 2px 6px rgba(0,0,0,0.08)">'
            for u in image_urls[:12]
        )
        img_html = f'<div style="text-align:center;padding:16px 0;background:#fafafa;border-radius:8px;margin-bottom:16px">{imgs}</div>'

    return f"""<div style="font-family:Arial,sans-serif;max-width:740px;margin:0 auto;color:#222;padding:20px;line-height:1.6">

  <h1 style="font-size:22px;border-bottom:3px solid #e63946;padding-bottom:10px;margin-bottom:16px;color:#111">{title}</h1>

  {img_html}

  <div style="background:#fff8f8;border-left:4px solid #e63946;padding:12px 16px;border-radius:4px;margin-bottom:20px;font-size:15px">
    <strong>⭐ Why customers love this product:</strong> Premium quality, fast shipping, and backed by our 30-day money-back guarantee.
  </div>

  <h3 style="color:#e63946;margin-top:20px;font-size:17px">✨ Key Features &amp; Benefits</h3>
  <ul style="line-height:2;padding-left:22px;margin-bottom:20px">{feat_items}</ul>

  <h3 style="color:#e63946;font-size:17px">📋 Product Details</h3>
  <table style="width:100%;border-collapse:collapse;margin-bottom:20px;font-size:14px">
    <tr style="background:#f8f8f8"><td style="padding:8px 12px;border:1px solid #eee;font-weight:bold;width:40%">Condition</td><td style="padding:8px 12px;border:1px solid #eee">Brand New — Never Used</td></tr>
    <tr><td style="padding:8px 12px;border:1px solid #eee;font-weight:bold">Package Contents</td><td style="padding:8px 12px;border:1px solid #eee">1x {title[:60]}</td></tr>
    <tr style="background:#f8f8f8"><td style="padding:8px 12px;border:1px solid #eee;font-weight:bold">Brand</td><td style="padding:8px 12px;border:1px solid #eee">Unbranded / Generic</td></tr>
    <tr><td style="padding:8px 12px;border:1px solid #eee;font-weight:bold">Suitable For</td><td style="padding:8px 12px;border:1px solid #eee">Adults, Teens, Gift Use</td></tr>
  </table>

  <h3 style="color:#e63946;font-size:17px">📦 Shipping &amp; Delivery</h3>
  <ul style="line-height:2;padding-left:22px;margin-bottom:20px">
    <li><strong>Same/next-day dispatch</strong> — orders processed fast</li>
    <li><strong>Free shipping</strong> — no hidden fees, ever</li>
    <li><strong>Full order tracking</strong> — track every step of the way</li>
    <li><strong>Estimated delivery:</strong> 7–15 business days to your door</li>
  </ul>

  <h3 style="color:#e63946;font-size:17px">🔒 Buyer Protection &amp; Guarantee</h3>
  <ul style="line-height:2;padding-left:22px;margin-bottom:20px">
    <li>✅ <strong>30-day hassle-free returns</strong> — full money-back guarantee</li>
    <li>✅ <strong>100% authentic</strong> — brand new in original packaging</li>
    <li>✅ <strong>eBay Money Back Guarantee</strong> — you're always protected</li>
    <li>✅ <strong>Fast support</strong> — we respond within hours, 7 days a week</li>
  </ul>

  <p style="background:#f0f8ff;border-left:4px solid #1a73e8;padding:12px 16px;border-radius:4px;margin-top:16px;font-size:14px">
    💬 <strong>Questions?</strong> Message us through eBay before or after your purchase — we're always happy to help!
  </p>

</div>"""


async def list_ready_products(limit: int = 10) -> list[dict]:
    products = get_ready_products(limit=limit)
    listed = []

    for p in products:
        title = _clean_title(p["title"])
        logger.info(f"Listing: {title[:60]}")
        category_id = get_suggested_category(title)

        # Build image list: main image + any extras stored during research
        image_urls = []
        if p.get("image_url"):
            image_urls.append(p["image_url"])
        if p.get("extra_images"):
            image_urls += [u.strip() for u in p["extra_images"].split(",") if u.strip()]
        image_urls = image_urls[:12]  # eBay max

        description = _build_description(title, image_urls)

        item_specifics = _build_item_specifics(title, category_id)

        try:
            ebay_item_id = await create_listing(
                title=title,
                description=description,
                price=p["ebay_price"],
                image_urls=image_urls,
                category_id=category_id,
                variant_id=p.get("cj_variant_id", ""),
                item_specifics=item_specifics,
            )
        except DuplicateListing:
            logger.info(f"  Already live on eBay, marking listed: {title[:50]}")
            mark_product_listed(p["id"])
            continue
        except InvalidCategory:
            logger.warning(f"  Category {category_id} rejected, retrying with fallback 112581")
            try:
                ebay_item_id = await create_listing(
                    title=title,
                    description=description,
                    price=p["ebay_price"],
                    image_urls=image_urls,
                    category_id="112581",
                    variant_id=p.get("cj_variant_id", ""),
                    item_specifics=_build_item_specifics(title, "112581"),
                )
            except Exception:
                ebay_item_id = None

        if not ebay_item_id:
            logger.warning(f"  Failed to list: {p['title'][:50]}")
            continue

        save_listing(
            product_id=p["id"],
            ebay_item_id=ebay_item_id,
            ebay_price=p["ebay_price"],
            cj_price=p["cj_price"],
        )
        mark_product_listed(p["id"])

        listed.append({
            "title": p["title"],
            "ebay_item_id": ebay_item_id,
            "ebay_price": p["ebay_price"],
            "cj_price": p["cj_price"],
            "margin_percent": p["margin_percent"],
        })
        logger.info(f"  ✅ Listed as eBay item {ebay_item_id} @ ${p['ebay_price']:.2f}")

    return listed
