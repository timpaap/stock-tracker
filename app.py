"""
app.py — Main entry point for the Stock & ETF Portfolio Tracker

How to run:
    source venv/bin/activate
    streamlit run app.py

What this file does:
  - Starts a local web server on your Mac (http://localhost:8501)
  - Streamlit automatically opens it in your browser — it behaves like a
    desktop app but lives in a browser tab
  - Provides a sidebar to upload new transaction files and refresh prices
  - Shows a dashboard with:
      • Summary cards (total invested, current value, total gain/loss, return %)
      • A donut chart of portfolio allocation
      • A line chart of portfolio value over time
      • A bar chart of gain/loss per position
      • A full transaction table

Layout:
  ┌─────────────────────────────────────────────────────┐
  │  Sidebar          │  Main area                      │
  │  ─────────────    │  ──────────────────────────     │
  │  Upload file      │  Summary cards (4 metrics)      │
  │  Refresh prices   │  Allocation chart               │
  │  Last updated     │  History chart                  │
  │                   │  Gain/Loss bar chart             │
  │                   │  Transactions table              │
  └─────────────────────────────────────────────────────┘
"""

import os
import pandas as pd
import streamlit as st

# Always run relative to the project root, regardless of where streamlit was launched from.
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import database
import transaction_parser
import dividend_parser
import portfolio
import charts
import etf_holdings


# ---------------------------------------------------------------------------
# Page configuration — must be the first Streamlit call in the file
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Portfolio Tracker",
    page_icon="📈",
    layout="wide",           # use the full width of the browser window
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# Custom CSS — small style tweaks to make the metric cards look nicer
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    /* Make metric values a bit larger */
    [data-testid="stMetricValue"] { font-size: 1.6rem; }
    /* Subtle card background for metric containers */
    [data-testid="stMetric"] {
        background: #f8f9fa;
        border-radius: 10px;
        padding: 14px 18px;
    }
    /* Remove top padding from the main block */
    .block-container { padding-top: 1.5rem; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Initialise database (creates tables if they don't exist yet)
# ---------------------------------------------------------------------------
database.init_db()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("📈 Portfolio Tracker")
    st.markdown("---")

    # --- File upload ---
    st.subheader("Upload Transactions")
    st.caption("Export your transactions from DeGiro as an Excel (.xlsx) file and upload it here.")
    uploaded_files = st.file_uploader(
        label="Choose .xlsx file(s)",
        type=["xlsx"],
        accept_multiple_files=True,
        help="You can upload multiple monthly files at once.",
    )

    if uploaded_files:
        for uploaded_file in uploaded_files:
            # Save the file to the uploads/ folder so it's kept on disk
            save_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads", uploaded_file.name)
            with open(save_path, "wb") as f:
                f.write(uploaded_file.getbuffer())

            # Parse and save to database
            try:
                txs = transaction_parser.parse_transactions(save_path)
                saved, skipped = database.save_transactions(txs)
                if saved > 0:
                    st.success(f"✅ {uploaded_file.name}: {saved} new transaction(s) added.")
                else:
                    st.info(f"ℹ️ {uploaded_file.name}: already up to date.")
                if skipped > 0:
                    st.caption(f"  ({skipped} duplicate(s) skipped)")
            except Exception as e:
                st.error(f"❌ Error reading {uploaded_file.name}: {e}")

    st.markdown("---")

    # --- Refresh prices button ---
    st.subheader("Live Prices")
    st.caption("Fetches the latest market prices from Yahoo Finance.")

    if st.button("🔄 Refresh Prices", use_container_width=True):
        with st.spinner("Fetching prices — this may take a moment..."):
            portfolio.refresh_all_prices()
        st.success("Prices updated!")
        st.rerun()  # reload the page so updated prices appear immediately

    # Show when prices were last fetched
    prices = database.load_prices()
    if prices:
        timestamps = [p["fetched_at"] for p in prices.values() if p.get("fetched_at")]
        if timestamps:
            last = max(timestamps)
            st.caption(f"Last updated: {last}")

    st.markdown("---")

    # --- Mutations / dividends upload ---
    st.subheader("Upload Mutations (Dividends)")
    st.caption(
        "Export your account mutations from DeGiro "
        "(*Rekeningmutatieoverzicht*) as Excel and upload here "
        "to track dividend income."
    )
    mutations_files = st.file_uploader(
        label="Choose mutations .xlsx file(s)",
        type=["xlsx"],
        accept_multiple_files=True,
        key="mutations_uploader",
    )
    if mutations_files:
        for mf in mutations_files:
            save_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "uploads", mf.name
            )
            with open(save_path, "wb") as f:
                f.write(mf.getbuffer())
            try:
                divs = dividend_parser.parse_dividends(save_path)
                if divs:
                    saved, skipped = database.save_dividends(divs)
                    if saved > 0:
                        st.success(f"✅ {mf.name}: {saved} dividend payment(s) added.")
                    else:
                        st.info(f"ℹ️ {mf.name}: already up to date.")
                    if skipped > 0:
                        st.caption(f"  ({skipped} duplicate(s) skipped)")
                else:
                    st.info(f"ℹ️ {mf.name}: no dividend rows found.")
            except Exception as e:
                st.error(f"❌ Error reading {mf.name}: {e}")

    st.markdown("---")
    st.caption("Data stored locally in `data/portfolio.db`")


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
transactions      = database.load_transactions()
prices            = database.load_prices()
dividends         = database.load_dividends()
dividends_by_isin = database.load_dividends_by_isin()
positions         = portfolio.calculate_positions(transactions, prices)
summary           = portfolio.calculate_portfolio_summary(positions, transactions, dividends)


# ---------------------------------------------------------------------------
# Main dashboard
# ---------------------------------------------------------------------------
st.title("My Portfolio Dashboard")

if not transactions:
    # First-time user — show a helpful getting-started message
    st.info(
        "👋 Welcome! Upload your DeGiro transaction file using the sidebar on the left, "
        "then click **🔄 Refresh Prices** to fetch live market prices."
    )
    st.stop()


# --- Profit overview block ---
total_unrealized = summary["total_gain"] if summary["prices_available"] else 0.0
total_dividends  = summary.get("total_dividends", 0.0)
total_profit     = total_unrealized + total_dividends

st.subheader("💹 Profit Overview")
pcol1, pcol2, pcol3 = st.columns(3)

with pcol1:
    if summary["prices_available"]:
        sign = "+" if total_profit >= 0 else ""
        st.metric(
            label="💹 Total Profit",
            value=f"{sign}€{total_profit:,.2f}",
            help="Unrealized gain/loss + dividends received",
        )
    else:
        sign = "+" if total_dividends >= 0 else ""
        st.metric(
            label="💹 Total Profit",
            value=f"{sign}€{total_dividends:,.2f}",
            help="Only dividends counted — refresh prices to include unrealized gain",
        )

with pcol2:
    if summary["prices_available"]:
        gain = summary["total_gain"]
        sign = "+" if gain >= 0 else ""
        st.metric(
            label="📈 Unrealized Gain / Loss",
            value=f"{sign}€{gain:,.2f}",
            delta=f"{sign}{summary['total_return_pct']:.2f}%",
        )
    else:
        st.metric(label="📈 Unrealized Gain / Loss", value="—")

with pcol3:
    sign = "+" if total_dividends >= 0 else ""
    st.metric(
        label="💰 Realized (Dividends)",
        value=f"{sign}€{total_dividends:,.2f}",
    )

st.markdown("---")

# --- Summary metric cards ---
col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        label="💶 Net Investment",
        value=f"€{summary['net_investment']:,.2f}",
    )
with col2:
    if summary["prices_available"]:
        st.metric(
            label="📊 Current Value",
            value=f"€{summary['total_value']:,.2f}",
        )
    else:
        st.metric(label="📊 Current Value", value="—",
                  help="Click 'Refresh Prices' in the sidebar.")
with col3:
    st.metric(label="🗂 Positions", value=summary["num_positions"])

st.markdown("---")

# --- Charts ---
if not prices:
    st.warning(
        "⚠️ No price data yet. Click **🔄 Refresh Prices** in the sidebar — "
        "the charts will fully populate once prices are fetched."
    )

# Row 1: allocation + gain/loss side by side
chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    st.plotly_chart(
        charts.allocation_chart(positions),
        key="chart_allocation",
        width="stretch",
    )

with chart_col2:
    st.plotly_chart(
        charts.position_bar_chart(positions, dividends_by_isin),
        key="chart_bars",
        width="stretch",
    )

# Row 2: portfolio history — full width
st.plotly_chart(
    charts.portfolio_history_chart(transactions, prices, dividends),
    key="chart_history",
    width="stretch",
)

st.markdown("---")

# --- Transactions table ---
st.subheader("All Transactions")

if transactions:
    import pandas as pd

    df = pd.DataFrame(transactions)

    # Pick and rename columns for display
    display_cols = {
        "date":             "Date",
        "name":             "Product",
        "isin":             "ISIN",
        "transaction_type": "Type",
        "quantity":         "Qty",
        "price":            "Price (local)",
        "transaction_fee":  "Fee (€)",
        "total_eur":        "Total (€)",
    }
    df_display = df[[c for c in display_cols if c in df.columns]].rename(columns=display_cols)
    df_display = df_display.sort_values("Date", ascending=False)

    # Colour BUY rows green, SELL rows red
    def _row_colour(row):
        colour = "background-color: #e8f5e9" if row["Type"] == "BUY" else "background-color: #ffebee"
        return [colour] * len(row)

    st.dataframe(
        df_display.style.apply(_row_colour, axis=1),
        width="stretch",
        hide_index=True,
    )

st.markdown("---")

# --- Dividends table ---
st.subheader("💰 Dividend Income")

if dividends:
    import pandas as _pd
    div_df = _pd.DataFrame(dividends)

    # Per-stock summary
    by_stock = (
        div_df.groupby(["isin", "name"])
        .agg(
            payments  = ("net_eur", "count"),
            gross_eur = ("gross_eur", "sum"),
            tax_eur   = ("tax_eur", "sum"),
            net_eur   = ("net_eur", "sum"),
        )
        .reset_index()
        .rename(columns={
            "name":     "Product",
            "isin":     "ISIN",
            "payments": "# Payments",
            "gross_eur":"Gross (€)",
            "tax_eur":  "Tax (€)",
            "net_eur":  "Net (€)",
        })
        .sort_values("Net (€)", ascending=False)
    )
    st.dataframe(
        by_stock.style.format({
            "Gross (€)": "€{:,.2f}",
            "Tax (€)":   "€{:,.2f}",
            "Net (€)":   "€{:,.2f}",
        }),
        width="stretch",
        hide_index=True,
    )

    with st.expander("Show individual dividend payments"):
        detail_df = div_df[["date","name","isin","currency_original","amount_original","gross_eur","tax_eur","net_eur"]].copy()
        detail_df.columns = ["Date","Product","ISIN","Currency","Amount (orig)","Gross (€)","Tax (€)","Net (€)"]
        detail_df = detail_df.sort_values("Date", ascending=False)
        st.dataframe(
            detail_df.style.format({
                "Amount (orig)": "{:,.4f}",
                "Gross (€)":    "€{:,.2f}",
                "Tax (€)":      "€{:,.2f}",
                "Net (€)":      "€{:,.2f}",
            }),
            width="stretch",
            hide_index=True,
        )
else:
    st.info(
        "No dividend data yet. Upload your DeGiro mutations file "
        "(*Rekeningmutatieoverzicht*) in the sidebar to track dividend income."
    )

st.markdown("---")

# --- Closed Positions ---
st.subheader("📂 Closed Positions")

closed_positions = portfolio.calculate_closed_positions(transactions)

if closed_positions:
    import pandas as _pd2
    cp_df = _pd2.DataFrame(closed_positions)
    cp_df = cp_df.rename(columns={
        "name":           "Product",
        "isin":           "ISIN",
        "first_buy":      "First Buy",
        "last_sell":      "Last Sell",
        "total_invested": "Cost Basis (€)",
        "total_proceeds": "Proceeds (€)",
        "realized_gain":  "Realized P&L (€)",
        "return_pct":     "Return (%)",
    })

    def _pnl_colour(row):
        colour = "background-color: #e8f5e9" if row["Realized P&L (€)"] >= 0 else "background-color: #ffebee"
        return [colour] * len(row)

    st.dataframe(
        cp_df.style
            .apply(_pnl_colour, axis=1)
            .format({
                "Cost Basis (€)":  "€{:,.2f}",
                "Proceeds (€)":    "€{:,.2f}",
                "Realized P&L (€)": lambda v: f"{'+'if v>=0 else ''}€{v:,.2f}",
                "Return (%)": lambda v: f"{'+'if v>=0 else ''}{v:.2f}%",
            }),
        width="stretch",
        hide_index=True,
    )
else:
    st.info("No fully closed positions found.")

st.markdown("---")

# --- Portfolio Diversification ---
st.subheader("🌍 Portfolio Diversification")
st.caption(
    "Sector data from Yahoo Finance (yfinance). "
    "Region is inferred from each ETF's investment focus. "
    "Individual stocks and bond ETFs may show limited sector data."
)

with st.spinner("Fetching ETF sector data from Yahoo Finance…"):
    sector_totals, region_totals, coverage, excluded_pct = etf_holdings.get_portfolio_breakdown(positions)

# Side-by-side donut charts
if excluded_pct > 0:
    st.caption(
        f"⚠️ Bond ETFs ({excluded_pct:.1f}% of portfolio value) are excluded from both charts — "
        "sector and country data is only meaningful for equity positions. "
        "Percentages shown are relative to the equity portion of your portfolio."
    )
div_col1, div_col2 = st.columns(2)
with div_col1:
    st.plotly_chart(
        charts.sector_allocation_chart(sector_totals),
        key="chart_sector",
        width="stretch",
    )
with div_col2:
    st.plotly_chart(
        charts.region_allocation_chart(region_totals),
        key="chart_region",
        width="stretch",
    )

# Coverage table — tells the user what was included
with st.expander("Data coverage per position"):
    import pandas as _pd3
    cov_df = _pd3.DataFrame(coverage)[[
        "name", "ticker", "weight_pct", "dominant_region", "region_data", "sector_data"
    ]].rename(columns={
        "name":           "Product",
        "ticker":         "Ticker",
        "weight_pct":     "Weight (%)",
        "dominant_region":"Dominant Region",
        "region_data":    "Country data",
        "sector_data":    "Sector data",
    })
    cov_df["Country data"] = cov_df["Country data"].map({True: "✅ justetf", False: "⚠️ inferred", "manual": "✏️ manual", "excluded": "⏭️ excluded"})
    cov_df["Sector data"]  = cov_df["Sector data"].map({True: "✅ yfinance", False: "❌ no data", "manual": "✏️ manual", "excluded": "⏭️ excluded"})
    st.dataframe(cov_df, width="stretch", hide_index=True)
