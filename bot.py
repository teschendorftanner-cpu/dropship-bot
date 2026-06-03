import asyncio
import logging
import re
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    RESEARCH_INTERVAL_HOURS, PRICE_SYNC_INTERVAL_HOURS, ORDER_POLL_MINUTES
)
from database import (
    init_db, get_stats, get_active_listings, get_setting, set_setting,
    deactivate_listing, get_fulfilled_without_tracking, save_tracking,
    get_keywords, add_keyword, remove_keyword,
    get_failed_orders, get_fulfilled_orders,
)
from ebay_client import get_active_ebay_listings, end_listing, get_suggested_category, revise_category
from research import research_products
from lister import list_ready_products
from price_sync import sync_prices
from order_processor import poll_new_orders, process_pending_orders

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
        "🤖 *Dropship Bot — Command Center*\n\n"
        "*📊 Overview*\n"
        "*/status* — Profit summary & active listings\n"
        "*/profit* — Detailed profit breakdown (today/week/month)\n"
        "*/history [n]* — Last N fulfilled orders\n\n"
        "*🔍 Research & Listing*\n"
        "*/research* — Find new products to list\n"
        "*/list [n]* — List ready products on eBay\n"
        "*/listings* — View all active eBay listings\n"
        "*/addproduct <cj_url>* — Manually queue a specific CJ product\n"
        "*/checklinks* — See which listings are missing a CJ variant ID\n"
        "*/addkeyword <keyword>* — Add a research keyword\n"
        "*/removekeyword <keyword>* — Remove a keyword\n"
        "*/keywords* — List all custom keywords\n\n"
        "*📦 Orders*\n"
        "*/orders [days]* — Check & fulfill pending orders\n"
        "*/failed* — Show failed orders needing attention\n"
        "*/retry <order_id>* — Retry a failed order automatically\n"
        "*/fulfill <order_id> <cj_url>* — Manually fulfill with a CJ link\n"
        "*/markshipped <order_id> [tracking]* — Mark as shipped on eBay\n"
        "*/lookup <ebay_item_id>* — Get CJ link for a listing\n\n"
        "*⚙️ Settings*\n"
        "*/syncprices* — Sync CJ prices → eBay\n"
        "*/dedupe* — Remove duplicate listings\n"
        "*/recategorize* — Fix categories on all listings\n"
        "*/pause* / */resume* — Pause or resume automation\n"
        "*/setmargin <pct>* — Min margin (e.g. /setmargin 25)\n"
        "*/setmarkup <pct>* — Markup (e.g. /setmarkup 40)\n"
        "*/setmax <n>* — Max listings (e.g. /setmax 30)"
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
        f"📅 Today: *${s['today_profit']:.2f}* | 📆 This week: *${s['week_profit']:.2f}*\n"
        f"💵 Avg profit/order: *${s['avg_profit']:.2f}*\n\n"
        f"📦 Active listings: {s['active_listings']}\n"
        f"🛒 Pending: {s['pending_orders']} | ✅ Fulfilled: {s['fulfilled_orders']} | ❌ Failed: {s['failed_orders']}"
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
        cost = p.get('total_cost') or p.get('cj_price') or 0
        lines.append(
            f"• {_esc(p['title'][:50])}\n"
            f"  Cost ${cost:.2f} → eBay ${p['ebay_price']:.2f} "
            f"({p['margin_percent']:.1f}% margin)"
        )
    lines.append("\nRun /list to create eBay listings.")
    await msg.edit_text("\n".join(lines), parse_mode="Markdown")


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
    listed = await list_ready_products(limit=limit)
    if not listed:
        await msg.edit_text("No ready products. Run /research first.")
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
        await update.message.reply_text("No active listings. Run /research then /list.")
        return
    lines = [f"📋 *{len(listings)} Active Listings:*\n"]
    for l in listings[:15]:
        lines.append(f"• {_esc(l['title'][:45])} — ${l['ebay_price']:.2f} (cost ${l['cj_price']:.2f})")
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
    if new:
        await msg.edit_text(f"📬 {len(new)} new order(s)! Fulfilling via CJ...")
        results = await process_pending_orders()
        lines = [f"*Order Processing Complete:*\n"]
        for r in results:
            if r["success"]:
                lines.append(f"✅ {_esc(r['buyer_name'])} — profit *${r['net_profit']:.2f}*")
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
    msg = await update.message.reply_text("🔄 Syncing prices with CJ...")
    result = await sync_prices()
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


async def cmd_fulfill(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /fulfill <ebay_order_id> <cj_url>")
        return
    ebay_order_id = ctx.args[0].strip()
    cj_url = ctx.args[1].strip()

    match = re.search(r'-p-([A-Za-z0-9]+)\.html', cj_url)
    if not match:
        await update.message.reply_text("Could not parse a CJ product ID from that URL.")
        return
    product_id = match.group(1)

    msg = await update.message.reply_text(f"🔍 Fetching CJ product {product_id}...")

    from cj_client import get_product
    from database import get_order_by_ebay_id, mark_order_fulfilled, log_profit
    from fulfillment import fulfill_order
    from ebay_client import mark_order_shipped
    from config import EBAY_FEE_PERCENT

    product = get_product(product_id)
    if not product:
        await msg.edit_text("❌ Could not fetch product from CJ. Check the URL.")
        return
    variants = product.get("variants", [])
    variant_id = variants[0].get("vid", "") if variants else ""
    if not variant_id:
        await msg.edit_text("❌ No variant ID found for that CJ product.")
        return

    order = get_order_by_ebay_id(ebay_order_id)
    if not order:
        await msg.edit_text(f"No order found for `{ebay_order_id}` — run /orders first to import it.")
        return

    buyer = {
        "name": order["buyer_name"],
        "address": order["buyer_address"],
        "city": order["buyer_city"],
        "state": order["buyer_state"],
        "zip": order["buyer_zip"],
        "country": order.get("buyer_country", "United States"),
        "country_code": "US",
        "phone": "0000000000",
    }

    await msg.edit_text(f"🔄 Placing CJ order for {_esc(order['buyer_name'])}...", parse_mode="Markdown")
    result = await fulfill_order(variant_id=variant_id, buyer=buyer, order_ref=ebay_order_id)

    if result["success"]:
        tracking = result.get("tracking", "")
        mark_order_fulfilled(order["id"], result["order_id"], tracking)
        ebay_fee = order["sale_price"] * (EBAY_FEE_PERCENT / 100)
        net_profit = order["sale_price"] - ebay_fee - order["cj_price"]
        log_profit(order["id"], order["sale_price"], order["cj_price"], ebay_fee, net_profit)
        await mark_order_shipped(ebay_order_id, tracking_number=tracking)
        await msg.edit_text(
            f"✅ *Order fulfilled!*\nCJ order: `{result['order_id']}`\n"
            f"Tracking: `{tracking or 'pending'}`\nProfit: *${net_profit:.2f}*",
            parse_mode="Markdown"
        )
    else:
        await msg.edit_text(f"❌ CJ order failed: {result['error']}")


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
        f"💵 Avg profit/order: *${s['avg_profit']:.2f}*\n"
        f"❌ Failed orders: {s['failed_orders']}"
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


async def cmd_failed(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    orders = get_failed_orders()
    if not orders:
        await update.message.reply_text("✅ No failed orders.")
        return
    lines = [f"❌ *{len(orders)} Failed Order(s):*\n"]
    for o in orders:
        title = o.get("title") or "Unknown item"
        reason = o.get("failure_reason") or "unknown error"
        lines.append(
            f"• `{o['ebay_order_id']}`\n"
            f"  {_esc(o['buyer_name'])}\n"
            f"  {_esc(o.get('buyer_address',''))}, {o.get('buyer_city','')}, {o.get('buyer_state','')}\n"
            f"  Item: {_esc(title[:45])}\n"
            f"  Error: _{reason[:70]}_\n"
            f"  → /retry `{o['ebay_order_id']}` or /fulfill `{o['ebay_order_id']}` <cj\\_url>"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_retry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /retry <ebay_order_id>")
        return
    ebay_order_id = ctx.args[0].strip()
    from database import get_order_by_ebay_id, get_db
    order = get_order_by_ebay_id(ebay_order_id)
    if not order:
        await update.message.reply_text(f"Order `{ebay_order_id}` not found — run /orders first to import it.", parse_mode="Markdown")
        return
    if order["status"] not in ("failed", "pending"):
        await update.message.reply_text(
            f"Order status is `{order['status']}` — can only retry failed or pending orders.",
            parse_mode="Markdown"
        )
        return
    with get_db() as db:
        db.execute("UPDATE orders SET status='pending', failure_reason='' WHERE ebay_order_id=?", (ebay_order_id,))
    msg = await update.message.reply_text(f"🔄 Retrying order `{ebay_order_id}`...", parse_mode="Markdown")
    from order_processor import process_pending_orders
    results = await process_pending_orders()
    for r in results:
        if r.get("ebay_order_id") == ebay_order_id:
            if r["success"]:
                await msg.edit_text(
                    f"✅ *Order fulfilled!*\nProfit: *${r['net_profit']:.2f}*",
                    parse_mode="Markdown"
                )
            else:
                await msg.edit_text(
                    f"❌ Still failing: {r['error'][:80]}\n\nTry: /fulfill `{ebay_order_id}` <cj\\_url>",
                    parse_mode="Markdown"
                )
            return
    await msg.edit_text("Processed — check /status or Railway logs for result.", parse_mode="Markdown")


async def cmd_addkeyword(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /addkeyword <keyword>\nExample: /addkeyword car phone holder")
        return
    keyword = " ".join(ctx.args).strip()
    added = add_keyword(keyword)
    if added:
        await update.message.reply_text(f"✅ Added to research rotation: *{_esc(keyword)}*", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"Already in rotation: *{_esc(keyword)}*", parse_mode="Markdown")


async def cmd_removekeyword(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /removekeyword <keyword>")
        return
    keyword = " ".join(ctx.args).strip()
    removed = remove_keyword(keyword)
    if removed:
        await update.message.reply_text(f"✅ Removed: *{_esc(keyword)}*", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"Keyword not found: *{_esc(keyword)}*\n\nSee /keywords for the full list.", parse_mode="Markdown")


async def cmd_keywords(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    keywords = get_keywords()
    if not keywords:
        await update.message.reply_text(
            "No custom keywords set — using the built-in list of 50 niches.\n\n"
            "Add your own with /addkeyword <keyword>"
        )
        return
    lines = [f"🔍 *{len(keywords)} Custom Keywords:*\n"]
    for kw in keywords:
        lines.append(f"• {_esc(kw)}")
    lines.append("\nAdd more with /addkeyword or remove with /removekeyword")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_checklinks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    listings = get_active_listings()
    if not listings:
        await update.message.reply_text("No active listings.")
        return
    missing = [l for l in listings if not l.get("cj_variant_id")]
    linked = len(listings) - len(missing)
    if not missing:
        await update.message.reply_text(
            f"✅ All {linked} listings have a CJ variant ID linked.",
            parse_mode="Markdown"
        )
        return
    lines = [
        f"🔗 *{linked}/{len(listings)} listings linked to CJ*\n",
        f"❌ *{len(missing)} missing a CJ variant ID:*\n"
    ]
    for l in missing[:20]:
        lines.append(f"• `{l['ebay_item_id']}` — {_esc(l['title'][:50])}")
    if len(missing) > 20:
        lines.append(f"\n...and {len(missing) - 20} more")
    lines.append("\nFix with: /addproduct <cj_url> or /fulfill <order_id> <cj_url>")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_addproduct(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /addproduct <cj_url>\nExample: /addproduct https://cjdropshipping.com/product/...-p-ABC123.html")
        return
    cj_url = ctx.args[0].strip()
    match = re.search(r'-p-([A-Za-z0-9]+)\.html', cj_url)
    if not match:
        await update.message.reply_text("❌ Could not parse a CJ product ID from that URL.")
        return
    product_id = match.group(1)
    msg = await update.message.reply_text(f"🔍 Fetching CJ product {product_id}...")
    from cj_client import get_product, get_shipping_cost, get_product_images
    from database import upsert_product
    from config import MARKUP_PERCENT, MIN_MARGIN_PERCENT
    from research import calculate_margin
    product = get_product(product_id)
    if not product:
        await msg.edit_text("❌ Could not fetch product from CJ. Check the URL.")
        return
    variants = product.get("variants", [{}])
    cj_price = float(variants[0].get("variantSellPrice") or product.get("sellPrice") or 0)
    if cj_price <= 0:
        await msg.edit_text("❌ Could not get a price for this product.")
        return
    variant_id = variants[0].get("vid", "") if variants else ""
    title = product.get("productNameEn", "")
    image_url = product.get("productImage", "")
    shipping = get_shipping_cost(product_id)
    total_cost = round(cj_price + shipping, 2)
    ebay_price = round(total_cost * (1 + MARKUP_PERCENT / 100), 2)
    margin = calculate_margin(total_cost, ebay_price)
    extra_imgs = get_product_images(product_id)
    extra_images_str = ",".join(extra_imgs[1:])
    result = upsert_product(
        cj_url=cj_url,
        cj_variant_id=variant_id,
        title=title,
        cj_price=total_cost,
        ebay_price=ebay_price,
        margin_percent=margin,
        image_url=image_url,
        extra_images=extra_images_str,
    )
    if result["ready"]:
        margin_note = f"⚠️ Low margin ({margin:.1f}%)" if margin < MIN_MARGIN_PERCENT else f"{margin:.1f}% margin"
        await msg.edit_text(
            f"✅ *Product queued for listing!*\n\n"
            f"Title: {_esc(title[:60])}\n"
            f"Cost: ${total_cost:.2f} → eBay: ${ebay_price:.2f} ({margin_note})\n\n"
            f"Run /list to publish it on eBay.",
            parse_mode="Markdown"
        )
    else:
        await msg.edit_text(f"Already in your listings: {_esc(title[:60])}", parse_mode="Markdown")


async def cmd_markshipped(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /markshipped <ebay_order_id> [tracking_number]")
        return
    ebay_order_id = ctx.args[0].strip()
    tracking = ctx.args[1].strip() if len(ctx.args) > 1 else ""
    msg = await update.message.reply_text(f"🔄 Marking order `{ebay_order_id}` as shipped...", parse_mode="Markdown")
    from ebay_client import mark_order_shipped
    from database import get_order_by_ebay_id, mark_order_fulfilled
    success = await mark_order_shipped(ebay_order_id, tracking_number=tracking)
    if success:
        order = get_order_by_ebay_id(ebay_order_id)
        if order:
            mark_order_fulfilled(order["id"], cj_order_id=order.get("cj_order_id") or "", tracking=tracking)
        await msg.edit_text(f"✅ eBay order `{ebay_order_id}` marked as shipped.", parse_mode="Markdown")
    else:
        await msg.edit_text(f"❌ Failed — check Railway logs for details.")


async def cmd_lookup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /lookup <ebay_item_id>")
        return
    ebay_item_id = ctx.args[0].strip()
    with __import__('database').get_db() as db:
        row = db.execute(
            """SELECT p.title, p.cj_url, p.cj_variant_id, l.ebay_price, l.cj_price
               FROM listings l JOIN products p ON l.product_id = p.id
               WHERE l.ebay_item_id = ?""",
            (ebay_item_id,)
        ).fetchone()
    if not row:
        await update.message.reply_text(f"No listing found for eBay item `{ebay_item_id}`.", parse_mode="Markdown")
        return
    cj_url = row["cj_url"] or "not saved"
    variant_id = row["cj_variant_id"] or "not saved"
    text = (
        f"🔍 *Listing {ebay_item_id}*\n\n"
        f"*Title:* {_esc(row['title'][:80])}\n"
        f"*eBay price:* ${row['ebay_price']:.2f}\n"
        f"*CJ cost:* ${row['cj_price']:.2f}\n"
        f"*CJ variant ID:* `{variant_id}`\n"
        f"*CJ URL:* {cj_url}"
    )
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)


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

async def research_loop(app: Application):
    while True:
        await asyncio.sleep(RESEARCH_INTERVAL_HOURS * 3600)
        if get_setting("paused", "false") == "true":
            continue
        logger.info("[Research loop] Starting...")
        try:
            found = research_products()
            if found:
                await send(app, f"🔍 Found *{len(found)}* new products ready to list. Send /list to publish them.")
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
                    msg = f"❌ *Manual fulfillment needed*\nOrder: `{r['ebay_order_id']}`\nError: {r['error'][:80]}"
                    if r.get("buyer_address"):
                        msg += f"\n\n📦 Ship to:\n{_esc(r['buyer_name'])}\n{_esc(r['buyer_address'])}"
                    if r.get("title"):
                        msg += f"\n🛍 Item: {_esc(r['title'][:60])}"
                    await send(app, msg)
        except Exception as e:
            logger.error(f"[Order loop] Error: {e}")

        # Check fulfilled orders that are still waiting for a tracking number
        try:
            from cj_client import get_order_tracking
            from ebay_client import mark_order_shipped
            for order in get_fulfilled_without_tracking():
                tracking = get_order_tracking(order["cj_order_id"])
                if tracking:
                    save_tracking(order["id"], tracking)
                    await mark_order_shipped(order["ebay_order_id"], tracking_number=tracking)
                    logger.info(f"Updated tracking for {order['ebay_order_id']}: {tracking}")
                    await send(app, f"📦 *Tracking updated!*\nOrder: `{order['ebay_order_id']}`\nTracking: `{tracking}`")
        except Exception as e:
            logger.error(f"[Tracking loop] Error: {e}")


async def price_loop(app: Application):
    while True:
        await asyncio.sleep(PRICE_SYNC_INTERVAL_HOURS * 3600)
        if get_setting("paused", "false") == "true":
            continue
        logger.info("[Price loop] Syncing prices...")
        try:
            result = await sync_prices()
            if result["ended"] > 0:
                await send(app, f"⚠️ {result['ended']} listing(s) ended (no longer profitable after price change).")
        except Exception as e:
            logger.error(f"[Price loop] Error: {e}")


async def cmd_diagnose(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return
    msg = await update.message.reply_text("🔬 Running research diagnostics...")

    lines = []

    # 1. CJ auth
    from cj_client import _get_token, search_products as cj_search
    token = _get_token()
    lines.append(f"{'✅' if token else '❌'} CJ auth: {'OK' if token else 'FAILED — check CJ_API_KEY'}")

    # 2. Slot count
    from database import get_active_listings, get_db
    active = get_active_listings()
    from config import MAX_LISTINGS, MIN_MARGIN_PERCENT
    slots = MAX_LISTINGS - len(active)
    lines.append(f"📦 Active listings: {len(active)} / {MAX_LISTINGS} ({slots} slots open)")

    # 3. Products already in DB as 'listed'
    with get_db() as db:
        already_listed = db.execute("SELECT COUNT(*) as c FROM products WHERE status='listed'").fetchone()["c"]
        ready = db.execute("SELECT COUNT(*) as c FROM products WHERE status='ready'").fetchone()["c"]
    lines.append(f"🗄 DB products — listed: {already_listed}, ready to list: {ready}")

    # 3b. Margin math check
    from config import MARKUP_PERCENT, EBAY_FEE_PERCENT
    sample_cost = 20.0
    sample_price = sample_cost * (1 + MARKUP_PERCENT / 100)
    sample_margin = ((sample_price * (1 - EBAY_FEE_PERCENT / 100) - sample_cost) / sample_price) * 100
    margin_ok = sample_margin >= MIN_MARGIN_PERCENT
    lines.append(
        f"{'✅' if margin_ok else '❌'} Margin math: {MARKUP_PERCENT:.0f}% markup → "
        f"{sample_margin:.1f}% margin (need {MIN_MARGIN_PERCENT:.0f}%)"
    )

    # 4. Test one keyword — trace full filter chain on first new product
    from cj_client import get_shipping_cost as cj_shipping
    from ebay_client import get_sold_median
    test_keyword = "LED interior car lights RGB"
    results = cj_search(test_keyword, page_size=10)
    lines.append(f"🔍 CJ search '{test_keyword}': {len(results)} raw results")
    if results:
        price_ok = [r for r in results if 5 <= r["price"] <= 50]
        has_image = [r for r in price_ok if r.get("image_url")]
        lines.append(f"   • Price $5–$50: {len(price_ok)}, has image: {len(has_image)}")
        with get_db() as db:
            new_products = [r for r in has_image if not db.execute(
                "SELECT 1 FROM products WHERE cj_url=?", (r["url"],)).fetchone()]
        lines.append(f"   • New to DB: {len(new_products)}")

        if new_products:
            p = new_products[0]
            shipping = cj_shipping(p["product_id"])
            total_cost = round(p["price"] + shipping, 2)
            ebay_price = round(total_cost * (1 + MARKUP_PERCENT / 100), 2)
            try:
                ebay_median = get_sold_median(test_keyword)
            except Exception:
                ebay_median = None
            capped = False
            if ebay_median and ebay_price > ebay_median * 0.92:
                ebay_price = round(ebay_median * 0.92, 2)
                capped = True
            margin = ((ebay_price * (1 - EBAY_FEE_PERCENT / 100) - total_cost) / ebay_price) * 100
            lines.append(f"\n📊 Sample product trace:")
            lines.append(f"   CJ price: ${p['price']:.2f} + shipping ${shipping:.2f} = ${total_cost:.2f}")
            lines.append(f"   eBay price: ${ebay_price:.2f}{' (capped by median $' + str(ebay_median) + ')' if capped else ''}")
            lines.append(f"   {'✅' if margin >= MIN_MARGIN_PERCENT else '❌'} Margin: {margin:.1f}% (need {MIN_MARGIN_PERCENT:.0f}%)")

    await msg.edit_text("\n".join(lines))


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
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("research", cmd_research))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("listings", cmd_listings))
    app.add_handler(CommandHandler("orders", cmd_orders))
    app.add_handler(CommandHandler("syncprices", cmd_syncprices))
    app.add_handler(CommandHandler("dedupe", cmd_dedupe))
    app.add_handler(CommandHandler("recategorize", cmd_recategorize))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("lookup", cmd_lookup))
    app.add_handler(CommandHandler("fulfill", cmd_fulfill))
    app.add_handler(CommandHandler("markshipped", cmd_markshipped))
    app.add_handler(CommandHandler("setmargin", cmd_setmargin))
    app.add_handler(CommandHandler("setmarkup", cmd_setmarkup))
    app.add_handler(CommandHandler("setmax", cmd_setmax))
    app.add_handler(CommandHandler("profit", cmd_profit))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("failed", cmd_failed))
    app.add_handler(CommandHandler("retry", cmd_retry))
    app.add_handler(CommandHandler("addkeyword", cmd_addkeyword))
    app.add_handler(CommandHandler("removekeyword", cmd_removekeyword))
    app.add_handler(CommandHandler("keywords", cmd_keywords))
    app.add_handler(CommandHandler("addproduct", cmd_addproduct))
    app.add_handler(CommandHandler("checklinks", cmd_checklinks))
    app.add_handler(CommandHandler("trimlistings", cmd_trimlistings))
    app.add_handler(CommandHandler("diagnose", cmd_diagnose))

    return app
