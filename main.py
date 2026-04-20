import asyncio
import logging
import sys
from config import TELEGRAM_BOT_TOKEN, EBAY_EMAIL, CJ_EMAIL
from bot import create_app, research_loop, order_loop, price_loop

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


async def main():
    check_config()
    app = create_app()

    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logger.info("✅ Dropship bot running. Open Telegram and send /start")

        await asyncio.gather(
            research_loop(app),
            order_loop(app),
            price_loop(app),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped.")
