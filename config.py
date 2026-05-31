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

# CJ Dropshipping (free at app.cjdropshipping.com)
CJ_EMAIL = os.getenv("CJ_EMAIL", "")
CJ_PASSWORD = os.getenv("CJ_PASSWORD", "")
CJ_API_KEY = os.getenv("CJ_API_KEY", "")  # optional — from CJ dashboard → API page

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Profit settings
MIN_MARGIN_PERCENT = float(os.getenv("MIN_MARGIN_PERCENT", "25"))
MARKUP_PERCENT = float(os.getenv("MARKUP_PERCENT", "60"))
EBAY_FEE_PERCENT = float(os.getenv("EBAY_FEE_PERCENT", "15.5"))
MAX_LISTINGS = int(os.getenv("MAX_LISTINGS", "50"))  # stay under eBay's 250 free insertions/month
RESEARCH_INTERVAL_HOURS = float(os.getenv("RESEARCH_INTERVAL_HOURS", "6"))
PRICE_SYNC_INTERVAL_HOURS = float(os.getenv("PRICE_SYNC_INTERVAL_HOURS", "2"))
ORDER_POLL_MINUTES = int(os.getenv("ORDER_POLL_MINUTES", "60"))
