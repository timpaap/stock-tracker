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


tab1, tab2, tab3 = st.tabs(["📊 Portfolio", "🔬 ETF Analysis  ·  Estimates", "⚖️ Rebalancing"])

# ===========================================================================
# TAB 1 — Exact data (based on your real transactions and live prices)
# ===========================================================================
with tab1:

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
        st.plotly_chart(
            charts.dividend_over_time_chart(dividends),
            key="chart_dividends",
            width="stretch",
        )

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


# ===========================================================================
# TAB 2 — Estimates (based on partial ETF data from Yahoo Finance / justetf)
# ===========================================================================
with tab2:
    st.info(
        "📐 **These charts are estimates, not exact figures.** "
        "They are calculated from partial ETF data: sector weights and top-10 holdings "
        "from Yahoo Finance, and country breakdowns from justetf.com. "
        "They give a directional view of your portfolio exposure — useful for spotting "
        "concentration risk — but should not be treated as precise measurements."
    )

    # --- Portfolio Diversification ---
    st.subheader("🌍 Portfolio Diversification")
    st.caption(
        "Sector data from Yahoo Finance (yfinance). "
        "Country data from justetf.com. "
        "Individual stocks and bond ETFs may show limited coverage."
    )

    with st.spinner("Fetching ETF sector & country data…"):
        sector_totals, region_totals, coverage, excluded_pct = etf_holdings.get_portfolio_breakdown(positions)

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

    st.markdown("---")

    # --- Individual Stock Exposure ---
    st.subheader("🏢 Individual Stock Exposure")
    st.caption(
        "Your effective EUR exposure to individual stocks, calculated by multiplying each ETF's "
        "portfolio value by the weight of its top 10 holdings (Yahoo Finance). "
        "Diversified world/S&P 500 ETFs typically cover 35–40% of their holdings this way; "
        "concentrated ETFs (e.g. tech) cover 60–70%. The same stock appearing in multiple ETFs "
        "is aggregated into a single bar."
    )

    with st.spinner("Fetching top 10 holdings per ETF from Yahoo Finance…"):
        stock_list, stock_coverage = etf_holdings.get_stock_exposure(positions)

    st.caption(
        f"Approximately **{stock_coverage:.1f}%** of your equity portfolio value is captured "
        "in the chart below (based on top 10 holdings per ETF)."
    )
    st.plotly_chart(charts.stock_exposure_chart(stock_list), key="chart_stocks", width="stretch")

    with st.expander(f"All {len(stock_list)} stocks"):
        import pandas as _pd4
        exp_df = _pd4.DataFrame(stock_list)[["symbol", "name", "value", "sources"]]
        exp_df.columns = ["Symbol", "Name", "Effective Value (€)", "Via ETF(s)"]
        exp_df["Effective Value (€)"] = exp_df["Effective Value (€)"].map(lambda x: f"€{x:,.2f}")
        exp_df["Via ETF(s)"] = exp_df["Via ETF(s)"].map(lambda x: ", ".join(x))
        st.dataframe(exp_df, width="stretch", hide_index=True)


# ===========================================================================
# TAB 3 — Rebalancing
# ===========================================================================
with tab3:
    import json as _json

    CATEGORIES = ["Broad ETF", "Individual / Sector ETF", "Bond"]
    ALL_CATEGORIES = CATEGORIES + ["Unclassified"]

    # ------------------------------------------------------------------
    # Target allocation
    # ------------------------------------------------------------------
    st.subheader("🎯 Target Allocation")
    st.caption(
        "Set how much of your portfolio should be in each bucket. "
        "Bonds are auto-calculated so the total always equals 100%."
    )

    _saved_json = database.load_setting("rebalancing_targets", None)
    _defaults = (
        _json.loads(_saved_json)
        if _saved_json
        else {"Broad ETF": 80, "Individual / Sector ETF": 20, "Bond": 0}
    )

    tgt_col1, tgt_col2, tgt_col3 = st.columns(3)
    with tgt_col1:
        broad_target = st.slider(
            "Broad ETFs (%)",
            min_value=0, max_value=100,
            value=int(_defaults.get("Broad ETF", 80)),
            key="rb_broad",
        )
    with tgt_col2:
        ind_target = st.slider(
            "Individual / Sector ETFs (%)",
            min_value=0, max_value=100,
            value=int(_defaults.get("Individual / Sector ETF", 20)),
            key="rb_ind",
        )
    with tgt_col3:
        bond_target = st.slider(
            "Bonds (%)",
            min_value=0, max_value=100,
            value=int(_defaults.get("Bond", 0)),
            key="rb_bond",
        )

    targets = {"Broad ETF": broad_target, "Individual / Sector ETF": ind_target, "Bond": bond_target}
    _total_targets = broad_target + ind_target + bond_target

    if _total_targets != 100:
        st.warning(
            f"⚠️ Broad ETFs ({broad_target}%) + Individual/Sector ETFs ({ind_target}%) + Bonds ({bond_target}%) "
            f"= **{_total_targets}%** — adjust the sliders so the total equals exactly 100%."
        )

    if st.button("💾 Save Targets", key="rb_save_targets", disabled=(_total_targets != 100)):
        database.save_setting("rebalancing_targets", _json.dumps(targets))
        st.success("Targets saved!")

    st.markdown("---")

    # ------------------------------------------------------------------
    # Category assignment per position
    # ------------------------------------------------------------------
    st.subheader("🏷️ Classify Your Holdings")
    st.caption(
        "Assign each position to a category. We auto-guess based on the name — "
        "correct any mistakes and click **Save Categories**."
    )

    _saved_cats = database.load_asset_categories()

    def _auto_guess(name: str) -> str:
        n = name.lower()
        if any(kw in n for kw in [
            "world", "s&p", "europe", "msci", "stoxx", "emerging", "em ", "ftse",
            "all cap", "vanguard", "ishares core", "global", "developed",
        ]):
            return "Broad ETF"
        if any(kw in n for kw in [
            "bond", "obligat", "government", "treasury", "gilt", "aggregate", "corporate",
        ]):
            return "Bond"
        return "Unclassified"

    _cat_rows = []
    for _pos in positions:
        _isin = _pos["isin"]
        _cat = _saved_cats.get(_isin) or _auto_guess(_pos["name"])
        _cat_rows.append({
            "ISIN":      _isin,
            "Product":   _pos["name"],
            "Value (€)": _pos.get("current_value") or 0.0,
            "Category":  _cat,
        })

    _cat_df = pd.DataFrame(_cat_rows)

    edited_cats = st.data_editor(
        _cat_df,
        column_config={
            "ISIN":      st.column_config.TextColumn("ISIN", disabled=True),
            "Product":   st.column_config.TextColumn("Product", disabled=True),
            "Value (€)": st.column_config.NumberColumn("Value (€)", format="€%.2f", disabled=True),
            "Category":  st.column_config.SelectboxColumn(
                "Category",
                options=ALL_CATEGORIES,
                required=True,
            ),
        },
        hide_index=True,
        key="cat_editor",
        width="stretch",
    )

    if st.button("💾 Save Categories", key="rb_save_cats"):
        for _, _row in edited_cats.iterrows():
            database.save_asset_category(_row["ISIN"], _row["Category"])
        st.success("Categories saved!")
        st.rerun()

    st.markdown("---")

    # ------------------------------------------------------------------
    # Current vs Target analysis
    # ------------------------------------------------------------------
    st.subheader("📊 Current vs Target Allocation")

    # Build allocation from the (possibly-edited, not-yet-saved) table
    _live_cats = {_row["ISIN"]: _row["Category"] for _, _row in edited_cats.iterrows()}
    _total_value = sum(_pos.get("current_value") or 0.0 for _pos in positions)

    _by_cat: dict[str, float] = {cat: 0.0 for cat in ALL_CATEGORIES}
    for _pos in positions:
        _cat = _live_cats.get(_pos["isin"], "Unclassified")
        _by_cat[_cat] = _by_cat.get(_cat, 0.0) + (_pos.get("current_value") or 0.0)

    _unclassified_val = _by_cat.get("Unclassified", 0.0)
    if _unclassified_val > 0 and _total_value > 0:
        _n_unclassified = sum(
            1 for _pos in positions
            if _live_cats.get(_pos["isin"], "Unclassified") == "Unclassified"
        )
        st.warning(
            f"⚠️ {_unclassified_val / _total_value * 100:.1f}% of your portfolio "
            f"({_n_unclassified} position(s)) is still **Unclassified**. "
            "Set categories above for accurate rebalancing figures."
        )

    if _total_value == 0:
        st.info("No price data available yet — refresh prices in the sidebar first.")
    else:
        _current_pcts = {
            cat: (_by_cat.get(cat, 0.0) / _total_value * 100)
            for cat in CATEGORIES
        }

        st.plotly_chart(
            charts.rebalancing_chart(
                _current_pcts,
                {cat: float(targets[cat]) for cat in CATEGORIES},
                current_values={cat: _by_cat.get(cat, 0.0) for cat in CATEGORIES},
                total_value=_total_value,
            ),
            key="chart_rebalancing",
            width="stretch",
        )

        # Status indicator cards
        _status_cols = st.columns(len(CATEGORIES))
        for _i, _cat in enumerate(CATEGORIES):
            _curr = _current_pcts[_cat]
            _tgt  = float(targets[_cat])
            _dev  = _curr - _tgt
            _abs  = abs(_dev)
            _icon = "🟢" if _abs <= 5 else ("🟡" if _abs <= 15 else "🔴")
            _sign = "+" if _dev >= 0 else ""
            with _status_cols[_i]:
                st.metric(
                    label=f"{_icon} {_cat}",
                    value=f"{_curr:.1f}%",
                    delta=f"{_sign}{_dev:.1f} pp vs {_tgt:.0f}% target",
                    delta_color="inverse",
                    help=(
                        "🟢 within ±5 pp of target  "
                        "🟡 within ±15 pp of target  "
                        "🔴 more than 15 pp off target"
                    ),
                )

        st.markdown("---")

        # ------------------------------------------------------------------
        # Investment recommendation
        # ------------------------------------------------------------------
        st.subheader("💡 Next Investment")
        st.caption(
            "Enter how much you're planning to invest. "
            "We'll split it across categories to move your portfolio closest to your targets."
        )

        _invest = st.number_input(
            "Amount to invest (€)",
            min_value=0.0,
            value=1000.0,
            step=100.0,
            key="rb_invest",
        )

        if _invest > 0:
            _new_total = _total_value + _invest

            # For each category: how much would be needed to reach the target after investment?
            _gaps: dict[str, float] = {}
            for _cat in CATEGORIES:
                _target_val = _new_total * (targets[_cat] / 100.0)
                _current_val = _by_cat.get(_cat, 0.0)
                _gaps[_cat] = max(0.0, _target_val - _current_val)

            _total_gap = sum(_gaps.values())

            st.write(f"**Recommended split of €{_invest:,.2f}:**")
            _rec_cols = st.columns(len(CATEGORIES))
            _recommendations: dict[str, float] = {}

            for _i, _cat in enumerate(CATEGORIES):
                _rec = (_invest * _gaps[_cat] / _total_gap) if _total_gap > 0 else 0.0
                _recommendations[_cat] = _rec
                _pct_of_inv = (_rec / _invest * 100) if _invest > 0 else 0.0
                with _rec_cols[_i]:
                    st.metric(
                        label=_cat,
                        value=f"€{_rec:,.2f}",
                        delta=f"{_pct_of_inv:.0f}% of investment",
                    )

            # Show the resulting allocation after the investment
            _after_pcts: dict[str, float] = {}
            for _cat in CATEGORIES:
                _after_val = _by_cat.get(_cat, 0.0) + _recommendations[_cat]
                _after_pcts[_cat] = _after_val / _new_total * 100

            st.caption(f"Your allocation after investing €{_invest:,.2f}:")
            st.plotly_chart(
                charts.rebalancing_chart(
                    _after_pcts,
                    {cat: float(targets[cat]) for cat in CATEGORIES},
                    title="Allocation After This Investment",
                    current_values={cat: _by_cat.get(cat, 0.0) + _recommendations[cat] for cat in CATEGORIES},
                    total_value=_new_total,
                ),
                key="chart_after_rebalancing",
                width="stretch",
            )

    st.markdown("---")

    # ==================================================================
    # BROAD ETF ZOOM — US / Europe / EM
    # ==================================================================
    st.subheader("🔍 Broad ETF Deep-dive: US · Europe · EM")
    st.caption(
        "Zoom into your Broad ETF bucket and rebalance between US (S&P 500), "
        "Europe, and Emerging Markets."
    )

    BROAD_REGIONS    = ["US", "Europe", "EM"]
    ALL_BROAD_REGIONS = BROAD_REGIONS + ["Mixed / Global", "Unclassified"]

    # --- Region targets ---
    _saved_br_json = database.load_setting("broad_region_targets", None)
    _br_defaults = (
        _json.loads(_saved_br_json)
        if _saved_br_json
        else {"US": 80, "Europe": 15, "EM": 5}
    )

    br_col1, br_col2, br_col3 = st.columns(3)
    with br_col1:
        br_us = st.slider("US (%)", 0, 100, int(_br_defaults.get("US", 80)), key="br_us")
    with br_col2:
        br_eu = st.slider("Europe (%)", 0, 100, int(_br_defaults.get("Europe", 15)), key="br_eu")
    with br_col3:
        br_em = st.slider("EM (%)", 0, 100, int(_br_defaults.get("EM", 5)), key="br_em")

    br_targets = {"US": br_us, "Europe": br_eu, "EM": br_em}
    _br_sum = br_us + br_eu + br_em

    if _br_sum != 100:
        st.warning(
            f"⚠️ US ({br_us}%) + Europe ({br_eu}%) + EM ({br_em}%) = **{_br_sum}%** — "
            "adjust sliders so the total equals 100%."
        )

    if st.button("💾 Save Region Targets", key="br_save_targets", disabled=(_br_sum != 100)):
        database.save_setting("broad_region_targets", _json.dumps(br_targets))
        st.success("Region targets saved!")

    st.markdown("")

    # --- Classify broad ETFs by region ---
    _saved_regions = database.load_broad_regions()

    def _auto_guess_region(name: str) -> str:
        n = name.lower()
        if any(kw in n for kw in ["s&p", "500", "nasdaq", "us ", "usa", "united states", "north america"]):
            return "US"
        if any(kw in n for kw in ["europe", "stoxx", "euro", "europ"]):
            return "Europe"
        if any(kw in n for kw in ["emerging", " em ", "em bond", "brics", "asia", "pacific"]):
            return "EM"
        if any(kw in n for kw in ["world", "global", "msci acwi", "all country", "ftse all"]):
            return "Mixed / Global"
        return "Unclassified"

    # Only show positions classified as Broad ETF
    _br_positions = [
        _pos for _pos in positions
        if _live_cats.get(_pos["isin"], _auto_guess(_pos["name"])) == "Broad ETF"
    ]

    if not _br_positions:
        st.info(
            "No positions are classified as **Broad ETF** yet. "
            "Classify your holdings in the section above first."
        )
    else:
        _br_rows = []
        for _pos in _br_positions:
            _isin = _pos["isin"]
            _reg = _saved_regions.get(_isin) or _auto_guess_region(_pos["name"])
            _br_rows.append({
                "ISIN":      _isin,
                "Product":   _pos["name"],
                "Value (€)": _pos.get("current_value") or 0.0,
                "Region":    _reg,
            })

        _br_df = pd.DataFrame(_br_rows)

        edited_regions = st.data_editor(
            _br_df,
            column_config={
                "ISIN":      st.column_config.TextColumn("ISIN", disabled=True),
                "Product":   st.column_config.TextColumn("Product", disabled=True),
                "Value (€)": st.column_config.NumberColumn("Value (€)", format="€%.2f", disabled=True),
                "Region":    st.column_config.SelectboxColumn(
                    "Region",
                    options=ALL_BROAD_REGIONS,
                    required=True,
                ),
            },
            hide_index=True,
            key="br_editor",
            width="stretch",
        )

        if st.button("💾 Save Regions", key="br_save_regions"):
            for _, _row in edited_regions.iterrows():
                database.save_broad_region(_row["ISIN"], _row["Region"])
            st.success("Regions saved!")
            st.rerun()

        st.markdown("")

        # --- Current vs target within Broad ETF bucket ---
        _live_regions = {_row["ISIN"]: _row["Region"] for _, _row in edited_regions.iterrows()}
        _br_total = sum(_pos.get("current_value") or 0.0 for _pos in _br_positions)

        _by_region: dict[str, float] = {r: 0.0 for r in ALL_BROAD_REGIONS}
        for _pos in _br_positions:
            _reg = _live_regions.get(_pos["isin"], "Unclassified")
            _by_region[_reg] = _by_region.get(_reg, 0.0) + (_pos.get("current_value") or 0.0)

        _mixed_val = _by_region.get("Mixed / Global", 0.0)
        _unc_val   = _by_region.get("Unclassified", 0.0)
        if (_mixed_val + _unc_val) > 0 and _br_total > 0:
            st.warning(
                f"⚠️ {(_mixed_val + _unc_val) / _br_total * 100:.1f}% of your Broad ETF bucket "
                "is labelled **Mixed/Global** or **Unclassified** — assign a region for more accurate figures."
            )

        if _br_total == 0:
            st.info("No price data for Broad ETFs yet — refresh prices first.")
        elif _br_sum != 100:
            st.info("Set region targets that sum to 100% above to see the analysis.")
        else:
            _br_current_pcts = {
                reg: (_by_region.get(reg, 0.0) / _br_total * 100)
                for reg in BROAD_REGIONS
            }

            st.plotly_chart(
                charts.rebalancing_chart(
                    _br_current_pcts,
                    {reg: float(br_targets[reg]) for reg in BROAD_REGIONS},
                    title="Broad ETF: Current vs Target (US / Europe / EM)",
                    current_values={reg: _by_region.get(reg, 0.0) for reg in BROAD_REGIONS},
                    total_value=_br_total,
                ),
                key="chart_broad_region",
                width="stretch",
            )

            _br_status_cols = st.columns(len(BROAD_REGIONS))
            for _i, _reg in enumerate(BROAD_REGIONS):
                _curr = _br_current_pcts[_reg]
                _tgt  = float(br_targets[_reg])
                _dev  = _curr - _tgt
                _icon = "🟢" if abs(_dev) <= 5 else ("🟡" if abs(_dev) <= 15 else "🔴")
                _sign = "+" if _dev >= 0 else ""
                with _br_status_cols[_i]:
                    st.metric(
                        label=f"{_icon} {_reg}",
                        value=f"{_curr:.1f}%",
                        delta=f"{_sign}{_dev:.1f} pp vs {_tgt:.0f}% target",
                        delta_color="inverse",
                    )

            st.markdown("---")

            # --- Next investment within Broad ETF bucket ---
            st.write("**💡 Next Broad ETF Investment**")
            st.caption(
                "Enter how much you plan to put into Broad ETFs. "
                "We'll split it to best close the gap across US, Europe, and EM."
            )

            _br_invest = st.number_input(
                "Amount to invest in Broad ETFs (€)",
                min_value=0.0,
                value=500.0,
                step=100.0,
                key="br_invest",
            )

            if _br_invest > 0:
                _br_new_total = _br_total + _br_invest
                _br_gaps: dict[str, float] = {}
                for _reg in BROAD_REGIONS:
                    _br_gaps[_reg] = max(
                        0.0,
                        _br_new_total * (br_targets[_reg] / 100.0) - _by_region.get(_reg, 0.0),
                    )
                _br_total_gap = sum(_br_gaps.values())

                st.write(f"**Recommended split of €{_br_invest:,.2f} within Broad ETFs:**")
                _br_rec_cols = st.columns(len(BROAD_REGIONS))
                _br_recs: dict[str, float] = {}
                for _i, _reg in enumerate(BROAD_REGIONS):
                    _rec = (_br_invest * _br_gaps[_reg] / _br_total_gap) if _br_total_gap > 0 else 0.0
                    _br_recs[_reg] = _rec
                    with _br_rec_cols[_i]:
                        st.metric(
                            label=_reg,
                            value=f"€{_rec:,.2f}",
                            delta=f"{(_rec / _br_invest * 100):.0f}% of investment",
                        )

                _br_after_pcts: dict[str, float] = {
                    _reg: (_by_region.get(_reg, 0.0) + _br_recs[_reg]) / _br_new_total * 100
                    for _reg in BROAD_REGIONS
                }
                st.caption(f"Region allocation after investing €{_br_invest:,.2f} in Broad ETFs:")
                st.plotly_chart(
                    charts.rebalancing_chart(
                        _br_after_pcts,
                        {reg: float(br_targets[reg]) for reg in BROAD_REGIONS},
                        title="Broad ETF Allocation After This Investment",
                        current_values={reg: _by_region.get(reg, 0.0) + _br_recs[reg] for reg in BROAD_REGIONS},
                        total_value=_br_new_total,
                    ),
                    key="chart_broad_after",
                    width="stretch",
                )
