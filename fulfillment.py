import logging
from cj_client import place_order, get_order_tracking

logger = logging.getLogger(__name__)


async def fulfill_order(variant_id: str, buyer: dict, order_ref: str) -> dict:
    """
    Place a CJ Dropshipping order to ship directly to the eBay buyer.
    Returns: {"success": bool, "order_id": str, "tracking": str, "error": str}
    """
    logger.info(f"Placing CJ order for {buyer.get('name')} (ref: {order_ref})")
    result = place_order(order_ref, variant_id, buyer)

    if result["success"] and result["order_id"]:
        tracking = get_order_tracking(result["order_id"])
        result["tracking"] = tracking

    return result
