"""
transaction_parser.py — Reads DeGiro Excel transaction exports

DeGiro exports transactions as .xlsx files with Dutch column headers.
This module parses those files into a list of standardised dicts that
the rest of the app can work with.

Named transaction_parser instead of parser to avoid shadowing
Python's built-in 'parser' module.
"""

import os
import pandas as pd


# Maps Dutch DeGiro column names to our internal English field names.
# DeGiro adds a trailing space to 'Koers' in some exports — we strip all
# header whitespace before mapping to handle this safely.
COLUMN_MAP = {
    "Datum":              "date",
    "Tijd":               "time",
    "Product":            "name",
    "ISIN":               "isin",
    "Beurs":              "exchange",
    "Uitvoeringsplaats":  "execution_venue",
    "Aantal":             "quantity_raw",   # positive=BUY, negative=SELL
    "Koers":              "price",
    "Lokale waarde":      "local_value",
    "Waarde":             "value_eur",
    "Wisselkoers":        "exchange_rate",
    "Transactiekosten":   "transaction_fee",
    "Totaal":             "total_eur",
    "Order ID":           "order_ref",
}


def parse_transactions(file_path: str) -> list[dict]:
    """
    Parses a single DeGiro Excel export file.

    Parameters:
        file_path: path to the .xlsx file

    Returns:
        A list of transaction dicts, one per row.
        Returns an empty list if the file cannot be parsed.
    """
    try:
        df = pd.read_excel(file_path, header=0)
    except Exception as e:
        print(f"  Error reading {os.path.basename(file_path)}: {e}")
        return []

    # Strip whitespace from all column names (handles 'Koers ' trailing space)
    df.columns = [str(c).strip() for c in df.columns]

    # DeGiro has exported two different column-name formats over the years:
    #   old: "Waarde", "Totaal", "Transactiekosten"
    #   new: "Waarde EUR", "Totaal EUR", "Transactiekosten en/of kosten van derden EUR"
    # Build a prefix-lookup so both formats resolve to the right pandas column.
    col_names = list(df.columns)

    def _resolve(prefix: str) -> str | None:
        """Return the first column whose name starts with `prefix`, or None."""
        for c in col_names:
            if c == prefix or c.startswith(prefix + " ") or c.startswith(prefix + " "):
                return c
        return None

    COL_WAARDE      = _resolve("Waarde")       or "Waarde"
    COL_TOTAAL      = _resolve("Totaal")       or "Totaal"
    COL_TRANSACTIE  = _resolve("Transactiekosten") or "Transactiekosten"
    COL_AUTOFX      = _resolve("AutoFX")       # None if absent

    # Identify the last column — DeGiro puts the order UUID there,
    # but it has no header (shows as 'Unnamed: N')
    last_col = df.columns[-1]

    transactions = []

    for _, row in df.iterrows():
        # Skip rows that have no ISIN (header repeats, totals rows, etc.)
        isin = str(row.get("ISIN", "")).strip()
        if not isin or isin == "nan" or isin == "ISIN":
            continue

        # Extract order reference (UUID) from the last unnamed column
        order_ref = str(row.get(last_col, "")).strip()
        if order_ref == "nan":
            order_ref = None

        # Quantity: positive = BUY, negative = SELL
        qty_raw = row.get("Aantal", 0)
        try:
            qty_raw = float(qty_raw)
        except (ValueError, TypeError):
            qty_raw = 0.0

        transaction_type = "BUY" if qty_raw >= 0 else "SELL"
        quantity = abs(qty_raw)

        def _float(col: str) -> float | None:
            if col is None:
                return None
            val = row.get(col)
            try:
                return float(val) if val is not None and str(val) != "nan" else None
            except (ValueError, TypeError):
                return None

        def _str(col: str) -> str | None:
            if col is None:
                return None
            val = row.get(col)
            s = str(val).strip()
            return s if s and s != "nan" else None

        # Parse date — DeGiro uses DD-MM-YYYY format
        date_raw = _str("Datum")
        date_iso = None
        if date_raw:
            try:
                date_iso = pd.to_datetime(date_raw, dayfirst=True).strftime("%Y-%m-%d")
            except Exception:
                date_iso = date_raw

        # Parse time
        time_raw = _str("Tijd")
        time_fmt = None
        if time_raw:
            # Keep only HH:MM
            time_fmt = str(time_raw)[:5]

        transactions.append({
            "order_ref":        order_ref,
            "date":             date_iso,
            "time":             time_fmt,
            "name":             _str("Product"),
            "isin":             isin,
            "exchange":         _str("Beurs"),
            "execution_venue":  _str("Uitvoeringsplaats"),
            "transaction_type": transaction_type,
            "quantity":         quantity,
            "price":            _float("Koers"),
            "local_value":      _float("Lokale waarde"),
            "value_eur":        _float(COL_WAARDE),
            "exchange_rate":    _float("Wisselkoers"),
            "autofx_cost":      _float(COL_AUTOFX),
            "transaction_fee":  _float(COL_TRANSACTIE),
            "total_eur":        _float(COL_TOTAAL),
        })

    return transactions


def parse_all_uploads(upload_folder: str) -> list[dict]:
    """
    Parses all .xlsx files in the given folder and returns combined transactions,
    deduplicated by order_ref (UUID).

    Parameters:
        upload_folder: path to the folder containing DeGiro .xlsx exports

    Returns:
        A deduplicated list of transaction dicts, sorted by date.
    """
    if not os.path.isdir(upload_folder):
        return []

    xlsx_files = sorted(
        f for f in os.listdir(upload_folder)
        if f.lower().endswith(".xlsx")
    )

    # Dedup key: (order_ref, quantity, price) so that partial fills sharing
    # the same order ID (but with different quantities/prices) are kept,
    # while true duplicates from uploading the same file twice are dropped.
    seen_keys: set[tuple] = set()
    all_transactions: list[dict] = []

    for filename in xlsx_files:
        path = os.path.join(upload_folder, filename)
        print(f"Parsing: {filename}")
        txs = parse_transactions(path)

        added = 0
        for t in txs:
            ref = t.get("order_ref")
            if ref:
                key = (ref, t.get("quantity"), t.get("price"))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
            all_transactions.append(t)
            added += 1

        print(f"  → {added} transaction(s) added")

    print(f"\nTotal transactions loaded: {len(all_transactions)}")
    return all_transactions
