import asyncio
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    RESEARCH_INTERVAL_HOURS, PRICE_SYNC_INTERVAL_HOURS, ORDER_POLL_MINUTES
)
from database import init_db, get_stats, get_active_listings, get_setting, set_setting
from research import research_products
from lister import list_ready_products
from price_sync import sync_prices
from order_processor import poll_new_orders, process_pending_orders

logger = logging.getLogger(__name__)


def auth(update: Update) -> bool:
    if not TELEGRAM_CHAT_ID:
        return True
    return str(update.effective_chat.id) == str(TELEGRAM_CHAT_ID)


async def send(app: Application, text: str):
    if TELEGRAM_CHAT_ID:
        await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode="Markdown",
                                   disable_web_page_preview=True)


# ── Commands ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    text = (
        "🤖 *Dropship Bot — Command Center*\n\n"
        "*/status* — Profit summary & active listings\n"
        "*/research* — Find new products to list\n"
        "*/list* — List ready products on eBay now\n"
        "*/listings* — View all active eBay listings\n"
        "*/orders* — Check & fulfill pending orders\n"
        "*/syncprices* — Sync Walmart prices → eBay\n"
        "*/pause* — Pause all automation\n"
        "*/resume* — Resume automation\n"
        "*/setmargin <pct>* — Change min margin (e.g. /setmargin 25)\n"
        "*/setmarkup <pct>* — Change markup (e.g. /setmarkup 40)\n"
        "*/setmax <n>* — Max active listings (e.g. /setmax 30)"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    s = get_stats()
    paused = get_setting("paused", "false") == "true"
    state = "⏸️ PAUSED" if paused else "▶️ RUNNING"
    text = (
        f"📊 *Dropship Bot Status* — {state}\n\n"
        f"💰 Total profit: *${s['total_profit']:.2f}*\n"
        f"💵 Today's profit: *${s['today_profit']:.2f}*\n"
        f"📦 Active listings: {s['active_listings']}\n"
        f"🛒 Pending orders: {s['pending_orders']}\n"
        f"✅ Fulfilled orders: {s['fulfilled_orders']}\n"
        f"📋 Total orders: {s['total_orders']}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_research(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    msg = await update.message.reply_text("🔍 Researching profitable products...")
    found = research_products()
    if not found:
        await msg.edit_text("No new profitable products found right now. Try again later.")
        return
    lines = [f"✅ Found *{len(found)}* products ready to list:\n"]
    for p in found[:8]:
        cost = p.get('total_cost') or p.get('walmart_price') or 0
        lines.append(
            f"• {p['title'][:50]}\n"
            f"  Cost ${cost:.2f} → eBay ${p['ebay_price']:.2f} "
            f"({p['margin_percent']:.1f}% margin)"
        )
    lines.append("\nRun /list to create eBay listings.")
    await msg.edit_text("\n".join(lines), parse_mode="Markdown")


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    msg = await update.message.reply_text("📝 Creating eBay listings...")
    listed = list_ready_products(limit=10)
    if not listed:
        await msg.edit_text("No ready products. Run /research first.")
        return
    lines = [f"✅ *{len(listed)} listings created on eBay!*\n"]
    for l in listed:
        lines.append(
            f"• {l['title'][:50]}\n"
            f"  Item ID: `{l['ebay_item_id']}` @ ${l['ebay_price']:.2f}"
        )
    await msg.edit_text("\n".join(lines), parse_mode="Markdown")


async def cmd_listings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    listings = get_active_listings()
    if not listings:
        await update.message.reply_text("No active listings. Run /research then /list.")
        return
    lines = [f"📋 *{len(listings)} Active Listings:*\n"]
    for l in listings[:15]:
        lines.append(f"• {l['title'][:45]} — ${l['ebay_price']:.2f} (cost ${l['walmart_price']:.2f})")
    if len(listings) > 15:
        lines.append(f"\n...and {len(listings)-15} more")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    msg = await update.message.reply_text("🔄 Checking eBay for new orders...")
    new = poll_new_orders()
    if new:
        await msg.edit_text(f"📬 {len(new)} new order(s)! Fulfilling via Walmart...")
        results = await process_pending_orders()
        lines = [f"*Order Processing Complete:*\n"]
        for r in results:
            if r["success"]:
                lines.append(f"✅ {r['buyer_name']} — profit *${r['net_profit']:.2f}*")
            else:
                lines.append(f"❌ {r['ebay_order_id']} — {r['error'][:50]}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    else:
        pending_results = await process_pending_orders()
        if pending_results:
            lines = [f"*Processed {len(pending_results)} pending order(s):*\n"]
            for r in pending_results:
                if r["success"]:
                    lines.append(f"✅ Profit: *${r['net_profit']:.2f}*")
                else:
                    lines.append(f"❌ Failed: {r['error'][:50]}")
            await msg.edit_text("\n".join(lines), parse_mode="Markdown")
        else:
            await msg.edit_text("No new or pending orders.")


async def cmd_syncprices(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    msg = await update.message.reply_text("🔄 Syncing prices with Walmart...")
    result = sync_prices()
    await msg.edit_text(
        f"✅ Price sync complete:\n"
        f"• Checked: {result['checked']} listings\n"
        f"• Updated: {result['updated']}\n"
        f"• Ended (no longer profitable): {result['ended']}",
        parse_mode="Markdown"
    )


async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    set_setting("paused", "true")
    await update.message.reply_text("⏸️ Bot paused. Use /resume to restart.")


async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    set_setting("paused", "false")
    await update.message.reply_text("▶️ Bot resumed.")


async def cmd_setmargin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /setmargin 25")
        return
    try:
        val = float(ctx.args[0])
        set_setting("min_margin", str(val))
        await update.message.reply_text(f"✅ Minimum margin set to {val}%")
    except ValueError:
        await update.message.reply_text("Provide a number, e.g. /setmargin 25")


async def cmd_setmarkup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /setmarkup 40")
        return
    try:
        val = float(ctx.args[0])
        set_setting("markup", str(val))
        await update.message.reply_text(f"✅ Markup set to {val}%")
    except ValueError:
        await update.message.reply_text("Provide a number, e.g. /setmarkup 40")


async def cmd_setmax(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /setmax 30")
        return
    try:
        val = int(ctx.args[0])
        set_setting("max_listings", str(val))
        await update.message.reply_text(f"✅ Max listings set to {val}")
    except ValueError:
        await update.message.reply_text("Provide a number, e.g. /setmax 30")


# ── Background loops ──────────────────────────────────────────────────────────

async def research_loop(app: Application):
    while True:
        await asyncio.sleep(RESEARCH_INTERVAL_HOURS * 3600)
        if get_setting("paused", "false") == "true":
            continue
        logger.info("[Research loop] Starting...")
        try:
            found = research_products()
            if found:
                listed = list_ready_products(limit=len(found))
                if listed:
                    await send(app, f"🆕 Auto-listed *{len(listed)}* new products on eBay!")
        except Exception as e:
            logger.error(f"[Research loop] Error: {e}")


async def order_loop(app: Application):
    while True:
        await asyncio.sleep(ORDER_POLL_MINUTES * 60)
        if get_setting("paused", "false") == "true":
            continue
        logger.info("[Order loop] Polling eBay for orders...")
        try:
            new = poll_new_orders()
            if new:
                await send(app, f"📬 *{len(new)} new eBay order(s)!* Fulfilling now...")
            results = await process_pending_orders()
            for r in results:
                if r["success"]:
                    await send(
                        app,
                        f"✅ *Order fulfilled!*\nBuyer: {r['buyer_name']}\nProfit: *${r['net_profit']:.2f}*"
                    )
                else:
                    await send(app, f"❌ Fulfillment failed for {r['ebay_order_id']}: {r['error'][:80]}")
        except Exception as e:
            logger.error(f"[Order loop] Error: {e}")


async def price_loop(app: Application):
    while True:
        await asyncio.sleep(PRICE_SYNC_INTERVAL_HOURS * 3600)
        if get_setting("paused", "false") == "true":
            continue
        logger.info("[Price loop] Syncing prices...")
        try:
            result = sync_prices()
            if result["ended"] > 0:
                await send(app, f"⚠️ {result['ended']} listing(s) ended (no longer profitable after price change).")
        except Exception as e:
            logger.error(f"[Price loop] Error: {e}")


def create_app() -> Application:
    init_db()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("research", cmd_research))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("listings", cmd_listings))
    app.add_handler(CommandHandler("orders", cmd_orders))
    app.add_handler(CommandHandler("syncprices", cmd_syncprices))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("setmargin", cmd_setmargin))
    app.add_handler(CommandHandler("setmarkup", cmd_setmarkup))
    app.add_handler(CommandHandler("setmax", cmd_setmax))

    return app
