import asyncio
import logging
import sys
import telegram.error
from config import TELEGRAM_BOT_TOKEN
from bot import create_app, research_loop, order_loop, price_loop, send

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("dropship.log"),
    ]
)
logger = logging.getLogger(__name__)


def check_config():
    from config import EBAY_APP_ID, EBAY_CERT_ID, EBAY_REFRESH_TOKEN, CJ_API_KEY
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not EBAY_APP_ID:
        missing.append("EBAY_APP_ID")
    if not EBAY_CERT_ID:
        missing.append("EBAY_CERT_ID")
    if not EBAY_REFRESH_TOKEN:
        missing.append("EBAY_REFRESH_TOKEN")
    if not CJ_API_KEY:
        missing.append("CJ_API_KEY")
    if missing:
        logger.error(f"Missing required config: {', '.join(missing)}")
        logger.error("Set these in your .env file (see .env.example).")
        sys.exit(1)


async def startup_task(app):
    """On every start, restore active eBay listings and catch any pending orders."""
    await asyncio.sleep(8)  # let Telegram polling settle first
    try:
        from ebay_client import get_active_ebay_listings
        from database import sync_ebay_listing
        from bot import remove_duplicate_listings

        # Step 1: sync active eBay listings back into DB so orders can be fulfilled
        ebay_listings = get_active_ebay_listings()
        restored = sum(
            sync_ebay_listing(l["ebay_item_id"], l["title"],
                              l["ebay_price"], l["sku"])
            for l in ebay_listings
        )
        if restored:
            logger.info(f"[Startup] Restored {restored} listing(s) from eBay")

        # Step 2: remove any duplicates that built up
        dedupe = await remove_duplicate_listings()
        if dedupe["removed"]:
            logger.info(f"[Startup] Removed {dedupe['removed']} duplicate listing(s)")

        # Step 3: catch any orders that came in since last restart
        from order_processor import poll_new_orders, process_pending_orders
        new_orders = poll_new_orders()
        if new_orders:
            await send(app, f"📬 *{len(new_orders)} order(s) found on startup!* Fulfilling now...")
        order_results = await process_pending_orders()
        for r in order_results:
            if r["success"]:
                await send(app, f"✅ *Order fulfilled!*\nBuyer: {r['buyer_name']}\nProfit: *${r['net_profit']:.2f}*")
            else:
                from bot import _esc
                msg = f"❌ *Manual fulfillment needed*\nOrder: `{r['ebay_order_id']}`\nError: {r['error'][:80]}"
                if r.get("buyer_address"):
                    msg += f"\n\n📦 Ship to:\n{_esc(r['buyer_name'])}\n{_esc(r['buyer_address'])}"
                if r.get("title"):
                    msg += f"\n🛍 Item: {_esc(r['title'][:60])}"
                await send(app, msg)

        await send(app, "🚀 Bot started. Use /research then /list to add new products.")
    except Exception as e:
        logger.error(f"[Startup] Error: {e}")


async def main():
    check_config()
    app = create_app()

    async with app:
        await app.start()
        try:
            await app.updater.start_polling(drop_pending_updates=True)
        except telegram.error.Conflict:
            logger.warning("Another bot instance is running — exiting so Railway restarts cleanly")
            sys.exit(1)
        logger.info("✅ Dropship bot running. Open Telegram and send /start")

        await asyncio.gather(
            startup_task(app),
            order_loop(app),
            price_loop(app),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped.")
