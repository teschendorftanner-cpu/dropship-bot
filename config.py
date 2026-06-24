import os
from dotenv import load_dotenv

load_dotenv()

# eBay account
EBAY_EMAIL = os.getenv("EBAY_EMAIL", "")
EBAY_PASSWORD = os.getenv("EBAY_PASSWORD", "")
EBAY_APP_ID = os.getenv("EBAY_APP_ID", "")
EBAY_USER_TOKEN = os.getenv("EBAY_USER_TOKEN", "")

# eBay OAuth 2.0 (preferred — tokens auto-refresh, never expire manually)
EBAY_CERT_ID = os.getenv("EBAY_CERT_ID", "")
EBAY_RUNAME = os.getenv("EBAY_RUNAME", "")
EBAY_REFRESH_TOKEN = os.getenv("EBAY_REFRESH_TOKEN", "")

EBAY_FINDING_URL = "https://svcs.ebay.com/services/search/FindingService/v1"

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Profit settings (retail arbitrage — you buy clearance stock, eBay comps set the resale price)
MIN_MARGIN_PERCENT = float(os.getenv("MIN_MARGIN_PERCENT", "25"))
EBAY_FEE_PERCENT = float(os.getenv("EBAY_FEE_PERCENT", "15.5"))
SHIP_COST_ESTIMATE = float(os.getenv("SHIP_COST_ESTIMATE", "6"))  # your own outbound shipping, added to cost
MAX_LISTINGS = int(os.getenv("MAX_LISTINGS", "50"))  # stay under eBay's 250 free insertions/month
ORDER_POLL_MINUTES = int(os.getenv("ORDER_POLL_MINUTES", "60"))
