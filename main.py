import asyncio
import logging
import sys
import telegram.error
from config import TELEGRAM_BOT_TOKEN, EBAY_EMAIL, CJ_EMAIL
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
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not EBAY_EMAIL:
        missing.append("EBAY_EMAIL")
    if not CJ_EMAIL:
        missing.append("CJ_EMAIL")
    if missing:
        logger.error(f"Missing required config: {', '.join(missing)}")
        logger.error("Copy .env.example to .env and fill in your credentials.")
        sys.exit(1)


async def startup_task(app):
    """On every start, repopulate the DB so a fresh deploy never leaves the bot empty."""
    await asyncio.sleep(8)  # let Telegram polling settle first
    logger.info("[Startup] Auto-research starting...")
    try:
        from research import research_products
        from lister import list_ready_products
        found = research_products()
        if found:
            listed = await list_ready_products(limit=len(found))
            if listed:
                await send(app, f"🚀 Bot started — auto-listed *{len(listed)}* new products!")
            else:
                await send(app, f"🚀 Bot started — {len(found)} products queued\\. Run /list to publish.")
        else:
            await send(app, "🚀 Bot started\\. Run /research to find products\\.")
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
            research_loop(app),
            order_loop(app),
            price_loop(app),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped.")
