import logging
import time
import requests
from config import CJ_EMAIL, CJ_PASSWORD, CJ_API_KEY

logger = logging.getLogger(__name__)

BASE_URL = "https://developers.cjdropshipping.com/api2.0/v1"
_token = None
_token_expires_at = 0


def _get_token() -> str | None:
    global _token, _token_expires_at
    now = time.time()
    if _token and now < _token_expires_at - 300:
        return _token

    try:
        resp = requests.post(
            f"{BASE_URL}/authentication/getAccessToken",
            json={"apiKey": CJ_API_KEY},
            timeout=15
        )
        data = resp.json()
        if data.get("result"):
            _token = data["data"]["accessToken"]
            # CJ tokens are valid for ~24h; use a fixed 23h TTL so we always
            # refresh before expiry regardless of what the API returns
            _token_expires_at = now + 23 * 3600
            logger.info("CJ Dropshipping authenticated.")
            return _token
        logger.error(f"CJ auth failed: {data.get('message')}")
    except Exception as e:
        logger.error(f"CJ auth error: {e}")
    return None


def _headers() -> dict:
    return {"CJ-Access-Token": _get_token() or ""}


def search_products(keyword: str, page: int = 1, page_size: int = 20) -> list[dict]:
    try:
        resp = requests.get(
            f"{BASE_URL}/product/list",
            headers=_headers(),
            params={"productName": keyword, "pageNum": page, "pageSize": page_size},
            timeout=15
        )
        data = resp.json()
        if not data.get("result"):
            return []
        items = data.get("data", {}).get("list", [])
        results = []
        for item in items:
            try:
                variants = item.get("variants", [{}])
                price = float(variants[0].get("variantSellPrice") or item.get("sellPrice") or 0)
                if price <= 0:
                    continue
                results.append({
                    "product_id": item.get("pid", ""),
                    "variant_id": variants[0].get("vid", ""),
                    "title": item.get("productNameEn", ""),
                    "price": price,
                    "image_url": item.get("productImage", ""),
                    "category": item.get("categoryName", ""),
                    "url": f"https://cjdropshipping.com/product/-p-{item.get('pid','')}.html",
                    "shipping_time": item.get("productWeight", ""),
                })
            except (KeyError, IndexError, ValueError):
                continue
        return results
    except Exception as e:
        logger.error(f"CJ search error: {e}")
        return []


def get_product(product_id: str) -> dict | None:
    try:
        resp = requests.get(
            f"{BASE_URL}/product/query",
            headers=_headers(),
            params={"pid": product_id},
            timeout=15
        )
        data = resp.json()
        if data.get("result"):
            return data.get("data")
    except Exception as e:
        logger.error(f"CJ product fetch error: {e}")
    return None


def get_product_images(product_id: str) -> list[str]:
    """Return up to 12 image URLs for a product (main + image set + variant images)."""
    data = get_product(product_id)
    if not data:
        return []
    images = []

    def _add(url):
        url = (url or "").strip()
        if url and url not in images:
            images.append(url)

    _add(data.get("productImage", ""))

    image_set = data.get("productImageSet", "")
    if isinstance(image_set, str):
        for url in image_set.split(","):
            _add(url)
    elif isinstance(image_set, list):
        for url in image_set:
            _add(url)

    # Collect variant images as fallback
    for variant in data.get("variants", []):
        _add(variant.get("variantImage", ""))

    logger.info(f"CJ images for {product_id}: {len(images)} found")
    return images[:12]


def get_shipping_cost(product_id: str, country_code: str = "US") -> float:
    try:
        resp = requests.get(
            f"{BASE_URL}/logistic/freightCalculate",
            headers=_headers(),
            params={"pid": product_id, "countryCode": country_code, "quantity": 1},
            timeout=15
        )
        data = resp.json()
        if data.get("result"):
            options = data.get("data", [])
            if options:
                costs = [float(o.get("logisticPrice", 999)) for o in options]
                return min(costs)
    except Exception as e:
        logger.debug(f"CJ shipping calc error: {e}")
    return 8.0  # conservative fallback — real CJ-to-US shipping averages $6–12


_SHIPPING_METHODS = [
    "CJPacket", "CJPacket Sensitive", "CJPacket Economy",
    "CJPacket Ordinary", "USPS", "PostNL",
]


def place_order(order_ref: str, variant_id: str, buyer: dict) -> dict:
    """
    Place a CJ dropshipping order to ship directly to the eBay buyer.
    buyer keys: name, address, city, state, zip, country_code, phone
    Returns: {"success": bool, "order_id": str, "error": str}
    """
    base_payload = {
        "orderNumber": order_ref,
        "fromCountryCode": "CN",
        "shippingZip": buyer.get("zip", ""),
        "shippingCountryCode": buyer.get("country_code", "US"),
        "shippingCountry": buyer.get("country", "United States"),
        "shippingProvince": buyer.get("state", ""),
        "shippingCity": buyer.get("city", ""),
        "shippingAddress": buyer.get("address", ""),
        "shippingAddress2": "",
        "shippingCustomerName": buyer.get("name", ""),
        "shippingPhone": buyer.get("phone", "0000000000"),
        "products": [{"vid": variant_id, "quantity": 1}],
    }
    last_error = "No valid shipping method found"
    for method in _SHIPPING_METHODS:
        try:
            resp = requests.post(
                f"{BASE_URL}/shopping/order/createOrder",
                headers=_headers(),
                json={**base_payload, "logisticName": method},
                timeout=30
            )
            data = resp.json()
            if not isinstance(data, dict):
                last_error = str(data)
                time.sleep(1.1)
                continue
            if data.get("result"):
                raw = data.get("data") or {}
                order_id = raw.get("orderId", "") if isinstance(raw, dict) else str(raw)
                logger.info(f"CJ order placed with {method}, order_id={order_id}")
                # Auto-pay from CJ wallet so the order actually ships
                if order_id:
                    time.sleep(1.1)
                    pay_resp = requests.post(
                        f"{BASE_URL}/shopping/order/confirmOrder",
                        headers=_headers(),
                        json={"orderId": order_id},
                        timeout=30,
                    )
                    pay_data = pay_resp.json() if pay_resp.ok else {}
                    if isinstance(pay_data, dict) and pay_data.get("result"):
                        logger.info(f"CJ order {order_id} paid and confirmed")
                    else:
                        msg = pay_data.get("message", pay_resp.text[:100]) if isinstance(pay_data, dict) else pay_resp.text[:100]
                        logger.warning(f"CJ auto-pay failed for {order_id}: {msg} — pay manually in CJ dashboard")
                return {"success": True, "order_id": order_id, "error": ""}
            last_error = data.get("message", "unknown")
            if "logistic" in last_error.lower() or "freight" in last_error.lower():
                logger.warning(f"Shipping method {method} invalid, trying next...")
                time.sleep(1.1)
                continue
            return {"success": False, "order_id": "", "error": last_error}
        except Exception as e:
            logger.error(f"CJ order error ({method}): {e}")
            return {"success": False, "order_id": "", "error": str(e)}
    return {"success": False, "order_id": "", "error": last_error}


def get_order_tracking(cj_order_id: str) -> str:
    try:
        resp = requests.get(
            f"{BASE_URL}/shopping/order/getOrderDetail",
            headers=_headers(),
            params={"orderId": cj_order_id},
            timeout=15
        )
        data = resp.json()
        if data.get("result"):
            tracks = data.get("data", {}).get("trackingInfo", [])
            if tracks:
                return tracks[0].get("trackingNumber", "")
    except Exception as e:
        logger.debug(f"CJ tracking error: {e}")
    return ""
