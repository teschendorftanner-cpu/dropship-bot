from dotenv import load_dotenv
load_dotenv()

print("Testing CJ search...")
from cj_client import search_products, get_shipping_cost
products = search_products("wireless earbuds", page_size=5)
print(f"  CJ results: {len(products)}")
for p in products[:3]:
    print(f"  - {p['title'][:50]} @ ${p['price']:.2f}")

print("\nTesting eBay API...")
from ebay_client import get_sold_median
median = get_sold_median("wireless earbuds")
print(f"  eBay median: {median}")

print("\nTesting margin calc...")
if products and median:
    from research import calculate_margin
    p = products[0]
    shipping = get_shipping_cost(p["product_id"])
    cost = p["price"] + shipping
    from config import MARKUP_PERCENT
    ebay_price = round(cost * (1 + MARKUP_PERCENT / 100), 2)
    margin = calculate_margin(cost, ebay_price)
    print(f"  Cost: ${cost:.2f}, eBay price: ${ebay_price:.2f}, Margin: {margin:.1f}%")
