import asyncio
import logging
from database import (
    get_active_listings, save_order, get_pending_orders,
    mark_order_fulfilled, mark_order_failed, log_profit, update_product_variant
)
from ebay_client import get_new_orders, mark_order_shipped
from fulfillment import fulfill_order
from config import EBAY_FEE_PERCENT

logger = logging.getLogger(__name__)


def poll_new_orders(days_back: int = 3) -> list[dict]:
    active_listings = {l["ebay_item_id"]: l for l in get_active_listings()}
    raw_orders = get_new_orders(days_back=days_back)
    logger.info(f"[Orders] eBay returned {len(raw_orders)} order(s), {len(active_listings)} active listing(s) in DB")

    # If DB is empty but orders exist, sync listings from eBay first
    if not active_listings and raw_orders:
        logger.info("[Orders] DB empty — syncing listings from eBay before matching...")
        from ebay_client import get_active_ebay_listings
        from database import sync_ebay_listing
        for l in get_active_ebay_listings():
            sync_ebay_listing(l["ebay_item_id"], l["title"], l["ebay_price"], l["sku"])
        active_listings = {l["ebay_item_id"]: l for l in get_active_listings()}
        logger.info(f"[Orders] After sync: {len(active_listings)} listing(s) in DB")

    new_orders = []

    for o in raw_orders:
        listing = active_listings.get(o["ebay_item_id"])
        if not listing:
            logger.warning(f"[Orders] Order {o['order_id']} item {o['ebay_item_id']} not in DB — cannot auto-fulfill")
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
            cj_price=listing["cj_price"],
        )
        if order_db_id:
            new_orders.append({
                "order_db_id": order_db_id,
                "ebay_order_id": o["order_id"],
                "buyer_name": o["buyer_name"],
                "sale_price": o["sale_price"],
                "cj_price": listing["cj_price"],
                "variant_id": listing["cj_variant_id"],
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

        variant_id = listing.get("cj_variant_id", "")
        if not variant_id:
            from cj_client import search_products
            title = listing.get("title", "")
            # Strip filler/repeated words, use first 4 unique meaningful words
            seen, unique = set(), []
            for w in title.split():
                w_low = w.lower().strip(",'.-")
                if w_low and w_low not in seen and len(w_low) > 2:
                    seen.add(w_low)
                    unique.append(w)
                if len(unique) == 4:
                    break
            keywords = " ".join(unique)
            logger.warning(f"No variant ID for {order['ebay_order_id']} — searching CJ with: {keywords}")
            cj_results = search_products(keywords, page_size=5)
            if cj_results and cj_results[0].get("variant_id"):
                variant_id = cj_results[0]["variant_id"]
                logger.info(f"Found CJ variant {variant_id} via title search — proceeding with fulfillment")
                # Persist so future orders for this listing don't repeat the search
                update_product_variant(listing["product_id"], variant_id)
            else:
                logger.error(f"CJ search found nothing for '{keywords}' — order needs manual fulfillment")
                mark_order_failed(order["id"], f"CJ search found nothing for '{keywords}'")
                results.append({
                    "success": False,
                    "ebay_order_id": order["ebay_order_id"],
                    "error": "Could not find product on CJ — fulfill manually",
                    "buyer_name": order["buyer_name"],
                    "buyer_address": f"{order['buyer_address']}, {order['buyer_city']}, {order['buyer_state']} {order['buyer_zip']}",
                    "title": title,
                })
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
            variant_id=variant_id,
            buyer=buyer,
            order_ref=order["ebay_order_id"],
        )

        if result["success"]:
            tracking = result.get("tracking", "")
            mark_order_fulfilled(order["id"], result["order_id"], tracking)
            ebay_fee = order["sale_price"] * (EBAY_FEE_PERCENT / 100)
            net_profit = order["sale_price"] - ebay_fee - order["cj_price"]
            log_profit(order["id"], order["sale_price"], order["cj_price"], ebay_fee, net_profit)

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
            error_msg = result.get("error", "unknown")
            mark_order_failed(order["id"], error_msg)
            results.append({
                "success": False,
                "ebay_order_id": order["ebay_order_id"],
                "error": error_msg,
            })
            logger.error(f"❌ CJ fulfillment failed: {error_msg}")

    return results
