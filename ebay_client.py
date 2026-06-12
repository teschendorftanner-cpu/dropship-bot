import base64
import logging
import re
import statistics
import time
import requests
import xml.etree.ElementTree as ET
from config import EBAY_USER_TOKEN, EBAY_APP_ID, EBAY_FINDING_URL, EBAY_CERT_ID, EBAY_RUNAME, EBAY_REFRESH_TOKEN

logger = logging.getLogger(__name__)

TRADING_URL = "https://api.ebay.com/ws/api.dll"
NS = "urn:ebay:apis:eBLBaseComponents"

# In-memory OAuth token caches
_oauth_cache: dict = {"access_token": "", "expires_at": 0}
_app_token_cache: dict = {"access_token": "", "expires_at": 0}


class DuplicateListing(Exception):
    pass


class InvalidCategory(Exception):
    pass


class EbayListingError(Exception):
    pass


# ── OAuth 2.0 auto-refresh ────────────────────────────────────────────────────

def _get_access_token() -> str:
    """Return a valid OAuth access token, refreshing if necessary."""
    if not EBAY_REFRESH_TOKEN:
        logger.warning("EBAY_REFRESH_TOKEN not set — falling back to legacy token")
        return ""
    now = time.time()
    # Refresh 5 minutes before expiry
    if _oauth_cache["access_token"] and now < _oauth_cache["expires_at"] - 300:
        return _oauth_cache["access_token"]

    logger.info(f"Refreshing eBay OAuth token (APP_ID={EBAY_APP_ID[:12]}... CERT_ID={'set' if EBAY_CERT_ID else 'MISSING'})")
    creds = base64.b64encode(f"{EBAY_APP_ID}:{EBAY_CERT_ID}".encode()).decode()
    try:
        resp = requests.post(
            "https://api.ebay.com/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {creds}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": EBAY_REFRESH_TOKEN.strip(),
                "scope": "https://api.ebay.com/oauth/api_scope https://api.ebay.com/oauth/api_scope/sell.inventory https://api.ebay.com/oauth/api_scope/sell.fulfillment https://api.ebay.com/oauth/api_scope/sell.account",
            },
            timeout=30,
        )
        if not resp.ok:
            logger.error(f"OAuth refresh HTTP {resp.status_code}: {resp.text[:300]}")
            return ""
        try:
            data = resp.json()
        except Exception:
            logger.error(f"OAuth refresh bad response (HTTP {resp.status_code}): {resp.text[:300]}")
            return ""
        if "access_token" not in data:
            logger.error(f"OAuth refresh failed: {data}")
            return ""
        _oauth_cache["access_token"] = data["access_token"]
        _oauth_cache["expires_at"] = now + int(data.get("expires_in", 7200))
        logger.info("eBay OAuth access token refreshed successfully")
        return _oauth_cache["access_token"]
    except Exception as e:
        logger.error(f"OAuth refresh error: {e}")
        return ""


def _get_app_token() -> str:
    """Application-level OAuth token for catalog/taxonomy APIs (client_credentials flow)."""
    now = time.time()
    if _app_token_cache["access_token"] and now < _app_token_cache["expires_at"] - 300:
        return _app_token_cache["access_token"]
    creds = base64.b64encode(f"{EBAY_APP_ID}:{EBAY_CERT_ID}".encode()).decode()
    try:
        resp = requests.post(
            "https://api.ebay.com/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {creds}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
            timeout=30,
        )
        data = resp.json()
        token = data.get("access_token", "")
        if token:
            _app_token_cache["access_token"] = token
            _app_token_cache["expires_at"] = now + int(data.get("expires_in", 7200))
            logger.info("eBay app token obtained")
        else:
            logger.error(f"App token error: {data}")
        return token
    except Exception as e:
        logger.error(f"App token error: {e}")
        return ""


# ── Trading API helper ────────────────────────────────────────────────────────

def _call(call_name: str, body: str) -> ET.Element | None:
    headers = {
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME": call_name,
        "X-EBAY-API-SITEID": "0",
        "Content-Type": "text/xml",
    }

    access_token = _get_access_token()
    if access_token:
        headers["X-EBAY-API-IAF-TOKEN"] = access_token
        # Trading API still needs the RequesterCredentials block even with IAF header
        credentials_xml = "  <RequesterCredentials/>"
    else:
        credentials_xml = f"""  <RequesterCredentials>
    <eBayAuthToken>{EBAY_USER_TOKEN}</eBayAuthToken>
  </RequesterCredentials>"""

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<{call_name}Request xmlns="{NS}">
{credentials_xml}
  {body}
</{call_name}Request>"""
    try:
        resp = requests.post(TRADING_URL, data=xml.encode("utf-8"), headers=headers, timeout=30)
        resp.raise_for_status()
        return ET.fromstring(resp.content)
    except Exception as e:
        logger.error(f"Trading API error ({call_name}): {e}")
        return None


def _ns(tag):
    return f"{{{NS}}}{tag}"


def _ack(root) -> bool:
    if root is None:
        return False
    ack = root.findtext(_ns("Ack"))
    for err in root.findall(f".//{_ns('Errors')}"):
        severity = err.findtext(_ns("SeverityCode"), "")
        msg = err.findtext(_ns("ShortMessage"), "")
        if severity == "Warning":
            logger.warning(f"eBay warning: {msg}")
        elif msg:
            logger.error(f"eBay error: {msg}")
    if ack not in ("Success", "Warning"):
        logger.error(f"eBay Ack={ack!r}")
        return False
    return True


# ── Category suggestion ───────────────────────────────────────────────────────

_category_cache: dict = {}

def get_suggested_category(title: str) -> str:
    """Use eBay Taxonomy REST API to find the best leaf category for a product title."""
    if title in _category_cache:
        return _category_cache[title]
    token = _get_app_token()
    result = "112581"
    if token:
        try:
            resp = requests.get(
                "https://api.ebay.com/commerce/taxonomy/v1/category_tree/0/get_category_suggestions",
                headers={"Authorization": f"Bearer {token}"},
                params={"q": title[:350]},
                timeout=15,
            )
            if resp.ok:
                suggestions = resp.json().get("categorySuggestions", [])
                if suggestions:
                    result = str(suggestions[0]["category"]["categoryId"])
            else:
                logger.error(f"Taxonomy API HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.error(f"Taxonomy API error: {e}")
    _category_cache[title] = result
    logger.info(f"eBay category {result} for: {title[:50]}")
    return result


# ── Listings ──────────────────────────────────────────────────────────────────

async def create_listing(title: str, description: str, price: float,
                         image_urls: list = None, category_id: str = "9355",
                         variant_id: str = "",
                         item_specifics: list = None) -> str | None:
    def _valid_pic(url: str) -> bool:
        u = (url or "").strip()
        return bool(u) and u.startswith("https://") and " " not in u and len(u) < 2000

    pics = [u.strip() for u in (image_urls or []) if _valid_pic(u)]
    pic_xml = "".join(f"<PictureURL>{u}</PictureURL>" for u in pics[:12])
    sku_xml = f"<SKU>{variant_id}</SKU>" if variant_id else ""

    specs = item_specifics or [
        ("Brand", "Unbranded"),
        ("Color", "As Shown"),
        ("Condition", "New"),
        ("MPN", "Does Not Apply"),
    ]
    specs_xml = "".join(
        f"<NameValueList><Name>{n}</Name><Value>{v}</Value></NameValueList>"
        for n, v in specs
    )

    body = f"""
  <Item>
    <Title>{title[:80]}</Title>
    {sku_xml}
    <Description><![CDATA[{description}]]></Description>
    <PrimaryCategory><CategoryID>{category_id}</CategoryID></PrimaryCategory>
    <StartPrice>{price:.2f}</StartPrice>
    <CategoryMappingAllowed>true</CategoryMappingAllowed>
    <Country>US</Country>
    <Currency>USD</Currency>
    <DispatchTimeMax>3</DispatchTimeMax>
    <ListingDuration>GTC</ListingDuration>
    <ListingType>FixedPriceItem</ListingType>
    <PictureDetails>{pic_xml}</PictureDetails>
    <PostalCode>10001</PostalCode>
    <Location>United States</Location>
    <Quantity>10</Quantity>
    <ItemSpecifics>{specs_xml}</ItemSpecifics>
    <ReturnPolicy>
      <ReturnsAcceptedOption>ReturnsAccepted</ReturnsAcceptedOption>
      <RefundOption>MoneyBack</RefundOption>
      <ReturnsWithinOption>Days_30</ReturnsWithinOption>
      <ShippingCostPaidByOption>Buyer</ShippingCostPaidByOption>
    </ReturnPolicy>
    <ShippingDetails>
      <ShippingType>Flat</ShippingType>
      <ShippingServiceOptions>
        <ShippingServicePriority>1</ShippingServicePriority>
        <ShippingService>USPSFirstClass</ShippingService>
        <ShippingServiceCost>0.00</ShippingServiceCost>
        <ShippingServiceAdditionalCost>0.00</ShippingServiceAdditionalCost>
        <FreeShipping>true</FreeShipping>
      </ShippingServiceOptions>
    </ShippingDetails>
    <Site>US</Site>
    <ConditionID>1000</ConditionID>
  </Item>"""

    root = _call("AddFixedPriceItem", body)
    if not _ack(root):
        errors = root.findall(f".//{_ns('Errors')}") if root is not None else []
        for err in errors:
            msg = err.findtext(_ns("ShortMessage")) or ""
            if "Duplicate" in msg:
                raise DuplicateListing()
            if "category" in msg.lower():
                raise InvalidCategory(msg)
        if errors:
            short = errors[0].findtext(_ns("ShortMessage")) or ""
            long_ = errors[0].findtext(_ns("LongMessage")) or ""
            code  = errors[0].findtext(_ns("ErrorCode")) or ""
            detail = f"[{code}] {short} — {long_}" if long_ else f"[{code}] {short}"
        elif root is None:
            detail = "No response from eBay — check token/credentials"
        else:
            detail = "Unknown eBay error (no error elements in response)"
        raise EbayListingError(detail)
    item_id = root.findtext(_ns("ItemID"))
    item_id = root.findtext(_ns("ItemID"))
    if item_id:
        logger.info(f"Created eBay listing {item_id}")
    return item_id


async def revise_price(ebay_item_id: str, new_price: float) -> bool:
    body = f"""
  <Item>
    <ItemID>{ebay_item_id}</ItemID>
    <StartPrice>{new_price:.2f}</StartPrice>
  </Item>"""
    return _ack(_call("ReviseFixedPriceItem", body))


async def revise_category(ebay_item_id: str, category_id: str) -> bool:
    body = f"""
  <Item>
    <ItemID>{ebay_item_id}</ItemID>
    <PrimaryCategory><CategoryID>{category_id}</CategoryID></PrimaryCategory>
    <CategoryMappingAllowed>true</CategoryMappingAllowed>
  </Item>"""
    return _ack(_call("ReviseFixedPriceItem", body))


async def mark_order_shipped(ebay_order_id: str, tracking_number: str = "", carrier: str = "USPS") -> bool:
    token = _get_access_token()
    if not token:
        return False
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        # Fetch the order to get real line item IDs
        order_resp = requests.get(
            f"https://api.ebay.com/sell/fulfillment/v1/order/{ebay_order_id}",
            headers=headers, timeout=30,
        )
        if not order_resp.ok:
            logger.error(f"Could not fetch order {ebay_order_id}: {order_resp.status_code} {order_resp.text[:200]}")
            return False
        line_items = order_resp.json().get("lineItems", [])
        if not line_items:
            logger.error(f"No line items in order {ebay_order_id}")
            return False
        payload = {
            "lineItems": [{"lineItemId": item["lineItemId"], "quantity": item.get("quantity", 1)}
                          for item in line_items],
            "shippingCarrierCode": carrier,
            "trackingNumber": tracking_number or "0000000000",
        }
        resp = requests.post(
            f"https://api.ebay.com/sell/fulfillment/v1/order/{ebay_order_id}/shipping_fulfillment",
            headers=headers, json=payload, timeout=30,
        )
        if resp.status_code in (200, 201, 204):
            logger.info(f"Marked eBay order {ebay_order_id} as shipped")
            return True
        logger.error(f"mark_order_shipped HTTP {resp.status_code}: {resp.text[:300]}")
        return False
    except Exception as e:
        logger.error(f"mark_order_shipped error: {e}")
        return False


async def end_listing(ebay_item_id: str) -> bool:
    body = f"""
  <ItemID>{ebay_item_id}</ItemID>
  <EndingReason>NotAvailable</EndingReason>"""
    return _ack(_call("EndFixedPriceItem", body))


# ── Active listing sync ───────────────────────────────────────────────────────

def get_active_ebay_listings() -> list[dict]:
    """Pull all currently active eBay listings so the DB can be restored after a reset."""
    body = """
  <ActiveList>
    <Include>true</Include>
    <Pagination><EntriesPerPage>200</EntriesPerPage></Pagination>
  </ActiveList>"""
    root = _call("GetMyeBaySelling", body)
    if not _ack(root):
        return []
    items = []
    for item in root.findall(f".//{_ns('Item')}"):
        item_id = item.findtext(_ns("ItemID"))
        if not item_id:
            continue
        price_el = item.find(f".//{_ns('CurrentPrice')}")
        items.append({
            "ebay_item_id": item_id,
            "title": item.findtext(_ns("Title")) or "",
            "sku": item.findtext(_ns("SKU")) or "",  # CJ variant ID
            "ebay_price": float(price_el.text) if price_el is not None else 0.0,
        })
    return items


# ── Orders ────────────────────────────────────────────────────────────────────

def get_new_orders(days_back: int = 3) -> list[dict]:
    from datetime import datetime, timedelta
    token = _get_access_token()
    if not token:
        logger.error("No OAuth token for get_new_orders")
        return []
    since = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    try:
        resp = requests.get(
            "https://api.ebay.com/sell/fulfillment/v1/order",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            params={"filter": f"creationdate:[{since}..],orderfulfillmentstatus:{{NOT_STARTED|IN_PROGRESS}}", "limit": 50},
            timeout=30,
        )
        if not resp.ok:
            logger.error(f"Fulfillment API HTTP {resp.status_code}: {resp.text[:300]}")
            return []
        orders = []
        for order in resp.json().get("orders", []):
            try:
                order_id = order["orderId"]
                line_items = order.get("lineItems", [])
                if not line_items:
                    continue
                ebay_item_id = str(line_items[0].get("legacyItemId", ""))
                total = float(order.get("pricingSummary", {}).get("total", {}).get("value", 0))
                ship_to = {}
                for inst in order.get("fulfillmentStartInstructions", []):
                    if inst.get("fulfillmentInstructionsType") == "SHIP_TO":
                        ship_to = inst.get("shippingStep", {}).get("shipTo", {})
                        break
                addr = ship_to.get("contactAddress", {})
                orders.append({
                    "order_id": order_id,
                    "ebay_item_id": ebay_item_id,
                    "sale_price": total,
                    "buyer_name": ship_to.get("fullName", ""),
                    "address": addr.get("addressLine1", ""),
                    "city": addr.get("city", ""),
                    "state": addr.get("stateOrProvince", ""),
                    "zip": addr.get("postalCode", ""),
                    "country": addr.get("countryCode", "US"),
                })
            except Exception as e:
                logger.error(f"Order parse error: {e}")
        return orders
    except Exception as e:
        logger.error(f"Fulfillment API error: {e}")
        return []


# ── Research (eBay Finding API) ───────────────────────────────────────────────

def get_sold_median(keyword: str) -> float | None:
    params = {
        "OPERATION-NAME": "findCompletedItems",
        "SERVICE-VERSION": "1.0.0",
        "SECURITY-APPNAME": EBAY_APP_ID,
        "RESPONSE-DATA-FORMAT": "JSON",
        "REST-PAYLOAD": "",
        "keywords": keyword,
        "itemFilter(0).name": "SoldItemsOnly",
        "itemFilter(0).value": "true",
        "paginationInput.entriesPerPage": "100",
    }
    try:
        resp = requests.get(EBAY_FINDING_URL, params=params, timeout=20)
        data = resp.json()
        if "errorMessage" in data:
            logger.error(f"eBay API error for '{keyword}': {data['errorMessage'][0]['error'][0]['message'][0]}")
            return None
        items = data["findCompletedItemsResponse"][0]["searchResult"][0].get("item", [])
        prices = [float(i["sellingStatus"][0]["convertedCurrentPrice"][0]["__value__"])
                  for i in items
                  if float(i["sellingStatus"][0]["convertedCurrentPrice"][0]["__value__"]) > 0]
        if len(prices) < 5:
            return None
        return statistics.median(prices)
    except Exception as e:
        logger.error(f"eBay sold price error for '{keyword}': {e}")
        return None
