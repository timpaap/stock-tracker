"""
portfolio.py — Calculates portfolio returns and fetches live prices
"""

import socket
import time
import yfinance as yf

import database

# Cap every TCP connection at 10 seconds so yfinance never hangs indefinitely.
socket.setdefaulttimeout(10)

_EU_SUFFIXES = [".AS", ".DE", ".PA", ".MI", ".BR", ".SW"]
_EUR_EXCHANGES = {"AMS", "GER", "PAR", "MIL", "BRU", "EBS", "VIE", "ATH"}
_NAME_STOP_WORDS = {
    "ucits", "etf", "usd", "eur", "acc", "dist", "dis", "inc",
    "(acc)", "(dist)", "(inc)", "(dis)", "hedged", "gbp", "jpy", "chf",
    "cad", "on", "reg.", "shs",
}
_NAME_EXPANSIONS = {
    "ISHRS": "iShares",
    "ISHS": "iShares",
    "ISHARES": "iShares",
    "STST": "State Street SPDR",
    "SPDR": "SPDR",
    "VNGU": "Vanguard",
    "VGRD": "Vanguard",
}


def _extract_name_keywords(name: str) -> str:
    words = name.split()
    words = [_NAME_EXPANSIONS.get(w.upper(), w) for w in words]
    name = " ".join(words)

    for marker in [" UCITS ", " ETF USD", " ETF EUR", " UCITS"]:
        idx = name.upper().find(marker.upper())
        if idx > 4:
            name = name[:idx]
            break

    cleaned = [w for w in name.split() if w.lower() not in _NAME_STOP_WORDS]
    return " ".join(cleaned[:5]).strip()


def _search_candidates(query: str, max_results: int = 8) -> list[tuple[str, str]]:
    try:
        results = yf.Search(query, max_results=max_results, timeout=8)
        return [
            (q["symbol"], q.get("exchange", ""))
            for q in results.quotes
            if q.get("symbol")
        ]
    except (OSError, TimeoutError, Exception):
        return []


def fetch_price_for_isin(isin: str, name: str) -> dict | None:
    # --- 1. Use cached EUR ticker ---
    stored = database.load_prices().get(isin, {})
    if stored.get("ticker") and stored.get("currency") == "EUR":
        result = _fetch_by_ticker(stored["ticker"])
        if result and result["currency"] == "EUR":
            print(f"  Using cached ticker '{stored['ticker']}' for {isin}")
            return {"isin": isin, "name": name, **result}

    print(f"  Searching '{isin}' ({name[:45]})...")

    # --- 2. Search by ISIN ---
    isin_hits = _search_candidates(isin)

    # --- 3. Search by cleaned product name ---
    keywords = _extract_name_keywords(name)
    name_hits = _search_candidates(keywords) if keywords else []

    eu_symbols: dict[str, bool] = {}
    other_symbols: dict[str, bool] = {}

    for sym, exch in isin_hits + name_hits:
        if sym in eu_symbols or sym in other_symbols:
            continue
        if exch in _EUR_EXCHANGES:
            eu_symbols[sym] = True
        else:
            other_symbols[sym] = True

    if isin not in eu_symbols and isin not in other_symbols:
        other_symbols[isin] = True

    # --- 4. Try EU-exchange candidates first ---
    non_eur_result = None
    for symbol in eu_symbols:
        result = _fetch_by_ticker(symbol)
        if not result:
            continue
        if result["currency"] == "EUR":
            print(f"  Found EUR ticker '{symbol}' (EU exchange) for {isin}")
            return {"isin": isin, "name": name, **result}
        if non_eur_result is None:
            non_eur_result = {"isin": isin, "name": name, **result}

    # --- 5. Try other candidates ---
    for symbol in other_symbols:
        result = _fetch_by_ticker(symbol)
        if not result:
            continue
        if result["currency"] == "EUR":
            print(f"  Found EUR ticker '{symbol}' for {isin}")
            return {"isin": isin, "name": name, **result}
        if non_eur_result is None:
            non_eur_result = {"isin": isin, "name": name, **result}

    # --- 6. Try EU suffixes on all found ticker bases ---
    all_tried = set(eu_symbols) | set(other_symbols)
    bases_searched: set[str] = set()
    for symbol in list(all_tried):
        base = symbol.split(".")[0]
        for suffix in _EU_SUFFIXES:
            candidate = base + suffix
            if candidate in all_tried:
                continue
            all_tried.add(candidate)
            result = _fetch_by_ticker(candidate)
            if result and result["currency"] == "EUR":
                print(f"  Found EUR ticker '{candidate}' (suffix search) for {isin}")
                return {"isin": isin, "name": name, **result}
        if len(base) >= 3 and base not in bases_searched and base != isin:
            bases_searched.add(base)
            for alt_sym, alt_exch in _search_candidates(base, max_results=5):
                if alt_sym in all_tried:
                    continue
                all_tried.add(alt_sym)
                if alt_exch in _EUR_EXCHANGES:
                    result = _fetch_by_ticker(alt_sym)
                    if result and result["currency"] == "EUR":
                        print(f"  Found EUR ticker '{alt_sym}' (base search) for {isin}")
                        return {"isin": isin, "name": name, **result}

    # --- 7. Fallback: best non-EUR result ---
    if non_eur_result:
        print(f"  Warning: no EUR listing found for {isin}. "
              f"Using {non_eur_result['ticker']} ({non_eur_result['currency']}).")
        return non_eur_result

    print(f"  Could not find any price for {isin}")
    return None


def _fetch_by_ticker(symbol: str) -> dict | None:
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        price = getattr(info, "last_price", None)
        currency = getattr(info, "currency", "EUR")
        if price and price > 0:
            return {"ticker": symbol, "price": round(price, 4), "currency": currency}
    except (OSError, TimeoutError, Exception):
        pass
    return None


def refresh_all_prices() -> dict[str, dict]:
    isins = database.get_all_isins()
    if not isins:
        print("No stocks in portfolio yet.")
        return {}

    print(f"Fetching prices for {len(isins)} stock(s)/ETF(s)...")
    cached = database.load_prices()

    for row in isins:
        isin = row["isin"]
        name = row["name"]

        if isin in cached and cached[isin].get("currency") == "EUR" and cached[isin].get("price"):
            print(f"  Skipping {isin} — EUR price already cached")
            continue

        result = fetch_price_for_isin(isin, name)
        if result:
            database.save_price(
                isin=isin,
                name=name,
                ticker=result.get("ticker", ""),
                price=result["price"],
                currency=result.get("currency", "EUR"),
            )
        time.sleep(0.5)

    return database.load_prices()


def calculate_positions(transactions: list[dict], prices: dict[str, dict]) -> list[dict]:
    by_isin: dict[str, list[dict]] = {}
    for t in transactions:
        isin = t["isin"]
        by_isin.setdefault(isin, []).append(t)

    positions = []

    for isin, trades in by_isin.items():
        shares_held = 0.0
        total_invested = 0.0

        for t in trades:
            qty   = t["quantity"]
            total = t["total_eur"]

            if t["transaction_type"] == "BUY":
                shares_held    += qty
                total_invested += abs(total) if total is not None else 0.0

            elif t["transaction_type"] == "SELL":
                if shares_held > 0:
                    fraction_sold   = qty / shares_held
                    total_invested -= total_invested * fraction_sold
                shares_held -= qty
                shares_held  = max(shares_held, 0.0)

        if shares_held <= 0.0001:
            continue

        avg_cost = total_invested / shares_held if shares_held > 0 else 0.0

        price_info    = prices.get(isin)
        current_price = price_info["price"]    if price_info else None
        currency      = price_info["currency"] if price_info else "EUR"
        ticker        = price_info["ticker"]   if price_info else None

        if current_price is not None:
            current_value   = shares_held * current_price
            unrealized_gain = current_value - total_invested
            return_pct      = (unrealized_gain / total_invested * 100) if total_invested > 0 else 0.0
        else:
            current_value   = None
            unrealized_gain = None
            return_pct      = None

        positions.append({
            "isin":             isin,
            "name":             trades[0]["name"],
            "ticker":           ticker,
            "shares_held":      round(shares_held, 6),
            "avg_cost":         round(avg_cost, 4),
            "total_invested":   round(total_invested, 2),
            "current_price":    round(current_price, 4) if current_price else None,
            "currency":         currency,
            "current_value":    round(current_value, 2) if current_value else None,
            "unrealized_gain":  round(unrealized_gain, 2) if unrealized_gain is not None else None,
            "return_pct":       round(return_pct, 2) if return_pct is not None else None,
        })

    positions.sort(key=lambda p: p["current_value"] or 0, reverse=True)
    return positions


def calculate_closed_positions(transactions: list[dict]) -> list[dict]:
    """
    Returns a list of positions that have been fully sold (shares_held ≤ 0).
    For each closed position, calculates the realized P&L using the average
    cost basis method: as shares are sold, the proportion of the remaining
    cost basis is released and compared to the actual sale proceeds.
    """
    by_isin: dict[str, list[dict]] = {}
    for t in transactions:
        by_isin.setdefault(t["isin"], []).append(t)

    closed = []

    for isin, trades in by_isin.items():
        shares_held    = 0.0
        cost_basis     = 0.0
        total_proceeds = 0.0
        realized_gain  = 0.0
        first_buy      = None
        last_sell      = None

        for t in sorted(trades, key=lambda x: (x["date"], x["time"])):
            qty   = t["quantity"]
            total = abs(t["total_eur"]) if t.get("total_eur") is not None else 0.0

            if t["transaction_type"] == "BUY":
                shares_held += qty
                cost_basis  += total
                if first_buy is None:
                    first_buy = t["date"]

            elif t["transaction_type"] == "SELL" and shares_held > 0:
                fraction        = min(qty / shares_held, 1.0)
                cost_of_sold    = cost_basis * fraction
                realized_gain  += total - cost_of_sold
                total_proceeds += total
                cost_basis     -= cost_of_sold
                shares_held    -= qty
                shares_held     = max(shares_held, 0.0)
                last_sell       = t["date"]

        if shares_held > 0.0001:
            continue  # still open

        total_invested = total_proceeds - realized_gain  # original cost of sold shares
        return_pct = (realized_gain / total_invested * 100) if total_invested > 0 else 0.0

        closed.append({
            "isin":           isin,
            "name":           trades[0]["name"],
            "first_buy":      first_buy,
            "last_sell":      last_sell,
            "total_invested": round(total_invested, 2),
            "total_proceeds": round(total_proceeds, 2),
            "realized_gain":  round(realized_gain, 2),
            "return_pct":     round(return_pct, 2),
        })

    closed.sort(key=lambda p: p["realized_gain"], reverse=True)
    return closed


def calculate_portfolio_summary(positions: list[dict], transactions: list[dict] | None = None, dividends: list[dict] | None = None) -> dict:
    # Net investment = total paid for buys minus total received from sells.
    # This can go negative when you've cashed out more than you ever put in.
    if transactions is not None:
        net_investment = sum(
            abs(t["total_eur"]) if t["transaction_type"] == "BUY" else -abs(t["total_eur"])
            for t in transactions
            if t.get("total_eur") is not None
        )
    else:
        # Fallback: sum cost basis per position (cannot go negative but avoids breaking change)
        net_investment = sum(p["total_invested"] for p in positions)

    total_dividends = sum(d["net_eur"] for d in dividends) if dividends else 0.0

    priced         = [p for p in positions if p["current_value"] is not None]
    total_value    = sum(p["current_value"] for p in priced)
    total_gain     = total_value - net_investment

    total_return_pct = (
        (total_gain / net_investment * 100) if net_investment != 0 else 0.0
    )

    return {
        "net_investment":   round(net_investment, 2),
        "total_dividends":  round(total_dividends, 2),
        "total_value":      round(total_value, 2),
        "total_gain":       round(total_gain, 2),
        "total_return_pct": round(total_return_pct, 2),
        "num_positions":    len(positions),
        "prices_available": len(priced) == len(positions),
    }
