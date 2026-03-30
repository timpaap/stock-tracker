"""
database.py — Stores and retrieves all portfolio data using SQLite
"""

import os
import sqlite3
from datetime import datetime


_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, "data", "portfolio.db")


def _ensure_dirs() -> None:
    os.makedirs(os.path.join(_HERE, "data"), exist_ok=True)
    os.makedirs(os.path.join(_HERE, "uploads"), exist_ok=True)


def _get_connection() -> sqlite3.Connection:
    _ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_if_needed(conn: sqlite3.Connection, cursor: sqlite3.Cursor) -> None:
    """
    Migrates from old schema (order_ref TEXT UNIQUE) to new schema
    (composite UNIQUE on order_ref, quantity, price) so that partial fills
    sharing the same order ID are no longer treated as duplicates.
    """
    cursor.execute("PRAGMA index_list(transactions)")
    indexes = cursor.fetchall()
    for idx in indexes:
        if idx["origin"] == "u" and idx["unique"]:
            cursor.execute(f"PRAGMA index_info({idx['name']})")
            cols = [row["name"] for row in cursor.fetchall()]
            if cols == ["order_ref"]:
                # Old single-column unique constraint found — migrate.
                cursor.execute("ALTER TABLE transactions RENAME TO transactions_old")
                cursor.execute("""
                    CREATE TABLE transactions (
                        id               INTEGER PRIMARY KEY AUTOINCREMENT,
                        order_ref        TEXT,
                        date             TEXT    NOT NULL,
                        time             TEXT,
                        name             TEXT    NOT NULL,
                        isin             TEXT    NOT NULL,
                        exchange         TEXT,
                        execution_venue  TEXT,
                        transaction_type TEXT    NOT NULL,
                        quantity         REAL    NOT NULL,
                        price            REAL,
                        local_value      REAL,
                        value_eur        REAL,
                        exchange_rate    REAL,
                        autofx_cost      REAL,
                        transaction_fee  REAL,
                        total_eur        REAL,
                        UNIQUE(order_ref, quantity, price)
                    )
                """)
                cursor.execute("""
                    INSERT INTO transactions
                        (order_ref, date, time, name, isin, exchange,
                         execution_venue, transaction_type, quantity, price,
                         local_value, value_eur, exchange_rate, autofx_cost,
                         transaction_fee, total_eur)
                    SELECT
                        order_ref, date, time, name, isin, exchange,
                        execution_venue, transaction_type, quantity, price,
                        local_value, value_eur, exchange_rate, autofx_cost,
                        transaction_fee, total_eur
                    FROM transactions_old
                """)
                cursor.execute("DROP TABLE transactions_old")
                conn.commit()
                break


def init_db() -> None:
    conn = _get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            order_ref        TEXT,
            date             TEXT    NOT NULL,
            time             TEXT,
            name             TEXT    NOT NULL,
            isin             TEXT    NOT NULL,
            exchange         TEXT,
            execution_venue  TEXT,
            transaction_type TEXT    NOT NULL,
            quantity         REAL    NOT NULL,
            price            REAL,
            local_value      REAL,
            value_eur        REAL,
            exchange_rate    REAL,
            autofx_cost      REAL,
            transaction_fee  REAL,
            total_eur        REAL,
            UNIQUE(order_ref, quantity, price)
        )
    """)

    _migrate_if_needed(conn, cursor)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            isin        TEXT PRIMARY KEY,
            name        TEXT,
            ticker      TEXT,
            price       REAL,
            currency    TEXT,
            fetched_at  TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dividends (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            date              TEXT    NOT NULL,
            isin              TEXT    NOT NULL,
            name              TEXT,
            currency_original TEXT,
            amount_original   REAL,
            gross_eur         REAL,
            tax_eur           REAL,
            net_eur           REAL    NOT NULL,
            UNIQUE(date, isin, amount_original, currency_original)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS asset_categories (
            isin         TEXT PRIMARY KEY,
            category     TEXT NOT NULL DEFAULT 'Unclassified',
            broad_region TEXT NOT NULL DEFAULT 'Unclassified'
        )
    """)

    # Migration: add broad_region column if it doesn't exist yet
    cursor.execute("PRAGMA table_info(asset_categories)")
    cols = [row["name"] for row in cursor.fetchall()]
    if "broad_region" not in cols:
        cursor.execute("ALTER TABLE asset_categories ADD COLUMN broad_region TEXT NOT NULL DEFAULT 'Unclassified'")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Asset categories (rebalancing)
# ---------------------------------------------------------------------------

def load_asset_categories() -> dict[str, str]:
    """Returns {isin: category} for all saved categorisations."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT isin, category FROM asset_categories")
    rows = cursor.fetchall()
    conn.close()
    return {row["isin"]: row["category"] for row in rows}


def load_broad_regions() -> dict[str, str]:
    """Returns {isin: broad_region} for all Broad ETF positions."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT isin, broad_region FROM asset_categories")
    rows = cursor.fetchall()
    conn.close()
    return {row["isin"]: row["broad_region"] for row in rows}


def save_asset_category(isin: str, category: str) -> None:
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO asset_categories (isin, category) VALUES (?, ?)",
        (isin, category),
    )
    conn.commit()
    conn.close()


def save_broad_region(isin: str, region: str) -> None:
    conn = _get_connection()
    cursor = conn.cursor()
    # Upsert: preserve existing category if row already exists
    cursor.execute(
        """
        INSERT INTO asset_categories (isin, category, broad_region)
        VALUES (?, COALESCE((SELECT category FROM asset_categories WHERE isin = ?), 'Unclassified'), ?)
        ON CONFLICT(isin) DO UPDATE SET broad_region = excluded.broad_region
        """,
        (isin, isin, region),
    )
    conn.commit()
    conn.close()


def load_setting(key: str, default: str | None = None) -> str | None:
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row["value"] if row else default


def save_setting(key: str, value: str) -> None:
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()
    conn.close()


def save_transactions(transactions: list[dict]) -> tuple[int, int]:
    conn = _get_connection()
    cursor = conn.cursor()

    saved = 0
    skipped = 0

    for t in transactions:
        try:
            cursor.execute("""
                INSERT INTO transactions (
                    order_ref, date, time, name, isin, exchange, execution_venue,
                    transaction_type, quantity, price, local_value, value_eur,
                    exchange_rate, autofx_cost, transaction_fee, total_eur
                ) VALUES (
                    :order_ref, :date, :time, :name, :isin, :exchange, :execution_venue,
                    :transaction_type, :quantity, :price, :local_value, :value_eur,
                    :exchange_rate, :autofx_cost, :transaction_fee, :total_eur
                )
            """, {
                "order_ref":        t.get("order_ref"),
                "date":             str(t.get("date")),
                "time":             t.get("time"),
                "name":             t.get("name"),
                "isin":             t.get("isin"),
                "exchange":         t.get("exchange"),
                "execution_venue":  t.get("execution_venue"),
                "transaction_type": t.get("transaction_type"),
                "quantity":         t.get("quantity"),
                "price":            t.get("price"),
                "local_value":      t.get("local_value"),
                "value_eur":        t.get("value_eur"),
                "exchange_rate":    t.get("exchange_rate"),
                "autofx_cost":      t.get("autofx_cost"),
                "transaction_fee":  t.get("transaction_fee"),
                "total_eur":        t.get("total_eur"),
            })
            saved += 1
        except sqlite3.IntegrityError:
            skipped += 1

    conn.commit()
    conn.close()
    return saved, skipped


def load_transactions() -> list[dict]:
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM transactions ORDER BY date ASC, time ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def load_transactions_for_isin(isin: str) -> list[dict]:
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM transactions WHERE isin = ? ORDER BY date ASC, time ASC",
        (isin,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_all_isins() -> list[dict]:
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT isin, name
        FROM transactions
        ORDER BY name ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def save_price(isin: str, name: str, ticker: str, price: float, currency: str) -> None:
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO prices (isin, name, ticker, price, currency, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (isin, name, ticker, price, currency, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()


def load_prices() -> dict[str, dict]:
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM prices")
    rows = cursor.fetchall()
    conn.close()
    return {row["isin"]: dict(row) for row in rows}


def save_dividends(dividends: list[dict]) -> tuple[int, int]:
    """Insert new dividend records; skip exact duplicates. Returns (saved, skipped)."""
    conn = _get_connection()
    cursor = conn.cursor()
    saved = 0
    skipped = 0
    for d in dividends:
        try:
            cursor.execute("""
                INSERT INTO dividends
                    (date, isin, name, currency_original, amount_original,
                     gross_eur, tax_eur, net_eur)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d.get("date"), d.get("isin"), d.get("name"),
                d.get("currency_original"), d.get("amount_original"),
                d.get("gross_eur"), d.get("tax_eur"), d.get("net_eur"),
            ))
            saved += 1
        except sqlite3.IntegrityError:
            skipped += 1
    conn.commit()
    conn.close()
    return saved, skipped


def load_dividends() -> list[dict]:
    """Return all dividend records ordered by date."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM dividends ORDER BY date ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def load_dividends_by_isin() -> dict[str, float]:
    """Return a mapping of isin → total net_eur dividends received."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT isin, SUM(net_eur) AS total
        FROM dividends
        GROUP BY isin
    """)
    rows = cursor.fetchall()
    conn.close()
    return {row["isin"]: row["total"] for row in rows}
