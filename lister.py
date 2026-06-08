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

_CATEGORY_HOOKS = {
    "automotive": "Give your ride the upgrade it deserves. Built for drivers who want better performance, cleaner installs, and a sharper look — without the dealership price tag.",
    "pet": "Your pet deserves the best. Made with pet-safe materials and tested for durability — because happy pets make happy owners.",
    "gaming": "Level up your setup. Engineered for serious gamers who demand precision, comfort, and style in every session.",
    "tools": "Built for the job. Professional-grade materials and precision engineering — whether you're a weekend DIYer or a working tradesman.",
    "electronics": "Stay connected, charged, and in control. Compatible with all major devices and built to handle everyday use.",
    "beauty": "Professional-quality results at home. No salon appointment needed — just real results you'll notice from day one.",
    "outdoor": "Built tough for the outdoors. Reliable, durable, and ready for whatever the trail throws at you.",
    "kids": "Safe, fun, and screen-friendly. Made with child-safe materials and designed to keep kids engaged for hours.",
    "fitness": "Train smarter, recover faster. Designed to support your body through every rep, stretch, and recovery session.",
    "general": "Premium quality at an unbeatable price. Built to last and backed by our 30-day money-back guarantee.",
}


def _detect_category(title: str) -> str:
    t = title.lower()
    if any(w in t for w in (
        "car", "truck", "vehicle", "auto", "suv", "jeep", "silverado", "sierra",
        "ford", "chevy", "dodge", "license plate", "dash cam", "steering wheel",
        "seat cover", "floor mat", "windshield", "tire", "wheel", "headlight",
        "taillight", "bumper", "mirror", "wiper", "tow", "hitch",
    )):
        return "automotive"
    if any(w in t for w in ("dog", "cat", "pet", "puppy", "kitten", "paw", "fur", "leash", "collar")):
        return "pet"
    if any(w in t for w in ("gaming", "game", "controller", "xbox", "ps4", "ps5", "nintendo", "rgb mouse", "rgb keyboard", "gaming headset")):
        return "gaming"
    if any(w in t for w in ("drill", "wrench", "level", "tool", "saw", "screwdriver", "hammer", "stud finder", "laser level", "socket")):
        return "tools"
    if any(w in t for w in ("phone", "iphone", "android", "earbuds", "earphone", "headset", "charging", "power bank", "bluetooth speaker", "charger")):
        return "electronics"
    if any(w in t for w in ("makeup", "beauty", "skin", "face mask", "hair", "nail", "eyelash", "gua sha", "jade roller", "serum", "foundation")):
        return "beauty"
    if any(w in t for w in ("camping", "hiking", "survival", "tactical", "outdoor", "flashlight", "lantern", "trail")):
        return "outdoor"
    if any(w in t for w in ("kids", "child", "baby", "toddler", "toy", "drawing tablet", "fidget", "crayon")):
        return "kids"
    if any(w in t for w in ("yoga", "fitness", "gym", "resistance band", "workout", "exercise", "posture", "knee brace", "back brace", "compression")):
        return "fitness"
    return "general"


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


def _extract_features(title: str, category: str = "general") -> list[str]:
    t = title.lower()
    features = []

    if category == "automotive":
        features.append("Simple plug-and-play install — no cutting, splicing, or special tools needed")
        if "led" in t:
            features.append("High-output LED technology — dramatically brighter than stock bulbs, draws less power")
        if "2x" in t or "pair" in t or "set" in t:
            features.append("Comes as a complete set — everything needed for a full installation included")
        if "waterproof" in t or "weather" in t or "sealed" in t:
            features.append("Weatherproof sealed housing — rated against rain, road spray, and dust")
        if "universal" in t or "fit" in t:
            features.append("Wide vehicle compatibility — fits most makes and models, check listing for details")
        if "rgb" in t or "color" in t:
            features.append("Multiple color/mode options — customize the look to match your build")
        if "camera" in t or "cam" in t:
            features.append("Wide-angle lens captures full lane coverage — eliminates dangerous blind spots")

    elif category == "pet":
        features.append("100% pet-safe, non-toxic materials — independently tested and certified")
        features.append("Easy to clean — machine washable or quick wipe-down")
        if "dog" in t:
            features.append("Adjustable fit for all sizes — small, medium, and large breeds covered")
        if "waterproof" in t or "cover" in t:
            features.append("Heavy-duty waterproof liner — protects upholstery from muddy paws and accidents")
        if "anxiety" in t or "calming" in t:
            features.append("Gentle compression design mimics a reassuring hug — reduces stress in dogs")
        if "feeder" in t:
            features.append("Programmable meal schedule — feeds on time even when you're not home")

    elif category == "gaming":
        features.append("Ergonomic design engineered for marathon sessions — no hand fatigue or discomfort")
        if "rgb" in t or "led" in t:
            features.append("Fully customizable RGB lighting — sync with your setup for the perfect aesthetic")
        if "headset" in t:
            features.append("Immersive surround sound audio — hear every footstep, reload, and explosion in detail")
        if "pad" in t or "mousepad" in t:
            features.append("Ultra-smooth surface optimized for both high-DPI and low-DPI tracking")
        if "charging" in t or "dock" in t:
            features.append("Dual-controller charging — keeps both controllers ready so you never run out mid-game")

    elif category == "tools":
        features.append("Professional-grade hardened construction — built to take heavy daily use without failing")
        if "magnetic" in t:
            features.append("Strong rare-earth magnets — keeps screws, bolts, and bits right at your fingertips")
        if "led" in t or "light" in t:
            features.append("Integrated LED lighting — work clearly in dark corners, attics, and crawl spaces")
        if "laser" in t:
            features.append("Self-leveling laser — delivers accurate horizontal and vertical lines in seconds")
        if "rechargeable" in t:
            features.append("USB rechargeable battery — full power without the cost of constant AA replacements")

    elif category == "electronics":
        if "wireless" in t or "bluetooth" in t:
            features.append("Bluetooth 5.0 — rock-solid connection up to 33ft with zero audio dropouts")
        if "noise cancel" in t or "anc" in t:
            features.append("Active noise cancellation — blocks traffic, office noise, and background chatter")
        if "fast charg" in t or "quick charg" in t or "20w" in t or "45w" in t:
            features.append("Fast charging technology — 0 to 50% in under 30 minutes")
        if "power bank" in t:
            features.append("High-capacity battery — charges a smartphone 4–6 times on a single charge")
        if "ring light" in t:
            features.append("Even, flattering light eliminates harsh shadows — perfect for video calls and content")

    elif category == "beauty":
        features.append("Professional salon results from the comfort of home — no appointment needed")
        if "jade" in t or "roller" in t or "gua sha" in t:
            features.append("Improves circulation and reduces puffiness — visible results with daily use")
        if "brush" in t:
            features.append("Ultra-soft synthetic bristles — applies product flawlessly without streaking")
        if "nail" in t:
            features.append("Complete nail art system — everything you need for salon-quality designs at home")

    elif category == "outdoor":
        features.append("Military-grade durability — aircraft-grade aluminum body survives drops and rough handling")
        if "flashlight" in t or "torch" in t:
            features.append("1000+ lumen output — lights up an area the size of a basketball court from 50 yards")
        if "solar" in t:
            features.append("Solar charging — self-powers during the day, runs automatically at night")
        if "multi tool" in t or "multitool" in t:
            features.append("15+ functions in a pocket-sized package — knife, pliers, saw, screwdriver, and more")

    elif category == "kids":
        features.append("Non-toxic, child-safe materials — meets all US and EU safety standards")
        if "drawing" in t or "tablet" in t or "lcd" in t:
            features.append("One-button erase — kids can draw, erase, and start again endlessly with no paper waste")
        if "fidget" in t:
            features.append("Helps focus and reduce anxiety — proven sensory tool for kids and adults alike")

    elif category == "fitness":
        features.append("Breathable, moisture-wicking fabric — stays dry and comfortable through intense workouts")
        if "posture" in t or "back" in t:
            features.append("Medically designed posture correction — trains muscles to hold proper alignment naturally")
        if "knee" in t or "compression" in t:
            features.append("Graduated compression reduces swelling and supports joints during activity and recovery")
        if "heating" in t or "heat" in t:
            features.append("Multiple heat settings — gentle warmth to deep therapeutic heat for muscle relief")

    # Universal additions based on keywords not caught above
    if "waterproof" in t and not any("waterproof" in f.lower() for f in features):
        features.append("Fully waterproof rated — sealed against rain, splashes, and spills")
    if "solar" in t and not any("solar" in f.lower() for f in features):
        features.append("Solar-powered — eco-friendly and self-charging in natural sunlight")
    if "rechargeable" in t and not any("recharg" in f.lower() for f in features):
        features.append("Built-in rechargeable battery — USB charging, never buy batteries again")
    if ("stainless" in t or "aluminum" in t) and not any("steel" in f.lower() or "aluminum" in f.lower() for f in features):
        features.append("Premium metal construction — rust-proof, lightweight, and built to last for years")
    if "portable" in t and not any("portable" in f.lower() for f in features):
        features.append("Compact and lightweight — fits in a bag or pocket, ready whenever you need it")

    features += [
        "Brand new in original packaging — 100% unused, full quality guaranteed",
        "Makes a great gift — ideal for birthdays, holidays, or any occasion",
    ]
    return features[:8]


def _build_item_specifics(title: str, category_id: str = "112581") -> list[tuple[str, str]]:
    t = title.lower()
    category = _detect_category(title)
    specs = [
        ("Brand", "Unbranded"),
        ("Country/Region of Manufacture", "China"),
        ("Condition", "New"),
        ("MPN", "Does Not Apply"),
    ]

    if category == "automotive":
        specs += [
            ("Compatible Vehicle Make", "Universal Fit"),
            ("Compatible Vehicle Type", "Car, Truck, SUV, Van"),
            ("Warranty", "1 Year"),
        ]
        if "led" in t:
            specs.append(("Light Source Type", "LED"))
        if "waterproof" in t:
            specs.append(("Water Resistance Level", "Waterproof"))
        if "camera" in t or "cam" in t:
            specs.append(("Features", "Night Vision, Wide Angle, Loop Recording"))
        if "seat cover" in t:
            specs.append(("Placement on Vehicle", "Rear, Front"))
    elif category == "pet":
        specs += [
            ("Animal Type", "Dog, Cat, Pet"),
            ("Material", "Polyester / Neoprene"),
        ]
        if "waterproof" in t:
            specs.append(("Water Resistance Level", "Waterproof"))
        if "dog" in t:
            specs.append(("Size", "Small, Medium, Large — Adjustable"))
    elif category == "gaming":
        specs += [
            ("Platform", "PlayStation, Xbox, PC, Nintendo Switch"),
            ("Type", "Gaming Accessory"),
        ]
        if "rgb" in t or "led" in t:
            specs.append(("Features", "RGB Lighting, Multiple Modes"))
        if "headset" in t:
            specs.append(("Connectivity", "Wired / Wireless"))
            specs.append(("Audio Output Mode", "Stereo / Surround Sound"))
    elif category == "tools":
        specs += [
            ("Material", "Hardened Steel / ABS"),
            ("Power Source", "Battery / Rechargeable"),
        ]
        if "laser" in t:
            specs.append(("Features", "Self-Leveling, 360 Degree"))
    elif category == "electronics":
        if "bluetooth" in t or "wireless" in t:
            specs.append(("Connectivity", "Bluetooth 5.0"))
        if "waterproof" in t:
            specs.append(("Water Resistance Level", "IPX7 Waterproof"))
        if "charging" in t or "power bank" in t or "usb c" in t:
            specs.append(("Interface", "USB-C"))
        if "noise" in t or "anc" in t:
            specs.append(("Features", "Active Noise Cancellation"))
    elif category == "fitness":
        specs += [
            ("Material", "Neoprene / Elastic / Breathable Fabric"),
            ("Size", "Adjustable — One Size Fits Most"),
        ]
    elif category == "outdoor":
        specs.append(("Material", "Aircraft-Grade Aluminum / ABS"))
        if "flashlight" in t:
            specs.append(("Power Source", "Rechargeable Battery"))
            specs.append(("Features", "High Lumen, Waterproof, Zoomable"))

    # Apparel categories
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
        if not any(s[0] == "Connectivity" for s in specs):
            specs.append(("Connectivity", "Bluetooth / Wireless"))
    if "usb" in t:
        if not any(s[0] == "Interface" for s in specs):
            specs.append(("Interface", "USB"))
    if "led" in t:
        if not any(s[0] == "Light Source Type" for s in specs):
            specs.append(("Light Source Type", "LED"))
    if "rechargeable" in t:
        if not any(s[0] == "Power Source" for s in specs):
            specs.append(("Power Source", "Rechargeable Battery"))

    for color in ("black", "white", "silver", "chrome", "gold", "red", "blue", "green", "pink", "gray", "clear"):
        if color in t:
            specs.append(("Color", color.capitalize()))
            break
    else:
        specs.append(("Color", "As Shown"))

    return specs


def _build_description(title: str, image_urls: list = None) -> str:
    category = _detect_category(title)
    hook = _CATEGORY_HOOKS.get(category, _CATEGORY_HOOKS["general"])
    features = _extract_features(title, category)
    feat_items = "".join(f"<li style='margin-bottom:9px'>{f}</li>" for f in features)

    img_html = ""
    if image_urls:
        imgs = "".join(
            f'<img src="{u}" style="max-width:240px;margin:6px;border-radius:8px;border:1px solid #e0e0e0;box-shadow:0 2px 8px rgba(0,0,0,0.10)">'
            for u in image_urls[:12]
        )
        img_html = f'<div style="text-align:center;padding:18px 0;background:#f9f9f9;border-radius:8px;margin-bottom:22px">{imgs}</div>'

    compat_html = ""
    if category == "automotive":
        compat_html = """
  <div style="background:#fff8e1;border-left:4px solid #f59e0b;padding:12px 16px;border-radius:4px;margin-bottom:20px;font-size:14px">
    <strong>⚠️ Fitment Note:</strong> Please verify your vehicle&apos;s make, model, and year before ordering. Most items are universal fit — check product images and title for specifics.
  </div>"""

    return f"""<div style="font-family:Arial,sans-serif;max-width:760px;margin:0 auto;color:#1a1a1a;padding:22px;line-height:1.75">

  <h1 style="font-size:22px;border-bottom:3px solid #c0392b;padding-bottom:10px;margin-bottom:18px;color:#111;font-weight:700">{title}</h1>

  {img_html}

  <div style="background:#fef9f9;border-left:5px solid #c0392b;padding:14px 18px;border-radius:4px;margin-bottom:22px;font-size:15px;line-height:1.65;color:#333">
    {hook}
  </div>

  {compat_html}

  <h3 style="color:#c0392b;font-size:17px;margin-top:24px;margin-bottom:10px">✅ Key Features &amp; Benefits</h3>
  <ul style="line-height:2;padding-left:22px;margin-bottom:24px;font-size:14px">{feat_items}</ul>

  <h3 style="color:#c0392b;font-size:17px;margin-bottom:10px">📋 Product Details</h3>
  <table style="width:100%;border-collapse:collapse;margin-bottom:24px;font-size:14px">
    <tr style="background:#f8f8f8">
      <td style="padding:9px 14px;border:1px solid #eee;font-weight:bold;width:38%">Condition</td>
      <td style="padding:9px 14px;border:1px solid #eee">Brand New — Unused, Original Packaging</td>
    </tr>
    <tr>
      <td style="padding:9px 14px;border:1px solid #eee;font-weight:bold">Package Includes</td>
      <td style="padding:9px 14px;border:1px solid #eee">{title[:70]}</td>
    </tr>
    <tr style="background:#f8f8f8">
      <td style="padding:9px 14px;border:1px solid #eee;font-weight:bold">Brand</td>
      <td style="padding:9px 14px;border:1px solid #eee">Unbranded / Generic</td>
    </tr>
    <tr>
      <td style="padding:9px 14px;border:1px solid #eee;font-weight:bold">Ships From</td>
      <td style="padding:9px 14px;border:1px solid #eee">US Warehouse / International</td>
    </tr>
  </table>

  <h3 style="color:#c0392b;font-size:17px;margin-bottom:10px">🚚 Shipping &amp; Delivery</h3>
  <ul style="line-height:2;padding-left:22px;margin-bottom:24px;font-size:14px">
    <li><strong>Free shipping</strong> — no hidden fees, ever</li>
    <li><strong>Fast dispatch</strong> — same or next business day processing</li>
    <li><strong>Full tracking provided</strong> — monitor your order every step of the way</li>
    <li><strong>Estimated delivery:</strong> 7–15 business days to your door</li>
  </ul>

  <h3 style="color:#c0392b;font-size:17px;margin-bottom:10px">🔒 Guarantee &amp; Returns</h3>
  <ul style="line-height:2;padding-left:22px;margin-bottom:20px;font-size:14px">
    <li>✅ <strong>30-day hassle-free returns</strong> — if you're not happy, we'll fix it</li>
    <li>✅ <strong>100% money-back guarantee</strong> — no questions asked</li>
    <li>✅ <strong>eBay Money Back Guarantee</strong> — every purchase is protected</li>
    <li>✅ <strong>Responsive support</strong> — we reply within hours, 7 days a week</li>
  </ul>

  <p style="background:#eaf4fb;border-left:4px solid #2980b9;padding:13px 17px;border-radius:4px;font-size:14px;margin-top:20px">
    💬 <strong>Questions?</strong> Message us through eBay before or after your purchase — we&apos;re here to help and always respond fast.
  </p>

</div>"""


async def list_ready_products(limit: int = 10) -> list[dict]:
    products = get_ready_products(limit=limit)
    listed = []

    for p in products:
        title = _clean_title(p["title"])
        logger.info(f"Listing: {title[:60]}")
        category_id = get_suggested_category(title)

        # Build image list: fetch extra images now at listing time
        from cj_client import get_product_images
        from urllib.parse import urlparse
        pid_match = re.search(r'-p-([A-Za-z0-9]+)\.html', p.get("cj_url", ""))
        extra_imgs = get_product_images(pid_match.group(1)) if pid_match else []
        image_urls = []
        if p.get("image_url"):
            image_urls.append(p["image_url"])
        for u in extra_imgs:
            if u not in image_urls:
                image_urls.append(u)
        if p.get("extra_images"):
            image_urls += [u.strip() for u in p["extra_images"].split(",") if u.strip() and u.strip() not in image_urls]
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
