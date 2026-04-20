import logging
from config import EBAY_FEE_PERCENT, MIN_MARGIN_PERCENT, MARKUP_PERCENT
from database import get_active_listings, update_listing_price, deactivate_listing
from cj_client import get_product
from ebay_client import revise_price, end_listing

logger = logging.getLogger(__name__)


def sync_prices() -> dict:
    listings = get_active_listings()
    if not listings:
        return {"checked": 0, "updated": 0, "ended": 0}

    updated = 0
    ended = 0

    for listing in listings:
        try:
            product_id = listing.get("walmart_item_id", "")
            if not product_id:
                continue

            cj_product = get_product(product_id)
            if not cj_product:
                continue

            variants = cj_product.get("variants", [{}])
            new_cost = float(variants[0].get("variantSellPrice") or cj_product.get("sellPrice") or 0)
            if new_cost <= 0:
                continue

            old_cost = listing["walmart_price"]
            if abs(new_cost - old_cost) < 0.01:
                continue

            new_ebay_price = round(new_cost * (1 + MARKUP_PERCENT / 100), 2)
            revenue = new_ebay_price * (1 - EBAY_FEE_PERCENT / 100)
            margin = ((revenue - new_cost) / new_ebay_price) * 100

            if margin < MIN_MARGIN_PERCENT:
                logger.info(f"  Ending {listing['ebay_item_id']} (margin dropped to {margin:.1f}%)")
                end_listing(listing["ebay_item_id"])
                deactivate_listing(listing["ebay_item_id"])
                ended += 1
            else:
                logger.info(f"  Updating {listing['ebay_item_id']}: cost ${old_cost:.2f}→${new_cost:.2f}, eBay→${new_ebay_price:.2f}")
                if revise_price(listing["ebay_item_id"], new_ebay_price):
                    update_listing_price(listing["ebay_item_id"], new_ebay_price, new_cost)
                    updated += 1

        except Exception as e:
            logger.error(f"  Price sync error for {listing.get('ebay_item_id', '?')}: {e}")

    return {"checked": len(listings), "updated": updated, "ended": ended}
