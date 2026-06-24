import logging
from config import EBAY_FEE_PERCENT, MIN_MARGIN_PERCENT, SHIP_COST_ESTIMATE
from ebay_client import get_sold_median

logger = logging.getLogger(__name__)


def calculate_margin(cost: float, sell_price: float) -> float:
    revenue = sell_price * (1 - EBAY_FEE_PERCENT / 100)
    return round(((revenue - cost) / sell_price) * 100, 2)


def check_item(name: str, buy_price: float) -> dict:
    """Look up eBay sold comps for a clearance item and judge whether it's worth buying."""
    median = get_sold_median(name)
    cost = round(buy_price + SHIP_COST_ESTIMATE, 2)
    if median is None:
        return {"median": None, "cost": cost, "verdict": "UNKNOWN"}

    sell_price = round(median * 0.95, 2)
    margin = calculate_margin(cost, sell_price)
    verdict = "BUY" if margin >= MIN_MARGIN_PERCENT else "PASS"
    return {
        "median": median,
        "sell_price": sell_price,
        "cost": cost,
        "margin": margin,
        "verdict": verdict,
    }
