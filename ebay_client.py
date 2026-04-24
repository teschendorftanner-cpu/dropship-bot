import logging
import re
import statistics
import requests
import xml.etree.ElementTree as ET
from config import EBAY_USER_TOKEN, EBAY_APP_ID, EBAY_FINDING_URL

logger = logging.getLogger(__name__)

TRADING_URL = "https://api.ebay.com/ws/api.dll"
NS = "urn:ebay:apis:eBLBaseComponents"


class DuplicateListing(Exception):
    pass


# ── Trading API helper ────────────────────────────────────────────────────────

def _call(call_name: str, body: str) -> ET.Element | None:
    headers = {
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME": call_name,
        "X-EBAY-API-SITEID": "0",
        "Content-Type": "text/xml",
    }
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<{call_name}Request xmlns="{NS}">
  <RequesterCredentials>
    <eBayAuthToken>{EBAY_USER_TOKEN}</eBayAuthToken>
  </RequesterCredentials>
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


# ── Listings ──────────────────────────────────────────────────────────────────

async def create_listing(title: str, description: str, price: float,
                         image_urls: list = None, category_id: str = "9355") -> str | None:
    pics = image_urls or []
    pic_xml = "".join(f"<PictureURL>{u}</PictureURL>" for u in pics[:12] if u)
    body = f"""
  <Item>
    <Title>{title[:80]}</Title>
    <Description><![CDATA[{description}]]></Description>
    <PrimaryCategory><CategoryID>{category_id}</CategoryID></PrimaryCategory>
    <StartPrice>{price:.2f}</StartPrice>
    <CategoryMappingAllowed>true</CategoryMappingAllowed>
    <Country>US</Country>
    <Currency>USD</Currency>
    <DispatchTimeMax>5</DispatchTimeMax>
    <ListingDuration>GTC</ListingDuration>
    <ListingType>FixedPriceItem</ListingType>
    <PictureDetails>{pic_xml}</PictureDetails>
    <PostalCode>10001</PostalCode>
    <Location>United States</Location>
    <Quantity>10</Quantity>
    <ItemSpecifics>
      <NameValueList>
        <Name>Brand</Name>
        <Value>Unbranded</Value>
      </NameValueList>
      <NameValueList>
        <Name>Color</Name>
        <Value>As Shown</Value>
      </NameValueList>
      <NameValueList>
        <Name>Type</Name>
        <Value>Other</Value>
      </NameValueList>
    </ItemSpecifics>
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
        for err in (root.findall(f".//{_ns('Errors')}") if root is not None else []):
            if "Duplicate" in (err.findtext(_ns("ShortMessage")) or ""):
                raise DuplicateListing()
        return None
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


async def mark_order_shipped(ebay_order_id: str, tracking_number: str = "", carrier: str = "Other") -> bool:
    tracking_xml = ""
    if tracking_number:
        tracking_xml = f"""
  <Shipment>
    <ShipmentTrackingDetails>
      <ShippingCarrierUsed>{carrier}</ShippingCarrierUsed>
      <ShipmentTrackingNumber>{tracking_number}</ShipmentTrackingNumber>
    </ShipmentTrackingDetails>
  </Shipment>"""
    body = f"""
  <OrderID>{ebay_order_id}</OrderID>
  <Shipped>true</Shipped>{tracking_xml}"""
    return _ack(_call("CompleteSale", body))


async def end_listing(ebay_item_id: str) -> bool:
    body = f"""
  <ItemID>{ebay_item_id}</ItemID>
  <EndingReason>NotAvailable</EndingReason>"""
    return _ack(_call("EndFixedPriceItem", body))


# ── Orders ────────────────────────────────────────────────────────────────────

def get_new_orders(days_back: int = 1) -> list[dict]:
    from datetime import datetime, timedelta
    since = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    body = f"""
  <CreateTimeFrom>{since}</CreateTimeFrom>
  <OrderStatus>Completed</OrderStatus>
  <Pagination><EntriesPerPage>100</EntriesPerPage></Pagination>"""

    root = _call("GetOrders", body)
    if not _ack(root):
        return []

    orders = []
    for order in root.findall(f".//{_ns('Order')}"):
        try:
            order_id = order.findtext(_ns("OrderID"))
            shipping = order.find(f".//{_ns('ShippingAddress')}")
            total = order.find(f".//{_ns('AmountPaid')}")
            item_id_el = order.find(f".//{_ns('ItemID')}")
            if not (order_id and shipping and total):
                continue
            orders.append({
                "order_id": order_id,
                "ebay_item_id": item_id_el.text if item_id_el is not None else "",
                "sale_price": float(total.text or 0),
                "buyer_name": shipping.findtext(_ns("Name"), ""),
                "address": shipping.findtext(_ns("Street1"), ""),
                "city": shipping.findtext(_ns("CityName"), ""),
                "state": shipping.findtext(_ns("StateOrProvince"), ""),
                "zip": shipping.findtext(_ns("PostalCode"), ""),
                "country": shipping.findtext(_ns("Country"), "US"),
            })
        except Exception as e:
            logger.error(f"Order parse error: {e}")
    return orders


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
