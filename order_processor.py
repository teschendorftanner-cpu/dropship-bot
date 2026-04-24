import asyncio
import logging
from database import (
    get_active_listings, save_order, get_pending_orders,
    mark_order_fulfilled, mark_order_failed, log_profit
)
from ebay_client import get_new_orders, mark_order_shipped
from fulfillment import fulfill_order
from config import EBAY_FEE_PERCENT

logger = logging.getLogger(__name__)


def poll_new_orders() -> list[dict]:
    active_listings = {l["ebay_item_id"]: l for l in get_active_listings()}
    raw_orders = get_new_orders(days_back=1)
    new_orders = []

    for o in raw_orders:
        listing = active_listings.get(o["ebay_item_id"])
        if not listing:
            continue

        order_db_id = save_order(
            ebay_order_id=o["order_id"],
            listing_id=listing["id"],
            buyer_name=o["buyer_name"],
            address=o["address"],
            city=o["city"],
            state=o["state"],
            zip_code=o["zip"],
            country=o["country"],
            sale_price=o["sale_price"],
            walmart_price=listing["walmart_price"],
        )
        if order_db_id:
            new_orders.append({
                "order_db_id": order_db_id,
                "ebay_order_id": o["order_id"],
                "buyer_name": o["buyer_name"],
                "sale_price": o["sale_price"],
                "walmart_price": listing["walmart_price"],
                "variant_id": listing["walmart_item_id"],
                "address": o["address"],
                "city": o["city"],
                "state": o["state"],
                "zip": o["zip"],
                "country": o["country"],
                "title": listing["title"],
            })

    return new_orders


async def process_pending_orders() -> list[dict]:
    pending = get_pending_orders()
    if not pending:
        return []

    active_listings = {l["id"]: l for l in get_active_listings()}
    results = []

    for order in pending:
        listing = active_listings.get(order.get("listing_id"))
        if not listing:
            logger.warning(f"No listing found for order {order['ebay_order_id']}")
            continue

        buyer = {
            "name": order["buyer_name"],
            "address": order["buyer_address"],
            "city": order["buyer_city"],
            "state": order["buyer_state"],
            "zip": order["buyer_zip"],
            "country": order.get("buyer_country", "United States"),
            "country_code": "US",
            "phone": "0000000000",
        }

        result = await fulfill_order(
            variant_id=listing["walmart_item_id"],
            buyer=buyer,
            order_ref=order["ebay_order_id"],
        )

        if result["success"]:
            tracking = result.get("tracking", "")
            mark_order_fulfilled(order["id"], result["order_id"], tracking)
            ebay_fee = order["sale_price"] * (EBAY_FEE_PERCENT / 100)
            net_profit = order["sale_price"] - ebay_fee - order["walmart_price"]
            log_profit(order["id"], order["sale_price"], order["walmart_price"], ebay_fee, net_profit)

            shipped = await mark_order_shipped(order["ebay_order_id"], tracking_number=tracking)
            if shipped:
                logger.info(f"✅ Marked eBay order {order['ebay_order_id']} as shipped")
            else:
                logger.warning(f"⚠️ Could not mark eBay order {order['ebay_order_id']} as shipped — do it manually")

            results.append({
                "success": True,
                "ebay_order_id": order["ebay_order_id"],
                "buyer_name": buyer["name"],
                "net_profit": round(net_profit, 2),
                "cj_order_id": result["order_id"],
            })
            logger.info(f"✅ Fulfilled via CJ! Profit: ${net_profit:.2f}")
        else:
            mark_order_failed(order["id"])
            results.append({
                "success": False,
                "ebay_order_id": order["ebay_order_id"],
                "error": result.get("error", "unknown"),
            })
            logger.error(f"❌ CJ fulfillment failed: {result.get('error')}")

    return results
