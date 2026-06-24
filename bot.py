import asyncio
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ORDER_POLL_MINUTES, MIN_MARGIN_PERCENT, SHIP_COST_ESTIMATE
from database import (
    init_db, get_stats, get_active_listings, get_setting, set_setting,
    deactivate_listing, get_all_products, add_inventory_item,
    get_fulfilled_orders, reset_orphaned_products, get_order_by_ebay_id, mark_order_fulfilled,
)
from ebay_client import get_active_ebay_listings, end_listing, get_suggested_category, revise_category, mark_order_shipped
from comps import check_item
from lister import list_ready_products
from order_processor import poll_new_orders

logger = logging.getLogger(__name__)


def _esc(text: str) -> str:
    """Escape Markdown special chars in user-generated content (titles, names)."""
    for ch in ('*', '_', '`', '['):
        text = text.replace(ch, f'\\{ch}')
    return text


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
        "🤖 *Retail Arbitrage Bot — Command Center*\n\n"
        "*📊 Overview*\n"
        "*/status* — Profit summary & active listings\n"
        "*/profit* — Detailed profit breakdown (today/week/month)\n"
        "*/history [n]* — Last N fulfilled orders\n\n"
        "*🔍 Sourcing*\n"
        "*/check <price> <item name>* — eBay comp check on a clearance item\n"
        "*/instock <qty> <cost> <sell_price> <image_url> <title>* — Add bought stock\n"
        "*/inventory* — Items bought but not yet listed\n\n"
        "*📝 Listing*\n"
        "*/list [n]* — List ready inventory on eBay\n"
        "*/listings* — View all active eBay listings\n"
        "*/dedupe* — Remove duplicate listings\n"
        "*/recategorize* — Fix categories on all listings\n"
        "*/trimlistings [n]* — Keep only top N listings by sales/margin\n\n"
        "*📦 Orders*\n"
        "*/orders [days]* — Check for new eBay orders\n"
        "*/markshipped <order_id> [tracking]* — Mark as shipped on eBay\n"
        "*/lookup <ebay_item_id>* — Inventory details for a listing\n\n"
        "*⚙️ Settings*\n"
        "*/pause* / */resume* — Pause or resume automation\n"
        "*/setmargin <pct>* — Min margin for /check (e.g. /setmargin 25)\n"
        "*/setmax <n>* — Max listings (e.g. /setmax 30)\n"
        "*/endall CONFIRM* — End every active listing"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    s = get_stats()
    paused = get_setting("paused", "false") == "true"
    state = "⏸️ PAUSED" if paused else "▶️ RUNNING"
    text = (
        f"📊 *Retail Arbitrage Bot Status* — {state}\n\n"
        f"💰 Total profit: *${s['total_profit']:.2f}*\n"
        f"📅 Today: *${s['today_profit']:.2f}* | 📆 This week: *${s['week_profit']:.2f}*\n"
        f"💵 Avg profit/order: *${s['avg_profit']:.2f}*\n\n"
        f"📦 Active listings: {s['active_listings']}\n"
        f"📥 Inventory units on hand: {s['inventory_units']}\n"
        f"🛒 Pending shipment: {s['pending_orders']} | ✅ Fulfilled: {s['fulfilled_orders']}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "Usage: /check <clearance price> <item name>\n"
            "Example: /check 8.97 Lego Star Wars 75192"
        )
        return
    try:
        price = float(ctx.args[0])
    except ValueError:
        await update.message.reply_text("First argument must be the clearance price, e.g. /check 8.97 Lego set")
        return
    name = " ".join(ctx.args[1:]).strip()
    msg = await update.message.reply_text(f"🔍 Checking eBay sold comps for '{_esc(name)}'...", parse_mode="Markdown")
    result = check_item(name, price)

    if result["median"] is None:
        await msg.edit_text(
            f"❓ {_esc(name)}\n"
            f"No eBay sold comps found — too few completed sales to judge demand. Pass unless you already know this sells.",
            parse_mode="Markdown"
        )
        return

    verdict_line = "✅ *BUY*" if result["verdict"] == "BUY" else "❌ *PASS*"
    note = "Run /instock once you've bought it." if result["verdict"] == "BUY" else "Margin too thin at this price."
    await msg.edit_text(
        f"{verdict_line}\n\n"
        f"Item: {_esc(name)}\n"
        f"Clearance price: ${price:.2f} (+ ~${SHIP_COST_ESTIMATE:.2f} shipping = ${result['cost']:.2f})\n"
        f"eBay sold median: ${result['median']:.2f}\n"
        f"Suggested list price: ${result['sell_price']:.2f}\n"
        f"Margin: {result['margin']:.1f}% (need {MIN_MARGIN_PERCENT:.0f}%)\n\n"
        f"{note}",
        parse_mode="Markdown"
    )


async def cmd_instock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    if len(ctx.args) < 5:
        await update.message.reply_text(
            "Usage: /instock <qty> <cost> <sell_price> <image_url> <title...>\n"
            "Example: /instock 3 8.97 24.99 https://example.com/photo.jpg Lego Star Wars 75192"
        )
        return
    try:
        qty = int(ctx.args[0])
        cost = float(ctx.args[1])
        sell_price = float(ctx.args[2])
    except ValueError:
        await update.message.reply_text("qty, cost, and sell_price must be numbers.")
        return
    image_url = ctx.args[3].strip()
    title = " ".join(ctx.args[4:]).strip()
    if not title:
        await update.message.reply_text("Include a title after the image URL.")
        return

    from comps import calculate_margin
    margin = calculate_margin(cost, sell_price)
    product_id = add_inventory_item(
        title=title, cost_price=cost, ebay_price=sell_price,
        margin_percent=margin, qty=qty, image_url=image_url,
    )
    note = f"⚠️ Low margin ({margin:.1f}%)" if margin < MIN_MARGIN_PERCENT else f"{margin:.1f}% margin"
    await update.message.reply_text(
        f"✅ Added to inventory (#{product_id})\n"
        f"{_esc(title[:60])}\n"
        f"Qty: {qty} @ cost ${cost:.2f} → list ${sell_price:.2f} ({note})\n\n"
        f"Run /list to publish it on eBay.",
        parse_mode="Markdown"
    )


async def cmd_inventory(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    items = get_all_products(status="ready")
    if not items:
        await update.message.reply_text("No inventory waiting to be listed. Add some with /instock.")
        return
    lines = [f"📦 *{len(items)} item(s) ready to list:*\n"]
    for it in items[:20]:
        lines.append(
            f"• #{it['id']} {_esc(it['title'][:45])} — qty {it['qty_on_hand']} "
            f"@ ${it['ebay_price']:.2f} ({it['margin_percent']:.1f}%)"
        )
    if len(items) > 20:
        lines.append(f"\n...and {len(items) - 20} more")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    limit = 10
    if ctx.args:
        try:
            limit = int(ctx.args[0])
        except ValueError:
            await update.message.reply_text("Usage: /list [number] — e.g. /list 5")
            return
    msg = await update.message.reply_text(f"📝 Creating up to {limit} eBay listings...")
    from database import get_ready_products
    ready_count = len(get_ready_products(limit=200))
    listed, first_error = await list_ready_products(limit=limit)
    if not listed:
        if ready_count == 0:
            await msg.edit_text("No ready inventory. Add some with /instock first.")
        else:
            await msg.edit_text(f"❌ Found {ready_count} ready item(s) but eBay rejected them.\nError: {first_error}")
        return
    lines = [f"✅ *{len(listed)} listings created on eBay!*\n"]
    for l in listed:
        lines.append(
            f"• {_esc(l['title'][:50])}\n"
            f"  Item ID: `{l['ebay_item_id']}` @ ${l['ebay_price']:.2f}"
        )
    await msg.edit_text("\n".join(lines), parse_mode="Markdown")


async def cmd_listings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    listings = get_active_listings()
    if not listings:
        await update.message.reply_text("No active listings. Add stock with /instock then /list.")
        return
    lines = [f"📋 *{len(listings)} Active Listings:*\n"]
    for l in listings[:15]:
        lines.append(f"• {_esc(l['title'][:45])} — ${l['ebay_price']:.2f} (cost ${l['cost_price']:.2f})")
    if len(listings) > 15:
        lines.append(f"\n...and {len(listings)-15} more")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    days_back = 3
    if ctx.args:
        try:
            days_back = max(1, min(int(ctx.args[0]), 90))
        except ValueError:
            await update.message.reply_text("Usage: /orders [days] — e.g. /orders 30")
            return
    msg = await update.message.reply_text(f"🔄 Checking eBay orders from the last {days_back} day(s)...")
    new = poll_new_orders(days_back=days_back)
    if not new:
        await msg.edit_text("No new orders.")
        return
    lines = [f"📬 *{len(new)} new order(s):*\n"]
    for o in new:
        lines.append(
            f"• `{o['ebay_order_id']}` — {_esc(o['buyer_name'])}\n"
            f"  {_esc(o['title'][:45])} — profit *${o['net_profit']:.2f}*\n"
            f"  📦 Ship to: {_esc(o['address'])}, {o['city']}, {o['state']} {o['zip']}\n"
            f"  → /markshipped `{o['ebay_order_id']}` <tracking>"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


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


async def cmd_profit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    s = get_stats()
    text = (
        f"💰 *Profit Breakdown*\n\n"
        f"📅 Today: *${s['today_profit']:.2f}*\n"
        f"📆 This week: *${s['week_profit']:.2f}*\n"
        f"🗓 This month: *${s['month_profit']:.2f}*\n"
        f"📊 All time: *${s['total_profit']:.2f}*\n\n"
        f"📦 Fulfilled orders: {s['fulfilled_orders']}\n"
        f"💵 Avg profit/order: *${s['avg_profit']:.2f}*"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    limit = 10
    if ctx.args:
        try:
            limit = max(1, min(int(ctx.args[0]), 50))
        except ValueError:
            pass
    orders = get_fulfilled_orders(limit)
    if not orders:
        await update.message.reply_text("No fulfilled orders yet.")
        return
    lines = [f"📦 *Last {len(orders)} Fulfilled Orders:*\n"]
    for o in orders:
        profit = o.get("net_profit") or 0
        title = o.get("title") or "Unknown item"
        lines.append(
            f"• `{o['ebay_order_id']}`\n"
            f"  {_esc(o['buyer_name'])} — {_esc(title[:40])}\n"
            f"  Profit: *${profit:.2f}* | {(o.get('fulfilled_at') or '')[:10]}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_markshipped(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /markshipped <ebay_order_id> [tracking_number]")
        return
    ebay_order_id = ctx.args[0].strip()
    tracking = ctx.args[1].strip() if len(ctx.args) > 1 else ""
    msg = await update.message.reply_text(f"🔄 Marking order `{ebay_order_id}` as shipped...", parse_mode="Markdown")
    success = await mark_order_shipped(ebay_order_id, tracking_number=tracking)
    if success:
        order = get_order_by_ebay_id(ebay_order_id)
        if order:
            mark_order_fulfilled(order["id"], tracking=tracking)
        await msg.edit_text(f"✅ eBay order `{ebay_order_id}` marked as shipped.", parse_mode="Markdown")
    else:
        await msg.edit_text("❌ Failed — check Railway logs for details.")


async def cmd_lookup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /lookup <ebay_item_id>")
        return
    ebay_item_id = ctx.args[0].strip()
    with __import__('database').get_db() as db:
        row = db.execute(
            """SELECT p.title, p.cost_price as cost, p.qty_on_hand, l.ebay_price
               FROM listings l JOIN products p ON l.product_id = p.id
               WHERE l.ebay_item_id = ?""",
            (ebay_item_id,)
        ).fetchone()
    if not row:
        await update.message.reply_text(f"No listing found for eBay item `{ebay_item_id}`.", parse_mode="Markdown")
        return
    text = (
        f"🔍 *Listing {ebay_item_id}*\n\n"
        f"*Title:* {_esc(row['title'][:80])}\n"
        f"*eBay price:* ${row['ebay_price']:.2f}\n"
        f"*Your cost:* ${row['cost']:.2f}\n"
        f"*Qty on hand:* {row['qty_on_hand']}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


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


# ── Deduplication ────────────────────────────────────────────────────────────

async def remove_duplicate_listings() -> dict:
    """End all but the earliest listing for any duplicated eBay title."""
    listings = get_active_ebay_listings()
    by_title = {}
    for l in listings:
        key = l["title"].lower().strip()
        by_title.setdefault(key, []).append(l)

    ended = 0
    for dupes in by_title.values():
        if len(dupes) <= 1:
            continue
        dupes.sort(key=lambda x: x["ebay_item_id"])  # lowest ID = listed first
        for dupe in dupes[1:]:
            if await end_listing(dupe["ebay_item_id"]):
                deactivate_listing(dupe["ebay_item_id"])
                ended += 1
                logger.info(f"Removed duplicate listing {dupe['ebay_item_id']}: {dupe['title'][:50]}")

    return {"checked": len(listings), "removed": ended}


async def cmd_dedupe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    msg = await update.message.reply_text("🔍 Scanning for duplicate listings...")
    result = await remove_duplicate_listings()
    if result["removed"]:
        await msg.edit_text(
            f"✅ Dedupe complete — removed *{result['removed']}* duplicate(s) "
            f"from {result['checked']} listings.",
            parse_mode="Markdown"
        )
    else:
        await msg.edit_text(f"✅ No duplicates found across {result['checked']} listings.")


async def cmd_recategorize(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    listings = get_active_listings()
    if not listings:
        await update.message.reply_text("No active listings to recategorize.")
        return
    msg = await update.message.reply_text(f"🔄 Recategorizing {len(listings)} listings — this may take a minute...")
    updated = 0
    failed = 0
    for l in listings:
        try:
            cat = get_suggested_category(l["title"])
            success = await revise_category(l["ebay_item_id"], cat)
            if not success and cat != "112581":
                logger.warning(f"  Category {cat} failed for {l['ebay_item_id']}, retrying with 112581")
                success = await revise_category(l["ebay_item_id"], "112581")
            if success:
                updated += 1
            else:
                failed += 1
        except Exception as e:
            logger.error(f"Recategorize error for {l['ebay_item_id']}: {e}")
            failed += 1
        await asyncio.sleep(0.5)  # avoid rate limiting
    await msg.edit_text(
        f"✅ Recategorize complete:\n"
        f"• Updated: {updated}\n"
        f"• Failed: {failed}",
        parse_mode="Markdown"
    )


# ── Background loops ──────────────────────────────────────────────────────────

async def order_loop(app: Application):
    while True:
        await asyncio.sleep(ORDER_POLL_MINUTES * 60)
        if get_setting("paused", "false") == "true":
            continue
        logger.info("[Order loop] Polling eBay for orders...")
        try:
            new = poll_new_orders()
            for o in new:
                await send(
                    app,
                    f"📬 *New order!* Profit: ${o['net_profit']:.2f}\n"
                    f"{_esc(o['title'][:50])}\n"
                    f"📦 Ship to: {_esc(o['buyer_name'])}\n{_esc(o['address'])}, {o['city']}, {o['state']} {o['zip']}\n\n"
                    f"→ /markshipped `{o['ebay_order_id']}` <tracking>"
                )
        except Exception as e:
            logger.error(f"[Order loop] Error: {e}")


async def cmd_endall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    if not ctx.args or ctx.args[0] != "CONFIRM":
        await update.message.reply_text(
            "⚠️ This ends ALL active eBay listings permanently.\n"
            "Send `/endall CONFIRM` to proceed.",
            parse_mode="Markdown"
        )
        return

    msg = await update.message.reply_text("🔄 Ending all active listings...")
    listings = get_active_listings()
    if not listings:
        await msg.edit_text("No active listings to end.")
        return

    ended = 0
    failed = 0
    for l in listings:
        try:
            await end_listing(l["ebay_item_id"])
            deactivate_listing(l["ebay_item_id"])
            ended += 1
        except Exception as e:
            logger.error(f"[endall] Failed to end {l['ebay_item_id']}: {e}")
            failed += 1
        await asyncio.sleep(0.3)

    set_setting("paused", "true")
    text = f"✅ Ended {ended} listing(s)"
    if failed:
        text += f", {failed} failed (check Railway logs)"
    text += ". Bot paused — no new order activity will run."
    await msg.edit_text(text)


async def cmd_trimlistings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    keep = 50
    if ctx.args:
        try:
            keep = max(1, int(ctx.args[0]))
        except ValueError:
            pass
    msg = await update.message.reply_text(f"🔄 Trimming to top {keep} listings by sales then margin...")

    from database import get_db
    with get_db() as db:
        rows = db.execute(
            """SELECT l.ebay_item_id, l.id,
                      COUNT(o.id) as sales,
                      p.margin_percent
               FROM listings l
               JOIN products p ON l.product_id = p.id
               LEFT JOIN orders o ON o.listing_id = l.id AND o.status = 'fulfilled'
               WHERE l.status = 'active'
               GROUP BY l.id
               ORDER BY sales DESC, p.margin_percent DESC"""
        ).fetchall()

    to_keep = {r["ebay_item_id"] for r in rows[:keep]}
    to_end = [r["ebay_item_id"] for r in rows[keep:]]

    if not to_end:
        await msg.edit_text(f"Already at or under {keep} listings — nothing to end.")
        return

    ended = 0
    failed = 0
    for ebay_item_id in to_end:
        try:
            await end_listing(ebay_item_id)
            deactivate_listing(ebay_item_id)
            ended += 1
        except Exception as e:
            logger.error(f"[trimlistings] Failed to end {ebay_item_id}: {e}")
            failed += 1
        await asyncio.sleep(0.3)

    text = f"✅ Done\\. Kept {len(to_keep)}, ended {ended}"
    if failed:
        text += f", failed {failed} \\(check logs\\)"
    await msg.edit_text(text, parse_mode="MarkdownV2")


def create_app() -> Application:
    init_db()
    reset_orphaned_products()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("instock", cmd_instock))
    app.add_handler(CommandHandler("inventory", cmd_inventory))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("listings", cmd_listings))
    app.add_handler(CommandHandler("orders", cmd_orders))
    app.add_handler(CommandHandler("dedupe", cmd_dedupe))
    app.add_handler(CommandHandler("recategorize", cmd_recategorize))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("lookup", cmd_lookup))
    app.add_handler(CommandHandler("markshipped", cmd_markshipped))
    app.add_handler(CommandHandler("setmargin", cmd_setmargin))
    app.add_handler(CommandHandler("setmax", cmd_setmax))
    app.add_handler(CommandHandler("profit", cmd_profit))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("trimlistings", cmd_trimlistings))
    app.add_handler(CommandHandler("endall", cmd_endall))

    return app
