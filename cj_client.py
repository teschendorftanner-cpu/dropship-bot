import logging
import requests
from config import CJ_EMAIL, CJ_PASSWORD, CJ_API_KEY

logger = logging.getLogger(__name__)

BASE_URL = "https://developers.cjdropshipping.com/api2.0/v1"
_token = None


def _get_token() -> str | None:
    global _token
    if _token:
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
    """Return up to 10 image URLs for a product (main + extras)."""
    data = get_product(product_id)
    if not data:
        return []
    images = []
    main = data.get("productImage", "")
    if main:
        images.append(main)
    image_set = data.get("productImageSet", "")
    if isinstance(image_set, str):
        for url in image_set.split(","):
            url = url.strip()
            if url and url not in images:
                images.append(url)
    elif isinstance(image_set, list):
        for url in image_set:
            if url and url not in images:
                images.append(url)
    return images[:10]


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
    return 5.0  # default shipping estimate


def place_order(order_ref: str, variant_id: str, buyer: dict) -> dict:
    """
    Place a CJ dropshipping order to ship directly to the eBay buyer.
    buyer keys: name, address, city, state, zip, country_code, phone
    Returns: {"success": bool, "order_id": str, "error": str}
    """
    payload = {
        "orderNumber": order_ref,
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
        "logisticName": "CJPacket",
    }
    try:
        resp = requests.post(
            f"{BASE_URL}/shopping/order/createOrder",
            headers=_headers(),
            json=payload,
            timeout=30
        )
        data = resp.json()
        if data.get("result"):
            order_id = data.get("data", {}).get("orderId", "")
            return {"success": True, "order_id": order_id, "error": ""}
        return {"success": False, "order_id": "", "error": data.get("message", "unknown")}
    except Exception as e:
        logger.error(f"CJ order error: {e}")
        return {"success": False, "order_id": "", "error": str(e)}


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
