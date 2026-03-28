"""
etf_holdings.py — Fetch ETF sector weights and country breakdown.

Sector data  : yfinance funds_data.sector_weightings
Country data : justetf.com ETF profile page (static HTML, no JS needed)

Public API
----------
get_portfolio_breakdown(positions)  →  (sector_totals, region_totals, coverage)

    sector_totals  : dict[str, float]  sector → weighted fraction of portfolio
    region_totals  : dict[str, float]  country → weighted fraction of portfolio
    coverage       : list[dict]        per-position data/status summary
"""

import re
import requests
import yfinance as yf

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_JUSTETF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# Some stored tickers (e.g. .SG) are not recognised by yfinance.
# Map ISIN → an alternative ticker that yfinance does resolve.
_ISIN_TICKER_OVERRIDE: dict[str, str] = {
    "IE00BYTRRD19": "WTCH.AS",   # SPDR MSCI World Technology
}

# Hard-coded overrides for positions where automatic data fails.
# Format: isin → {"country": ..., "sector": ...}
_ISIN_MANUAL: dict[str, dict] = {
    "NL0010773842": {"country": "Netherlands", "sector": "Financials"},  # NN Group NV
}

# ISINs to exclude entirely from sector and region charts.
# These are typically bond ETFs or non-equity instruments.
_ISIN_EXCLUDE: set[str] = {
    "IE000WA6L436",   # iShares iBonds Dec 2026 Term EUR Corp — bond ETF
}

# Human-readable sector labels matching yfinance keys
_SECTOR_LABELS: dict[str, str] = {
    "technology":            "Technology",
    "financial_services":    "Financials",
    "consumer_cyclical":     "Consumer Cyclical",
    "consumer_defensive":    "Consumer Defensive",
    "healthcare":            "Healthcare",
    "industrials":           "Industrials",
    "communication_services":"Communication",
    "energy":                "Energy",
    "basic_materials":       "Materials",
    "utilities":             "Utilities",
    "realestate":            "Real Estate",
}

# Fallback: infer region from ETF name when justetf scrape fails
_REGION_RULES: list[tuple[list[str], str]] = [
    (["s&p 500", "sp500", "sp 500"],              "United States"),
    (["stoxx 50", "stoxx50", "euro stoxx"],        "Europe"),
    (["stoxx 600", "stoxx600"],                    "Europe"),
    (["msci europe"],                              "Europe"),
    (["aex"],                                      "Netherlands"),
    (["emerging market", "msci em", " em "],       "Emerging Markets"),
    (["world technology", "msci world tech"],      "Global (Tech)"),
    (["msci world"],                               "Global"),
    (["clean energy", "renewable"],                "Global (Clean Energy)"),
    (["ftse all", "ftse world"],                   "Global"),
    (["nasdaq"],                                   "United States"),
    (["ibonds", "term ", "creg", "bond", "treasury", "govt"], "Bonds"),
]


# ---------------------------------------------------------------------------
# Data fetching helpers
# ---------------------------------------------------------------------------

def _fetch_countries_justetf(isin: str) -> dict[str, float] | None:
    """
    Scrape the country breakdown table from the justetf ETF profile page.
    Returns a dict of {country_name: fraction} (values sum to ~1.0),
    or None if the page cannot be fetched / parsed.
    """
    url = f"https://www.justetf.com/en/etf-profile.html?isin={isin}&tab=holdings"
    try:
        r = requests.get(url, headers=_JUSTETF_HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        countries = re.findall(
            r'tl_etf-holdings_countries_value_name">([^<]+)</td>.*?'
            r'tl_etf-holdings_countries_value_percentage">([0-9.]+)%',
            r.text, re.DOTALL,
        )
        if not countries:
            return None
        result = {}
        for name, pct in countries:
            result[name.strip()] = round(float(pct) / 100, 6)
        return result
    except Exception:
        return None


def _infer_region_fallback(name: str) -> str:
    """Return a broad region label inferred from the ETF name."""
    lower = name.lower()
    for patterns, label in _REGION_RULES:
        if any(p in lower for p in patterns):
            return label
    return "Other"


def _fetch_sector_weights(ticker: str) -> dict[str, float] | None:
    """Fetch sector weightings from yfinance. Returns None on failure."""
    try:
        sw = yf.Ticker(ticker).funds_data.sector_weightings
        if sw:
            return {_SECTOR_LABELS.get(k, k.title()): round(v, 6) for k, v in sw.items()}
        return None
    except Exception:
        return None


def get_portfolio_breakdown(
    positions: list[dict],
) -> tuple[dict[str, float], dict[str, float], list[dict]]:
    """
    Aggregate sector and country exposure across all positions, weighted by
    each position's current portfolio value.

    Country data is fetched from justetf.com (per-ETF breakdown).
    Sector data is fetched from yfinance.
    Both fall back gracefully when data is unavailable.

    Parameters
    ----------
    positions : list of position dicts from portfolio.calculate_positions()

    Returns
    -------
    sector_totals : dict[str, float]
        Sector name → fraction of total valued portfolio (0–1).
    region_totals : dict[str, float]
        Country name → fraction of total valued portfolio (0–1).
    coverage : list[dict]
        Per-position breakdown: name, value, region, sector_data_available.
    """
    priced = [p for p in positions if p.get("current_value") is not None and p["current_value"] > 0]

    # Split into included and excluded positions upfront
    excluded = [p for p in priced if p["isin"] in _ISIN_EXCLUDE]
    included = [p for p in priced if p["isin"] not in _ISIN_EXCLUDE]

    total_value = sum(p["current_value"] for p in included)
    excluded_value = sum(p["current_value"] for p in excluded)
    excluded_pct = round(excluded_value / (total_value + excluded_value) * 100, 1) if (total_value + excluded_value) > 0 else 0

    sector_totals: dict[str, float] = {}
    region_totals: dict[str, float] = {}
    coverage: list[dict] = []

    for p in included:
        weight = p["current_value"] / total_value if total_value > 0 else 0
        ticker = p.get("ticker")
        isin   = p["isin"]
        name   = p["name"]

        # --- Manual override (e.g. individual stocks we know) ---
        if isin in _ISIN_MANUAL:
            manual = _ISIN_MANUAL[isin]
            country = manual["country"]
            region_totals[country] = region_totals.get(country, 0.0) + weight
            sector = manual["sector"]
            sector_totals[sector] = sector_totals.get(sector, 0.0) + weight
            coverage.append({
                "name":            name,
                "isin":            isin,
                "ticker":          ticker or "—",
                "current_value":   p["current_value"],
                "weight_pct":      round(weight * 100, 2),
                "dominant_region": country,
                "region_data":     "manual",
                "sector_data":     "manual",
            })
            continue

        # --- Country/Region breakdown (justetf scrape) ---
        country_weights = _fetch_countries_justetf(isin)
        if country_weights:
            for country, frac in country_weights.items():
                region_totals[country] = region_totals.get(country, 0.0) + frac * weight
            region_data_available = True
            # Use the dominant country as the summary region label for the coverage table
            dominant = max(country_weights, key=lambda k: country_weights[k])
        else:
            # Fallback: infer from name
            dominant = _infer_region_fallback(name)
            region_totals[dominant] = region_totals.get(dominant, 0.0) + weight
            region_data_available = False

        # --- Sector breakdown (yfinance) ---
        effective_ticker = _ISIN_TICKER_OVERRIDE.get(isin, ticker)
        sector_weights = _fetch_sector_weights(effective_ticker) if effective_ticker else None

        if sector_weights:
            for sector, sw in sector_weights.items():
                sector_totals[sector] = sector_totals.get(sector, 0.0) + sw * weight
            sector_available = True
        else:
            sector_available = False

        coverage.append({
            "name":             name,
            "isin":             isin,
            "ticker":           ticker or "—",
            "current_value":    p["current_value"],
            "weight_pct":       round(weight * 100, 2),
            "dominant_region":  dominant,
            "region_data":      region_data_available,
            "sector_data":      sector_available,
        })

    # Add excluded positions to coverage (marked as excluded)
    for p in excluded:
        coverage.append({
            "name":            p["name"],
            "isin":            p["isin"],
            "ticker":          p.get("ticker") or "—",
            "current_value":   p["current_value"],
            "weight_pct":      round(p["current_value"] / (total_value + excluded_value) * 100, 2) if (total_value + excluded_value) > 0 else 0,
            "dominant_region": "Excluded (bond)",
            "region_data":     "excluded",
            "sector_data":     "excluded",
        })

    # Renormalise sector totals to 1 (positions with no data are excluded)
    sector_sum = sum(sector_totals.values())
    if sector_sum > 0:
        sector_totals = {k: v / sector_sum for k, v in sector_totals.items()}

    # Renormalise region totals to 1 ("Other" from individual positions absorbs the gap)
    region_sum = sum(region_totals.values())
    if region_sum > 0:
        region_totals = {k: v / region_sum for k, v in region_totals.items()}

    # Drop tiny slices (< 0.5%) and bucket them into "Other"
    threshold = 0.005
    big = {k: v for k, v in region_totals.items() if v >= threshold or k == "Other"}
    small_sum = sum(v for k, v in region_totals.items() if v < threshold and k != "Other")
    if small_sum > 0:
        big["Other"] = big.get("Other", 0.0) + small_sum
    region_totals = big

    # Sort both dicts largest-first
    sector_totals = dict(sorted(sector_totals.items(), key=lambda x: -x[1]))
    region_totals = dict(sorted(region_totals.items(), key=lambda x: -x[1]))

    return sector_totals, region_totals, coverage, excluded_pct
