import asyncio
import logging
import re
from config import EBAY_FEE_PERCENT, MIN_MARGIN_PERCENT, MARKUP_PERCENT
from database import get_active_listings, update_listing_price, deactivate_listing
from cj_client import get_product, get_shipping_cost
from ebay_client import revise_price, end_listing

logger = logging.getLogger(__name__)


async def sync_prices() -> dict:
    listings = get_active_listings()
    if not listings:
        return {"checked": 0, "updated": 0, "ended": 0}

    updated = 0
    ended = 0

    for listing in listings:
        try:
            # Extract the CJ product ID (pid) from the stored URL.
            # The variant ID (vid) stored in cj_variant_id is NOT the same as pid
            # and will return nothing from get_product().
            cj_url = listing.get("cj_url", "")
            pid_match = re.search(r'-p-([A-Za-z0-9]+)\.html', cj_url)
            if not pid_match:
                continue
            product_id = pid_match.group(1)

            cj_product = get_product(product_id)
            if not cj_product:
                continue

            variants = cj_product.get("variants", [{}])
            new_product_cost = float(
                variants[0].get("variantSellPrice") or cj_product.get("sellPrice") or 0
            )
            if new_product_cost <= 0:
                continue

            # Always include shipping so the new price covers fulfilment cost
            shipping = get_shipping_cost(product_id)
            new_cost = round(new_product_cost + shipping, 2)

            old_cost = listing["cj_price"]
            if abs(new_cost - old_cost) < 0.10:
                continue  # ignore noise changes under $0.10

            new_ebay_price = round(new_cost * (1 + MARKUP_PERCENT / 100), 2)
            revenue = new_ebay_price * (1 - EBAY_FEE_PERCENT / 100)
            margin = ((revenue - new_cost) / new_ebay_price) * 100

            if margin < MIN_MARGIN_PERCENT:
                logger.info(f"  Ending {listing['ebay_item_id']} — margin fell to {margin:.1f}%")
                await end_listing(listing["ebay_item_id"])
                deactivate_listing(listing["ebay_item_id"])
                ended += 1
            else:
                logger.info(
                    f"  Updating {listing['ebay_item_id']}: "
                    f"cost ${old_cost:.2f}→${new_cost:.2f}, eBay→${new_ebay_price:.2f}"
                )
                if await revise_price(listing["ebay_item_id"], new_ebay_price):
                    update_listing_price(listing["ebay_item_id"], new_ebay_price, new_cost)
                    updated += 1

        except Exception as e:
            logger.error(f"  Price sync error for {listing.get('ebay_item_id', '?')}: {e}")

        await asyncio.sleep(0.3)  # stay well under eBay rate limits

    return {"checked": len(listings), "updated": updated, "ended": ended}
