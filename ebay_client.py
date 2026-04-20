import asyncio
import logging
import re
import statistics
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from config import EBAY_EMAIL, EBAY_PASSWORD, EBAY_APP_ID, EBAY_FINDING_URL

logger = logging.getLogger(__name__)

_browser_context = None


async def _get_context():
    global _browser_context
    if _browser_context is None:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        _browser_context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        await _login(_browser_context)
    return _browser_context


async def _login(context):
    logger.info("Logging into eBay...")
    page = await context.new_page()
    await page.goto("https://www.ebay.com/signin/", wait_until="networkidle")
    await page.fill('input#userid', EBAY_EMAIL)
    await page.click('button#signin-continue-btn')
    await page.wait_for_timeout(1500)
    await page.fill('input#pass', EBAY_PASSWORD)
    await page.click('button#sgnBt')
    await page.wait_for_url("https://www.ebay.com/**", timeout=15000)
    await page.close()
    logger.info("eBay login successful.")


# ── Research (eBay Finding API) ───────────────────────────────────────────────

def get_sold_median(keyword: str) -> float | None:
    params = {
        "OPERATION-NAME": "findCompletedItems",
        "SERVICE-VERSION": "1.0.0",
        "SECURITY-APPNAME": EBAY_APP_ID,
        "RESPONSE-DATA-FORMAT": "JSON",
        "REST-PAYLOAD": "",
        "keywords": keyword,
        "itemFilter(0).name": "SoldItemsOnly",
        "itemFilter(0).value": "true",
        "paginationInput.entriesPerPage": "100",
    }
    try:
        resp = requests.get(EBAY_FINDING_URL, params=params, timeout=20)
        data = resp.json()

        if "errorMessage" in data:
            msg = data["errorMessage"][0]["error"][0]["message"][0]
            logger.error(f"eBay API error for '{keyword}': {msg}")
            return None

        items = (
            data["findCompletedItemsResponse"][0]
                ["searchResult"][0]
                .get("item", [])
        )
        prices = []
        for item in items:
            try:
                p = float(item["sellingStatus"][0]["convertedCurrentPrice"][0]["__value__"])
                if p > 0:
                    prices.append(p)
            except (KeyError, IndexError, ValueError):
                continue

        if len(prices) < 5:
            logger.info(f"Not enough sold data for '{keyword}' ({len(prices)} results)")
            return None

        median = statistics.median(prices)
        logger.info(f"'{keyword}': median sold=${median:.2f} from {len(prices)} listings")
        return median
    except Exception as e:
        logger.error(f"eBay API error for '{keyword}': {e}")
        return None


# ── Listing (browser automation) ──────────────────────────────────────────────

async def create_listing(title: str, description: str, price: float,
                         image_url: str, category_id: str = None) -> str | None:
    context = await _get_context()
    page = await context.new_page()
    try:
        logger.info(f"Creating listing: {title[:50]}")
        await page.goto("https://www.ebay.com/sell/", wait_until="networkidle")
        await page.wait_for_timeout(1000)

        # Fill title
        title_input = page.locator('input[placeholder*="title" i], input[name*="title" i]')
        await title_input.first.fill(title[:80])
        await page.wait_for_timeout(500)

        # Try to find category suggestion button
        try:
            get_started = page.locator('button:has-text("Get started"), button:has-text("List it")')
            if await get_started.count() > 0:
                await get_started.first.click()
                await page.wait_for_timeout(2000)
        except PWTimeout:
            pass

        # Set price
        try:
            price_input = page.locator('input[placeholder*"price" i], input[aria-label*="price" i]')
            if await price_input.count() > 0:
                await price_input.first.clear()
                await price_input.first.fill(f"{price:.2f}")
        except Exception:
            pass

        # Set quantity to 99
        try:
            qty = page.locator('input[aria-label*="quantity" i], input[name*="quantity" i]')
            if await qty.count() > 0:
                await qty.first.clear()
                await qty.first.fill("99")
        except Exception:
            pass

        # Submit / List item
        list_btn = page.locator(
            'button:has-text("List item"), button:has-text("Submit listing"), '
            'button:has-text("Post your listing")'
        )
        await list_btn.first.click(timeout=10000)
        await page.wait_for_timeout(3000)

        # Extract item ID from confirmation URL or page
        item_id = None
        url_match = re.search(r'/(\d{12,14})', page.url)
        if url_match:
            item_id = url_match.group(1)
        else:
            content = await page.content()
            id_match = re.search(r'item(?:Id|ID|Number)["\s:]+(\d{10,14})', content)
            if id_match:
                item_id = id_match.group(1)

        if item_id:
            logger.info(f"Listed: eBay item {item_id}")
        else:
            logger.warning("Listed but could not extract item ID")

        return item_id

    except Exception as e:
        logger.error(f"Listing error: {e}")
        return None
    finally:
        await page.close()


async def revise_price(ebay_item_id: str, new_price: float) -> bool:
    context = await _get_context()
    page = await context.new_page()
    try:
        await page.goto(
            f"https://www.ebay.com/itm/edit/{ebay_item_id}",
            wait_until="networkidle"
        )
        price_input = page.locator('input[aria-label*="price" i]')
        await price_input.first.clear()
        await price_input.first.fill(f"{new_price:.2f}")
        await page.locator('button:has-text("Save"), button:has-text("Update")').first.click()
        await page.wait_for_timeout(2000)
        return True
    except Exception as e:
        logger.error(f"Revise price error: {e}")
        return False
    finally:
        await page.close()


async def end_listing(ebay_item_id: str) -> bool:
    context = await _get_context()
    page = await context.new_page()
    try:
        await page.goto(
            f"https://www.ebay.com/itm/end/{ebay_item_id}",
            wait_until="networkidle"
        )
        end_btn = page.locator('button:has-text("End listing"), input[value="End listing"]')
        await end_btn.first.click(timeout=8000)
        await page.wait_for_timeout(2000)
        return True
    except Exception as e:
        logger.error(f"End listing error: {e}")
        return False
    finally:
        await page.close()


async def get_new_orders(days_back: int = 1) -> list[dict]:
    context = await _get_context()
    page = await context.new_page()
    orders = []
    try:
        await page.goto(
            "https://www.ebay.com/mys/active/orders?filter=status:AWAITING_SHIPMENT",
            wait_until="networkidle"
        )
        await page.wait_for_timeout(2000)

        order_rows = page.locator('[data-test-id="order-card"], .order-card, [class*="order-row"]')
        count = await order_rows.count()

        for i in range(count):
            try:
                row = order_rows.nth(i)
                row_text = await row.inner_text()

                order_id_match = re.search(r'(?:Order|#)\s*(\d{12,20})', row_text)
                price_match = re.search(r'\$(\d+\.?\d*)', row_text)
                item_link = row.locator('a[href*="/itm/"]')
                item_id = ""
                if await item_link.count() > 0:
                    href = await item_link.first.get_attribute("href")
                    id_match = re.search(r'/(\d{10,14})', href or "")
                    if id_match:
                        item_id = id_match.group(1)

                if order_id_match and price_match:
                    orders.append({
                        "order_id": order_id_match.group(1),
                        "ebay_item_id": item_id,
                        "sale_price": float(price_match.group(1)),
                        "buyer_name": "",
                        "address": "",
                        "city": "",
                        "state": "",
                        "zip": "",
                        "country": "US",
                    })
            except Exception:
                continue

        # Get full buyer details for each order
        for order in orders:
            try:
                await _fill_buyer_details(context, order)
            except Exception as e:
                logger.debug(f"Could not get buyer details for order {order['order_id']}: {e}")

    except Exception as e:
        logger.error(f"Get orders error: {e}")
    finally:
        await page.close()

    return orders


async def _fill_buyer_details(context, order: dict):
    page = await context.new_page()
    try:
        await page.goto(
            f"https://www.ebay.com/mys/transaction?transId={order['order_id']}",
            wait_until="networkidle"
        )
        content = await page.content()

        name_match = re.search(r'Ship to[^<]*<[^>]+>([^<]{5,60})</\s*\w+>', content)
        if name_match:
            order["buyer_name"] = name_match.group(1).strip()

        addr_match = re.search(r'(\d+\s+[\w\s]+(?:St|Ave|Rd|Blvd|Dr|Ln|Way|Ct|Pl)[.,]?[^<]{0,40})', content)
        if addr_match:
            order["address"] = addr_match.group(1).strip()

        city_state_zip = re.search(r'([A-Za-z\s]+),\s*([A-Z]{2})\s+(\d{5})', content)
        if city_state_zip:
            order["city"] = city_state_zip.group(1).strip()
            order["state"] = city_state_zip.group(2)
            order["zip"] = city_state_zip.group(3)
    finally:
        await page.close()
