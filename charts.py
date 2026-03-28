"""
charts.py — Generates interactive charts using Plotly

What this file does:
  - Takes portfolio data (positions, transactions, prices) and turns it into
    interactive HTML charts that the user can hover over, zoom, and explore
  - All charts are returned as Plotly Figure objects, which app.py will embed
    directly into the desktop window — no browser needed

Three charts are built:

  1. allocation_chart(positions)
       A donut chart showing how your money is spread across your holdings.
       Each slice = one stock/ETF, sized by current market value.

  2. portfolio_history_chart(transactions, prices)
       A line chart with two lines over time:
         - "Invested": cumulative amount of money you have put in
         - "Value": what that investment was worth on each date
       This shows your portfolio's growth (or loss) over time.
       Uses yfinance to fetch historical daily prices.

  3. position_bar_chart(positions)
       A horizontal bar chart showing the gain/loss (in EUR and %) for each
       stock/ETF you currently hold. Green = profit, red = loss.
"""

import datetime
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


# Colour palette — used consistently across all charts
COLOUR_PROFIT = "#26a69a"   # teal/green for gains
COLOUR_LOSS   = "#ef5350"   # red for losses
COLOUR_INVEST = "#5c6bc0"   # indigo for invested amount line
COLOUR_VALUE  = "#26a69a"   # teal for current value line
COLOURS_PIE   = [
    "#5c6bc0", "#26a69a", "#ffa726", "#ef5350", "#8d6e63",
    "#78909c", "#ab47bc", "#29b6f6", "#66bb6a", "#ec407a",
]


def allocation_chart(positions: list[dict]) -> go.Figure:
    """
    Creates a donut chart showing portfolio allocation by current value.

    Each slice represents one stock or ETF. Hovering over a slice shows:
      - Full name of the holding
      - Current market value in EUR
      - Share count
      - Percentage of total portfolio

    Parameters:
        positions: list returned by portfolio.calculate_positions()

    Returns:
        A Plotly Figure object.
    """
    # Only include positions that have a current value
    priced = [p for p in positions if p.get("current_value") is not None]
    if not priced:
        return _empty_figure("No price data available yet.\nClick 'Refresh Prices' to fetch live prices.")

    labels = [p["name"] for p in priced]
    values = [p["current_value"] for p in priced]
    total  = sum(values)

    # go.Pie does not support bracket indexing in hovertemplate, so pre-format
    # the extra fields as a single HTML string and inject via %{customdata}.
    customdata = [
        f"Shares: {p['shares_held']:.4g}<br>"
        f"Price/share: €{p['current_value'] / p['shares_held']:,.2f}"
        for p in priced
    ]

    # Shorten long ETF names for display (keep first ~35 chars)
    short_labels = [_shorten(l) for l in labels]

    fig = go.Figure(go.Pie(
        labels=short_labels,
        values=values,
        customdata=customdata,
        hole=0.45,          # donut hole size (0 = full pie, 1 = empty ring)
        marker_colors=COLOURS_PIE,
        textinfo="label+percent",
        hovertemplate=(
            "<b>%{label}</b><br>"
            "Value: €%{value:,.2f}<br>"
            "%{customdata}<br>"
            "Share: %{percent}<extra></extra>"
        ),
    ))

    fig.update_layout(
        title=dict(text="Portfolio Allocation", font=dict(size=18)),
        annotations=[dict(
            text=f"€{total:,.0f}<br><span style='font-size:12px'>total</span>",
            x=0.5, y=0.5, font_size=18,
            showarrow=False
        )],
        showlegend=True,
        legend=dict(orientation="v", x=1.02, y=0.5),
        margin=dict(t=60, b=20, l=20, r=120),
        paper_bgcolor="white",
    )
    return fig


def portfolio_history_chart(
    transactions: list[dict],
    prices: dict[str, dict],
    dividends: list[dict] | None = None,
) -> go.Figure:
    """
    Creates a line chart showing how your portfolio value has changed over time,
    compared to how much you have actually invested.

    The "Invested" line rises each time you buy shares (a step-function).
    The "Value" line shows what your shares were worth on each calendar day,
    calculated using historical daily closing prices from yfinance.
    The "Value + Dividends" line adds cumulative dividend income on top.

    Parameters:
        transactions: list from database.load_transactions()
        prices      : dict from database.load_prices() — used to get ticker symbols
        dividends   : optional list from database.load_dividends()

    Returns:
        A Plotly Figure object.
    """
    if not transactions:
        return _empty_figure("No transactions found.")

    # --- Build a daily timeline from first trade to today ---
    first_date = min(datetime.date.fromisoformat(t["date"]) for t in transactions)
    today      = datetime.date.today()
    date_range = pd.date_range(start=first_date, end=today, freq="D")

    # --- Fetch historical prices for each held ticker ---
    ticker_to_isin: dict[str, str] = {}
    for isin, p in prices.items():
        if p.get("ticker"):
            ticker_to_isin[p["ticker"]] = isin

    tickers = list(ticker_to_isin.keys())
    hist_prices: dict[str, pd.Series] = {}   # isin → daily price Series
    # Fallback: cached current price for ISINs with no usable historical data
    current_price_fallback: dict[str, float] = {
        isin: p["price"]
        for isin, p in prices.items()
        if p.get("price") is not None
    }

    if tickers:
        try:
            raw = yf.download(
                tickers,
                start=first_date.isoformat(),
                end=(today + datetime.timedelta(days=1)).isoformat(),
                auto_adjust=True,
                progress=False,
            )
            if isinstance(raw.columns, pd.MultiIndex):
                close_df = raw["Close"]
                for ticker in tickers:
                    if ticker in close_df.columns:
                        series = close_df[ticker].dropna()
                        if not series.empty:
                            isin = ticker_to_isin[ticker]
                            hist_prices[isin] = series
            else:
                if len(tickers) == 1:
                    series = raw["Close"].dropna()
                    if not series.empty:
                        isin = ticker_to_isin[tickers[0]]
                        hist_prices[isin] = series
        except Exception as e:
            print(f"Warning: could not fetch historical prices: {e}")

    # --- Build a sorted list of (date_str, net_eur) for dividends ---
    sorted_divs = sorted(
        (d for d in (dividends or []) if d.get("date") and d.get("net_eur") is not None),
        key=lambda d: d["date"],
    )
    div_idx = 0
    cumulative_div_so_far = 0.0

    # --- For each day, calculate cumulative invested and total portfolio value ---
    cumulative_invested = []
    portfolio_values    = []
    portfolio_plus_divs = []
    plot_dates          = []

    sorted_tx = sorted(transactions, key=lambda t: t["date"])

    total_invested_so_far = 0.0
    shares_held: dict[str, float] = {}

    tx_idx = 0

    for day in date_range:
        day_date = day.date()
        day_ts   = pd.Timestamp(day_date)

        while tx_idx < len(sorted_tx) and sorted_tx[tx_idx]["date"] <= str(day_date):
            t = sorted_tx[tx_idx]
            isin = t["isin"]
            qty  = t["quantity"]
            if t["transaction_type"] == "BUY":
                shares_held[isin]      = shares_held.get(isin, 0.0) + qty
                total_invested_so_far += abs(t["total_eur"]) if t["total_eur"] is not None else 0.0
            elif t["transaction_type"] == "SELL":
                shares_held[isin] = max(shares_held.get(isin, 0.0) - qty, 0.0)
                # Subtract actual proceeds — allows total to go negative when
                # you've cashed out more than you ever put in.
                total_invested_so_far -= abs(t["total_eur"]) if t["total_eur"] is not None else 0.0
            tx_idx += 1

        # Accumulate dividends up to and including this day
        while div_idx < len(sorted_divs) and sorted_divs[div_idx]["date"] <= str(day_date):
            cumulative_div_so_far += sorted_divs[div_idx]["net_eur"]
            div_idx += 1

        # Only skip the very first days before any transaction has been made
        if not shares_held and total_invested_so_far == 0:
            continue

        day_value = 0.0
        can_value = True
        for isin, qty in shares_held.items():
            if qty <= 0:
                continue
            if isin in hist_prices:
                # Use the most recent price on or before this day (carry-forward)
                series    = hist_prices[isin]
                available = series[series.index <= day_ts]
                if not available.empty:
                    day_value += qty * float(available.iloc[-1])
                elif isin in current_price_fallback:
                    # History exists but doesn't go back this far — use cached
                    # current price as a flat approximation for this period.
                    day_value += qty * current_price_fallback[isin]
                else:
                    can_value = False
                    break
            elif isin in current_price_fallback:
                # No historical data at all — use cached price as flat approximation
                day_value += qty * current_price_fallback[isin]
            else:
                can_value = False
                break

        if not can_value:
            continue

        plot_dates.append(day_date)
        cumulative_invested.append(round(total_invested_so_far, 2))
        portfolio_values.append(round(day_value, 2))
        portfolio_plus_divs.append(round(day_value + cumulative_div_so_far, 2))

    if not plot_dates:
        return _empty_figure("Not enough historical price data to draw chart.\nTry refreshing prices first.")

    # --- Build the chart ---
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=plot_dates, y=portfolio_plus_divs,
        fill=None, mode="lines",
        line=dict(color="#ffa726", width=2, dash="dot"),
        name="Value + Dividends",
        hovertemplate="<b>%{x}</b><br>Value + Dividends: €%{y:,.2f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=plot_dates, y=portfolio_values,
        fill=None, mode="lines",
        line=dict(color=COLOUR_VALUE, width=2.5),
        name="Portfolio Value",
        hovertemplate="<b>%{x}</b><br>Value: €%{y:,.2f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=plot_dates, y=cumulative_invested,
        fill="tonexty",
        mode="lines",
        line=dict(color=COLOUR_INVEST, width=2.5, dash="dash"),
        name="Invested",
        hovertemplate="<b>%{x}</b><br>Invested: €%{y:,.2f}<extra></extra>",
        fillcolor="rgba(92, 107, 192, 0.12)",
    ))

    if portfolio_values and cumulative_invested:
        final_gain = portfolio_values[-1] - cumulative_invested[-1]
        gain_pct   = (final_gain / cumulative_invested[-1] * 100) if cumulative_invested[-1] else 0
        colour     = COLOUR_PROFIT if final_gain >= 0 else COLOUR_LOSS
        sign       = "+" if final_gain >= 0 else ""
        fig.add_annotation(
            x=plot_dates[-1], y=portfolio_values[-1],
            text=f"  {sign}€{final_gain:,.2f} ({sign}{gain_pct:.1f}%)",
            showarrow=False,
            font=dict(color=colour, size=13),
            xanchor="left",
        )

    fig.update_layout(
        title=dict(text="Portfolio Value Over Time", font=dict(size=18)),
        xaxis_title="Date",
        yaxis_title="Value (EUR)",
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.15),
        margin=dict(t=60, b=60, l=60, r=60),
        paper_bgcolor="white",
        plot_bgcolor="#fafafa",
        yaxis=dict(gridcolor="#eeeeee"),
        xaxis=dict(gridcolor="#eeeeee"),
    )
    return fig


def position_bar_chart(
    positions: list[dict],
    dividends_by_isin: dict[str, float] | None = None,
) -> go.Figure:
    """
    Creates a horizontal bar chart showing gain/loss per position.

    Each position has two bars:
      - Unrealized Gain / Loss  (teal = profit, red = loss)
      - Realized (Dividends)    (orange/gold, only when dividend data is provided)

    Parameters:
        positions        : list returned by portfolio.calculate_positions()
        dividends_by_isin: optional dict of isin → total net dividends in EUR

    Returns:
        A Plotly Figure object.
    """
    priced = [p for p in positions if p.get("unrealized_gain") is not None]
    if not priced:
        return _empty_figure("No price data available yet.\nClick 'Refresh Prices' to fetch live prices.")

    div_map = dividends_by_isin or {}

    # Sort by total gain (unrealized + dividends), smallest first
    priced = sorted(
        priced,
        key=lambda p: p["unrealized_gain"] + div_map.get(p["isin"], 0.0),
    )

    names      = [_shorten(p["name"]) for p in priced]
    unrealized = [p["unrealized_gain"] for p in priced]
    pcts       = [p["return_pct"] for p in priced]
    realized   = [div_map.get(p["isin"], 0.0) for p in priced]

    unr_colors = [COLOUR_PROFIT if g >= 0 else COLOUR_LOSS for g in unrealized]
    unr_text = [
        f"{'+'if g>=0 else ''}€{g:,.2f}  ({'+'if r>=0 else ''}{r:.1f}%)"
        for g, r in zip(unrealized, pcts)
    ]
    div_text = [f"+€{d:,.2f}" if d > 0 else "" for d in realized]

    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=unrealized,
        y=names,
        orientation="h",
        name="Unrealized Gain / Loss",
        marker_color=unr_colors,
        text=unr_text,
        textposition="outside",
        hovertemplate="<b>%{y}</b><br>Unrealized: €%{x:,.2f}<extra></extra>",
    ))

    if any(d > 0 for d in realized):
        fig.add_trace(go.Bar(
            x=realized,
            y=names,
            orientation="h",
            name="Realized (Dividends)",
            marker_color="#ffa726",   # orange/gold
            text=div_text,
            textposition="outside",
            hovertemplate="<b>%{y}</b><br>Dividends: €%{x:,.2f}<extra></extra>",
        ))

    has_dividends = any(d > 0 for d in realized)
    fig.update_layout(
        title=dict(text="Gain / Loss per Position", font=dict(size=18)),
        xaxis_title="Gain / Loss (EUR)",
        xaxis=dict(zeroline=True, zerolinecolor="#aaa", zerolinewidth=1.5),
        yaxis=dict(automargin=True),
        barmode="stack",
        legend=dict(orientation="h", y=-0.18) if has_dividends else dict(),
        margin=dict(t=60, b=80 if has_dividends else 60, l=20, r=140),
        paper_bgcolor="white",
        plot_bgcolor="#fafafa",
        showlegend=has_dividends,
    )
    return fig


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _empty_figure(message: str) -> go.Figure:
    """Returns a blank placeholder figure with a centred message."""
    fig = go.Figure()
    fig.add_annotation(
        text=message, x=0.5, y=0.5,
        xref="paper", yref="paper",
        showarrow=False,
        font=dict(size=14, color="#888"),
        align="center",
    )
    fig.update_layout(
        paper_bgcolor="white",
        plot_bgcolor="white",
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        margin=dict(t=40, b=40, l=40, r=40),
    )
    return fig


def _shorten(name: str, max_len: int = 35) -> str:
    """Truncates a long stock name and adds '…' so chart labels stay readable."""
    return name if len(name) <= max_len else name[:max_len].rstrip() + "…"
