"""Run this to diagnose why research finds nothing."""
import os
from dotenv import load_dotenv
load_dotenv()

print("=" * 50)
print("STEP 1: Checking config...")
from config import EBAY_APP_ID, CJ_EMAIL, CJ_PASSWORD
print(f"  EBAY_APP_ID set: {'YES' if EBAY_APP_ID else 'NO ❌'}")
print(f"  CJ_EMAIL set:    {'YES' if CJ_EMAIL else 'NO ❌'}")
print(f"  CJ_PASSWORD set: {'YES' if CJ_PASSWORD else 'NO ❌'}")

print("\nSTEP 2: Testing CJ Dropshipping login...")
from cj_client import _get_token, search_products
token = _get_token()
print(f"  CJ token: {'OK ✅' if token else 'FAILED ❌'}")

if token:
    print("\nSTEP 3: Searching CJ for 'wireless earbuds'...")
    products = search_products("wireless earbuds", page_size=5)
    print(f"  Found {len(products)} products")
    for p in products[:3]:
        print(f"  - {p['title'][:50]} @ ${p['price']:.2f}")

print("\nSTEP 4: Testing eBay Finding API...")
import requests
from config import EBAY_APP_ID, EBAY_FINDING_URL
params = {
    "OPERATION-NAME": "findCompletedItems",
    "SERVICE-VERSION": "1.0.0",
    "SECURITY-APPNAME": EBAY_APP_ID,
    "RESPONSE-DATA-FORMAT": "JSON",
    "REST-PAYLOAD": "",
    "keywords": "wireless earbuds",
    "itemFilter(0).name": "SoldItemsOnly",
    "itemFilter(0).value": "true",
    "paginationInput.entriesPerPage": "5",
}
resp = requests.get(EBAY_FINDING_URL, params=params, timeout=15)
data = resp.json()
print(f"  Raw response keys: {list(data.keys())}")
print(f"  Full response: {data}")

print("\nDone.")
