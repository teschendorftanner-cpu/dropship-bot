import logging
from database import (
    get_active_listings, save_order, get_order_by_ebay_id,
    log_profit, decrement_inventory,
)
from ebay_client import get_new_orders
from config import EBAY_FEE_PERCENT

logger = logging.getLogger(__name__)


def poll_new_orders(days_back: int = 3) -> list[dict]:
    """Check eBay for new orders. For each genuinely new sale, decrements inventory
    and logs profit immediately, then returns it so the bot can prompt for shipping.
    Already-known orders (still pending shipment) are skipped — they're not "new"."""
    active_listings = {l["ebay_item_id"]: l for l in get_active_listings()}
    raw_orders = get_new_orders(days_back=days_back)
    logger.info(f"[Orders] eBay returned {len(raw_orders)} order(s), {len(active_listings)} active listing(s) in DB")

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
            logger.warning(f"[Orders] Order {o['order_id']} item {o['ebay_item_id']} not in DB")
            continue

        already = get_order_by_ebay_id(o["order_id"])
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
            cost_price=listing["cost_price"],
        )
        if already is not None:
            continue  # already seen this order on a previous poll — don't re-process

        decrement_inventory(listing["product_id"])
        ebay_fee = o["sale_price"] * (EBAY_FEE_PERCENT / 100)
        net_profit = o["sale_price"] - ebay_fee - listing["cost_price"]
        log_profit(order_db_id, o["sale_price"], listing["cost_price"], ebay_fee, net_profit)

        new_orders.append({
            "ebay_order_id": o["order_id"],
            "buyer_name": o["buyer_name"],
            "sale_price": o["sale_price"],
            "net_profit": round(net_profit, 2),
            "address": o["address"],
            "city": o["city"],
            "state": o["state"],
            "zip": o["zip"],
            "country": o["country"],
            "title": listing["title"],
        })

    return new_orders
