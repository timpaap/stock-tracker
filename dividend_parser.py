"""
dividend_parser.py — Parses DeGiro account mutations exports to extract dividend income.

Exact column layout confirmed from DeGiro's mutations export:
  Datum, Tijd, Valutadatum, Product, ISIN, Omschrijving, FX, Mutatie, Unnamed: 8, ...
  - Omschrijving : row-type description
  - Mutatie      : currency code (EUR / USD / ...)
  - Unnamed: 8   : signed amount in that currency
  - FX           : exchange rate in foreign-currency units per EUR
                   (only populated on "Valuta Debitering" rows)

Foreign-currency dividend flow (example: USD):
  1. "Dividend"           row  – USD amount > 0, ISIN present, FX = NaN
  2. "Valuta Debitering"  row  – total USD deducted (negative), FX = <rate>  (USD/EUR)
  3. "Valuta Creditering" row  – EUR deposited

  EUR conversion: gross_eur = usd_amount / fx_rate
  (verified: 10.17 / 1.1749 = 8.66, 2.56 / 1.1801 = 2.17)

  Multiple dividends on the same day that share one FX conversion are handled
  correctly because each is converted independently using the same FX rate
  found by date-proximity matching to the "Valuta Debitering" row.

Withholding tax ("Dividendbelasting"):
  Always EUR, always negative, matched within 5 days by ISIN.

Deduplication key: (date, isin, amount_original, currency_original)
"""

import os
import pandas as pd



# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_str(val) -> str:
    s = str(val).strip()
    return "" if s == "nan" else s


def _safe_float(val) -> float | None:
    if val is None:
        return None
    s = str(val).strip().replace(",", ".")
    if not s or s == "nan":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_date(val) -> str | None:
    s = _safe_str(val)
    if not s:
        return None
    try:
        return pd.to_datetime(s, dayfirst=True).strftime("%Y-%m-%d")
    except Exception:
        return s


def _detect_columns(df: pd.DataFrame) -> dict:
    """
    Locate column names from the DataFrame header.

    DeGiro's mutations file has a "Mutatie" column for currency and the
    immediately following column (e.g. "Unnamed: 8") for the signed amount.
    The "FX" column carries the exchange rate only on "Valuta Debitering" rows.
    """
    cols = list(df.columns)
    found: dict = {}

    for i, col in enumerate(cols):
        cl = col.lower()
        if cl.startswith("datum"):
            found.setdefault("date", col)
        elif cl.startswith("product"):
            found.setdefault("product", col)
        elif cl == "isin":
            found.setdefault("isin", col)
        elif cl.startswith("omschrijving"):
            found.setdefault("desc", col)
        elif cl.startswith("fx"):
            found.setdefault("fx", col)
        elif cl.startswith("mutatie"):
            found.setdefault("mut_ccy", col)
            if i + 1 < len(cols):
                found.setdefault("mut_amt", cols[i + 1])

    return found



# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_dividends(file_path: str) -> list[dict]:
    """
    Parse a DeGiro account mutations Excel export and return one dict per
    net dividend payment.

    Each dict has:
        date              – ISO date string (YYYY-MM-DD)
        isin              – ISIN of the paying stock/ETF
        name              – Product name
        currency_original – Currency of the original dividend (EUR / USD / …)
        amount_original   – Raw amount in original currency (positive)
        gross_eur         – Gross dividend in EUR
        tax_eur           – Withholding tax in EUR (≤ 0)
        net_eur           – gross_eur + tax_eur
    """
    try:
        df = pd.read_excel(file_path, header=0)
    except Exception as e:
        print(f"  Error reading {os.path.basename(file_path)}: {e}")
        return []

    df.columns = [str(c).strip() for c in df.columns]
    C = _detect_columns(df)

    if "desc" not in C:
        print(f"  Could not find 'Omschrijving' column in {os.path.basename(file_path)}")
        print(f"  Found columns: {list(df.columns)}")
        return []

    # ------------------------------------------------------------------
    # Pass 1: bucket rows into dividends / taxes / FX debit events
    # ------------------------------------------------------------------
    dividend_rows: list[dict] = []
    tax_rows:      list[dict] = []
    fx_debit_rows: list[dict] = []   # "Valuta Debitering" — carry the FX rate

    for _, row in df.iterrows():
        desc = _safe_str(row.get(C.get("desc", ""), "")).lower()
        if not desc:
            continue

        date_str = _parse_date(row.get(C.get("date", ""), ""))
        if not date_str:
            continue

        isin     = _safe_str(row.get(C.get("isin", ""), ""))
        name     = _safe_str(row.get(C.get("product", ""), ""))
        currency = _safe_str(row.get(C.get("mut_ccy", ""), "")).upper()
        amount   = _safe_float(row.get(C.get("mut_amt", ""), ""))
        fx_rate  = _safe_float(row.get(C.get("fx", ""), "")) if "fx" in C else None

        if amount is None:
            continue

        if "dividendbelasting" in desc:
            # Withholding tax — always EUR, always negative
            tax_rows.append({
                "date":       date_str,
                "isin":       isin,
                "amount_eur": amount,   # negative
            })

        elif "dividend" in desc:
            # Gross dividend row
            dividend_rows.append({
                "date":     date_str,
                "isin":     isin,
                "name":     name,
                "currency": currency,
                "amount":   abs(amount),   # positive
            })

        elif "valuta debitering" in desc:
            # FX conversion debit — carries the exchange rate
            if fx_rate and fx_rate != 0:
                fx_debit_rows.append({
                    "date":     date_str,
                    "currency": currency,   # foreign currency (e.g. USD)
                    "fx_rate":  fx_rate,    # foreign-currency units per EUR
                })

    # ------------------------------------------------------------------
    # Pass 2: build net dividend records
    # ------------------------------------------------------------------
    results: list[dict] = []
    matched_tax_indices: set[int] = set()

    for div in dividend_rows:
        currency   = div["currency"]
        amount_raw = div["amount"]
        div_ts     = pd.Timestamp(div["date"])

        # ---- Convert to EUR ----
        if currency == "EUR":
            gross_eur = amount_raw

        else:
            # Find the closest "Valuta Debitering" row for this currency
            # within a [0, +7] day window after the dividend date.
            # The FX rate is: foreign-currency units per EUR
            # → EUR = foreign_amount / fx_rate
            best_fx  = None
            best_gap = float("inf")
            for fxd in fx_debit_rows:
                if fxd["currency"] != currency:
                    continue
                fxd_ts = pd.Timestamp(fxd["date"])
                gap = (fxd_ts - div_ts).days   # positive = debitering after dividend
                if 0 <= gap <= 7 and gap < best_gap:
                    best_gap = gap
                    best_fx  = fxd

            if best_fx:
                gross_eur = amount_raw / best_fx["fx_rate"]
            else:
                print(
                    f"  Warning: no FX rate found for {currency} dividend "
                    f"({div['name']}, {div['date']}) — skipped."
                )
                continue

        # ---- Find matching withholding tax ----
        tax_eur = 0.0
        for ti, tax in enumerate(tax_rows):
            if ti in matched_tax_indices:
                continue
            if tax["isin"] and div["isin"] and tax["isin"] != div["isin"]:
                continue
            tax_ts = pd.Timestamp(tax["date"])
            if abs((tax_ts - div_ts).days) > 5:
                continue
            tax_eur += tax["amount_eur"]   # negative
            matched_tax_indices.add(ti)

        net_eur = gross_eur + tax_eur

        results.append({
            "date":              div["date"],
            "isin":              div["isin"],
            "name":              div["name"],
            "currency_original": div["currency"],
            "amount_original":   round(amount_raw, 6),
            "gross_eur":         round(gross_eur,  4),
            "tax_eur":           round(tax_eur,    4),
            "net_eur":           round(net_eur,    4),
        })

    results.sort(key=lambda d: d["date"])
    print(f"  → Found {len(results)} dividend payment(s)")
    return results
