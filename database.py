import sqlite3
from contextlib import contextmanager
from datetime import datetime

DB_PATH = "dropship.db"


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
                walmart_url TEXT UNIQUE NOT NULL,
                walmart_item_id TEXT,
                title TEXT NOT NULL,
                walmart_price REAL NOT NULL,
                ebay_price REAL NOT NULL,
                margin_percent REAL NOT NULL,
                category TEXT,
                image_url TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                ebay_item_id TEXT UNIQUE NOT NULL,
                ebay_price REAL NOT NULL,
                walmart_price REAL NOT NULL,
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
                walmart_price REAL,
                profit REAL,
                status TEXT DEFAULT 'pending',
                walmart_order_id TEXT,
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
        """)


# ── Products ──────────────────────────────────────────────────────────────────

def upsert_product(walmart_url, walmart_item_id, title, walmart_price,
                   ebay_price, margin_percent, category="", image_url="") -> int:
    with get_db() as db:
        existing = db.execute(
            "SELECT id FROM products WHERE walmart_url = ?", (walmart_url,)
        ).fetchone()
        if existing:
            db.execute(
                """UPDATE products SET walmart_price=?, ebay_price=?, margin_percent=?,
                   updated_at=datetime('now') WHERE walmart_url=?""",
                (walmart_price, ebay_price, margin_percent, walmart_url)
            )
            return existing["id"]
        cur = db.execute(
            """INSERT INTO products (walmart_url, walmart_item_id, title, walmart_price,
               ebay_price, margin_percent, category, image_url, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready')""",
            (walmart_url, walmart_item_id, title, walmart_price, ebay_price,
             margin_percent, category, image_url)
        )
        return cur.lastrowid


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

def save_listing(product_id, ebay_item_id, ebay_price, walmart_price):
    with get_db() as db:
        db.execute(
            """INSERT OR REPLACE INTO listings (product_id, ebay_item_id, ebay_price, walmart_price)
               VALUES (?, ?, ?, ?)""",
            (product_id, ebay_item_id, ebay_price, walmart_price)
        )


def get_active_listings() -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            """SELECT l.*, p.title, p.walmart_url, p.walmart_item_id
               FROM listings l JOIN products p ON l.product_id = p.id
               WHERE l.status='active'"""
        ).fetchall()
        return [dict(r) for r in rows]


def update_listing_price(ebay_item_id, new_ebay_price, new_walmart_price):
    with get_db() as db:
        db.execute(
            "UPDATE listings SET ebay_price=?, walmart_price=? WHERE ebay_item_id=?",
            (new_ebay_price, new_walmart_price, ebay_item_id)
        )


def deactivate_listing(ebay_item_id):
    with get_db() as db:
        db.execute("UPDATE listings SET status='ended' WHERE ebay_item_id=?", (ebay_item_id,))


# ── Orders ────────────────────────────────────────────────────────────────────

def save_order(ebay_order_id, listing_id, buyer_name, address, city, state,
               zip_code, country, sale_price, walmart_price) -> int:
    profit = sale_price * (1 - 0.129) - walmart_price
    with get_db() as db:
        existing = db.execute(
            "SELECT id FROM orders WHERE ebay_order_id=?", (ebay_order_id,)
        ).fetchone()
        if existing:
            return existing["id"]
        cur = db.execute(
            """INSERT INTO orders (ebay_order_id, listing_id, buyer_name, buyer_address,
               buyer_city, buyer_state, buyer_zip, buyer_country, sale_price,
               walmart_price, profit, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (ebay_order_id, listing_id, buyer_name, address, city, state,
             zip_code, country, sale_price, walmart_price, profit)
        )
        return cur.lastrowid


def get_pending_orders() -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM orders WHERE status='pending' ORDER BY created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def mark_order_fulfilled(order_id, walmart_order_id, tracking=""):
    with get_db() as db:
        db.execute(
            """UPDATE orders SET status='fulfilled', walmart_order_id=?,
               tracking_number=?, fulfilled_at=datetime('now') WHERE id=?""",
            (walmart_order_id, tracking, order_id)
        )


def mark_order_failed(order_id, reason=""):
    with get_db() as db:
        db.execute(
            "UPDATE orders SET status='failed' WHERE id=?", (order_id,)
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
        total_orders = db.execute("SELECT COUNT(*) as c FROM orders").fetchone()["c"]
        fulfilled = db.execute("SELECT COUNT(*) as c FROM orders WHERE status='fulfilled'").fetchone()["c"]
        active_listings = db.execute("SELECT COUNT(*) as c FROM listings WHERE status='active'").fetchone()["c"]
        pending_orders = db.execute("SELECT COUNT(*) as c FROM orders WHERE status='pending'").fetchone()["c"]
        return {
            "total_profit": round(total_profit, 2),
            "today_profit": round(today_profit, 2),
            "total_orders": total_orders,
            "fulfilled_orders": fulfilled,
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
