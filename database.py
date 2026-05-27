import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from config import EBAY_FEE_PERCENT

DB_PATH = os.getenv("DB_PATH", "dropship.db")


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cj_url TEXT UNIQUE NOT NULL,
                cj_variant_id TEXT,
                title TEXT NOT NULL,
                cj_price REAL NOT NULL,
                ebay_price REAL NOT NULL,
                margin_percent REAL NOT NULL,
                category TEXT,
                image_url TEXT,
                extra_images TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                ebay_item_id TEXT UNIQUE NOT NULL,
                ebay_price REAL NOT NULL,
                cj_price REAL NOT NULL,
                status TEXT DEFAULT 'active',
                views INTEGER DEFAULT 0,
                listed_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(product_id) REFERENCES products(id)
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ebay_order_id TEXT UNIQUE NOT NULL,
                listing_id INTEGER,
                buyer_name TEXT,
                buyer_address TEXT,
                buyer_city TEXT,
                buyer_state TEXT,
                buyer_zip TEXT,
                buyer_country TEXT DEFAULT 'US',
                sale_price REAL,
                cj_price REAL,
                profit REAL,
                status TEXT DEFAULT 'pending',
                cj_order_id TEXT,
                tracking_number TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                fulfilled_at TEXT,
                FOREIGN KEY(listing_id) REFERENCES listings(id)
            );

            CREATE TABLE IF NOT EXISTS profit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                sale_price REAL NOT NULL,
                cost REAL NOT NULL,
                ebay_fee REAL NOT NULL,
                net_profit REAL NOT NULL,
                logged_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(order_id) REFERENCES orders(id)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT UNIQUE NOT NULL,
                active INTEGER DEFAULT 1,
                added_at TEXT DEFAULT (datetime('now'))
            );
        """)
        db.executescript("""
            CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status);
            CREATE INDEX IF NOT EXISTS idx_listings_ebay_item_id ON listings(ebay_item_id);
            CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
            CREATE INDEX IF NOT EXISTS idx_orders_ebay_order_id ON orders(ebay_order_id);
            CREATE INDEX IF NOT EXISTS idx_products_status ON products(status);
        """)

        # Safe migrations for existing databases
        for migration in [
            "ALTER TABLE products ADD COLUMN extra_images TEXT DEFAULT ''",
            "ALTER TABLE products RENAME COLUMN walmart_url TO cj_url",
            "ALTER TABLE products RENAME COLUMN walmart_item_id TO cj_variant_id",
            "ALTER TABLE products RENAME COLUMN walmart_price TO cj_price",
            "ALTER TABLE listings RENAME COLUMN walmart_price TO cj_price",
            "ALTER TABLE orders RENAME COLUMN walmart_price TO cj_price",
            "ALTER TABLE orders RENAME COLUMN walmart_order_id TO cj_order_id",
            "ALTER TABLE orders ADD COLUMN failure_reason TEXT DEFAULT ''",
        ]:
            try:
                db.execute(migration)
            except Exception:
                pass


# ── Products ──────────────────────────────────────────────────────────────────

def upsert_product(cj_url, cj_variant_id, title, cj_price,
                   ebay_price, margin_percent, category="", image_url="",
                   extra_images="") -> dict:
    """Returns {"id": int, "ready": bool} — ready=False means already listed on eBay."""
    with get_db() as db:
        existing = db.execute(
            "SELECT id, status FROM products WHERE cj_url = ?", (cj_url,)
        ).fetchone()
        # Also catch post-reset synced placeholders matched by CJ variant ID
        if not existing and cj_variant_id:
            existing = db.execute(
                "SELECT id, status FROM products WHERE cj_variant_id = ? AND status = 'listed'",
                (cj_variant_id,)
            ).fetchone()
        if existing:
            already_listed = existing["status"] == "listed"
            new_status = "listed" if already_listed else "ready"
            db.execute(
                """UPDATE products SET cj_price=?, ebay_price=?, margin_percent=?,
                   image_url=?, extra_images=?, status=?, updated_at=datetime('now')
                   WHERE cj_url=?""",
                (cj_price, ebay_price, margin_percent, image_url, extra_images,
                 new_status, cj_url)
            )
            return {"id": existing["id"], "ready": not already_listed}
        cur = db.execute(
            """INSERT INTO products (cj_url, cj_variant_id, title, cj_price,
               ebay_price, margin_percent, category, image_url, extra_images, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready')""",
            (cj_url, cj_variant_id, title, cj_price, ebay_price,
             margin_percent, category, image_url, extra_images)
        )
        return {"id": cur.lastrowid, "ready": True}


def update_product_variant(product_id: int, cj_variant_id: str):
    with get_db() as db:
        db.execute(
            "UPDATE products SET cj_variant_id=? WHERE id=?",
            (cj_variant_id, product_id)
        )


def get_ready_products(limit=10) -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM products WHERE status='ready' ORDER BY margin_percent DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def mark_product_listed(product_id: int):
    with get_db() as db:
        db.execute("UPDATE products SET status='listed' WHERE id=?", (product_id,))


def get_all_products(status=None) -> list[dict]:
    with get_db() as db:
        if status:
            rows = db.execute("SELECT * FROM products WHERE status=?", (status,)).fetchall()
        else:
            rows = db.execute("SELECT * FROM products ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


# ── Listings ──────────────────────────────────────────────────────────────────

def save_listing(product_id, ebay_item_id, ebay_price, cj_price):
    with get_db() as db:
        db.execute(
            """INSERT OR REPLACE INTO listings (product_id, ebay_item_id, ebay_price, cj_price)
               VALUES (?, ?, ?, ?)""",
            (product_id, ebay_item_id, ebay_price, cj_price)
        )


def get_active_listings() -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            """SELECT l.*, p.title, p.cj_url, p.cj_variant_id
               FROM listings l JOIN products p ON l.product_id = p.id
               WHERE l.status='active'"""
        ).fetchall()
        return [dict(r) for r in rows]


def update_listing_price(ebay_item_id, new_ebay_price, new_cj_price):
    with get_db() as db:
        db.execute(
            "UPDATE listings SET ebay_price=?, cj_price=? WHERE ebay_item_id=?",
            (new_ebay_price, new_cj_price, ebay_item_id)
        )


def sync_ebay_listing(ebay_item_id: str, title: str, ebay_price: float,
                      cj_variant_id: str = "") -> bool:
    """Restore a listing to the DB after a reset. Returns True if newly added."""
    with get_db() as db:
        if db.execute("SELECT id FROM listings WHERE ebay_item_id=?",
                      (ebay_item_id,)).fetchone():
            return False
        row = db.execute("SELECT id FROM products WHERE title=?", (title,)).fetchone()
        if row:
            product_id = row["id"]
            db.execute("UPDATE products SET status='listed' WHERE id=?", (product_id,))
        else:
            cur = db.execute(
                """INSERT INTO products (cj_url, cj_variant_id, title,
                   cj_price, ebay_price, margin_percent, status)
                   VALUES (?, ?, ?, 0, ?, 0, 'listed')""",
                (f"ebay_sync:{ebay_item_id}", cj_variant_id, title, ebay_price)
            )
            product_id = cur.lastrowid
        db.execute(
            """INSERT OR IGNORE INTO listings
               (product_id, ebay_item_id, ebay_price, cj_price)
               VALUES (?, ?, ?, 0)""",
            (product_id, ebay_item_id, ebay_price)
        )
        return True


def deactivate_listing(ebay_item_id):
    with get_db() as db:
        db.execute("UPDATE listings SET status='ended' WHERE ebay_item_id=?", (ebay_item_id,))


# ── Orders ────────────────────────────────────────────────────────────────────

def save_order(ebay_order_id, listing_id, buyer_name, address, city, state,
               zip_code, country, sale_price, cj_price) -> int:
    profit = sale_price * (1 - EBAY_FEE_PERCENT / 100) - cj_price
    with get_db() as db:
        existing = db.execute(
            "SELECT id, status FROM orders WHERE ebay_order_id=?", (ebay_order_id,)
        ).fetchone()
        if existing:
            if existing["status"] == "failed":
                db.execute("UPDATE orders SET status='pending' WHERE id=?", (existing["id"],))
            return existing["id"]
        cur = db.execute(
            """INSERT INTO orders (ebay_order_id, listing_id, buyer_name, buyer_address,
               buyer_city, buyer_state, buyer_zip, buyer_country, sale_price,
               cj_price, profit, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (ebay_order_id, listing_id, buyer_name, address, city, state,
             zip_code, country, sale_price, cj_price, profit)
        )
        return cur.lastrowid


def get_pending_orders() -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM orders WHERE status='pending' ORDER BY created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_fulfilled_without_tracking() -> list[dict]:
    """Returns fulfilled orders that have a CJ order ID but no tracking number yet."""
    with get_db() as db:
        rows = db.execute(
            """SELECT * FROM orders
               WHERE status='fulfilled' AND cj_order_id != '' AND cj_order_id IS NOT NULL
               AND (tracking_number IS NULL OR tracking_number = '')
               ORDER BY fulfilled_at ASC"""
        ).fetchall()
        return [dict(r) for r in rows]


def save_tracking(order_id: int, tracking_number: str):
    with get_db() as db:
        db.execute(
            "UPDATE orders SET tracking_number=? WHERE id=?",
            (tracking_number, order_id)
        )


def get_order_by_ebay_id(ebay_order_id: str) -> dict | None:
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM orders WHERE ebay_order_id=?", (ebay_order_id,)
        ).fetchone()
        return dict(row) if row else None


def mark_order_fulfilled(order_id, cj_order_id, tracking=""):
    with get_db() as db:
        db.execute(
            """UPDATE orders SET status='fulfilled', cj_order_id=?,
               tracking_number=?, fulfilled_at=datetime('now') WHERE id=?""",
            (cj_order_id, tracking, order_id)
        )


def mark_order_failed(order_id, reason=""):
    with get_db() as db:
        db.execute(
            "UPDATE orders SET status='failed', failure_reason=? WHERE id=?",
            (reason, order_id)
        )


def log_profit(order_id, sale_price, cost, ebay_fee, net_profit):
    with get_db() as db:
        db.execute(
            """INSERT OR IGNORE INTO profit_log (order_id, sale_price, cost, ebay_fee, net_profit)
               VALUES (?, ?, ?, ?, ?)""",
            (order_id, sale_price, cost, ebay_fee, net_profit)
        )


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    with get_db() as db:
        total_profit = db.execute(
            "SELECT COALESCE(SUM(net_profit), 0) as total FROM profit_log"
        ).fetchone()["total"]
        today_profit = db.execute(
            "SELECT COALESCE(SUM(net_profit), 0) as total FROM profit_log WHERE date(logged_at)=date('now')"
        ).fetchone()["total"]
        week_profit = db.execute(
            "SELECT COALESCE(SUM(net_profit), 0) as total FROM profit_log WHERE date(logged_at) >= date('now', '-7 days')"
        ).fetchone()["total"]
        month_profit = db.execute(
            "SELECT COALESCE(SUM(net_profit), 0) as total FROM profit_log WHERE date(logged_at) >= date('now', '-30 days')"
        ).fetchone()["total"]
        avg_profit = db.execute(
            "SELECT COALESCE(AVG(net_profit), 0) as avg FROM profit_log"
        ).fetchone()["avg"]
        total_orders = db.execute("SELECT COUNT(*) as c FROM orders").fetchone()["c"]
        fulfilled = db.execute("SELECT COUNT(*) as c FROM orders WHERE status='fulfilled'").fetchone()["c"]
        failed = db.execute("SELECT COUNT(*) as c FROM orders WHERE status='failed'").fetchone()["c"]
        active_listings = db.execute("SELECT COUNT(*) as c FROM listings WHERE status='active'").fetchone()["c"]
        pending_orders = db.execute("SELECT COUNT(*) as c FROM orders WHERE status='pending'").fetchone()["c"]
        return {
            "total_profit": round(total_profit, 2),
            "today_profit": round(today_profit, 2),
            "week_profit": round(week_profit, 2),
            "month_profit": round(month_profit, 2),
            "avg_profit": round(avg_profit, 2),
            "total_orders": total_orders,
            "fulfilled_orders": fulfilled,
            "failed_orders": failed,
            "active_listings": active_listings,
            "pending_orders": pending_orders,
        }


# ── Settings ──────────────────────────────────────────────────────────────────

def get_setting(key, default=None):
    with get_db() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with get_db() as db:
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))


# ── Keywords ──────────────────────────────────────────────────────────────────

def get_keywords() -> list[str]:
    with get_db() as db:
        rows = db.execute(
            "SELECT keyword FROM keywords WHERE active=1 ORDER BY added_at ASC"
        ).fetchall()
        return [r["keyword"] for r in rows]


def add_keyword(keyword: str) -> bool:
    """Returns True if newly added, False if already exists."""
    kw = keyword.lower().strip()
    with get_db() as db:
        existing = db.execute("SELECT id, active FROM keywords WHERE keyword=?", (kw,)).fetchone()
        if existing:
            if not existing["active"]:
                db.execute("UPDATE keywords SET active=1 WHERE keyword=?", (kw,))
            return False
        db.execute("INSERT INTO keywords (keyword) VALUES (?)", (kw,))
        return True


def remove_keyword(keyword: str) -> bool:
    """Returns True if found and removed."""
    with get_db() as db:
        result = db.execute("DELETE FROM keywords WHERE keyword=?", (keyword.lower().strip(),))
        return result.rowcount > 0


# ── Order history ─────────────────────────────────────────────────────────────

def get_failed_orders() -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            """SELECT o.*, p.title FROM orders o
               LEFT JOIN listings l ON o.listing_id = l.id
               LEFT JOIN products p ON l.product_id = p.id
               WHERE o.status='failed'
               ORDER BY o.created_at DESC LIMIT 20"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_fulfilled_orders(limit: int = 10) -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            """SELECT o.*, p.title, pl.net_profit FROM orders o
               LEFT JOIN listings l ON o.listing_id = l.id
               LEFT JOIN products p ON l.product_id = p.id
               LEFT JOIN profit_log pl ON pl.order_id = o.id
               WHERE o.status='fulfilled'
               ORDER BY o.fulfilled_at DESC LIMIT ?""",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
