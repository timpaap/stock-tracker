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
from datetime import date, datetime
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
import finance_tracker


# ---------------------------------------------------------------------------
# Page configuration — must be the first Streamlit call in the file
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Portfolio Tracker",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    [data-testid="stMetricValue"] { font-size: 1.6rem; }
    [data-testid="stMetric"] {
        background: #f8f9fa;
        border-radius: 10px;
        padding: 14px 18px;
    }
    .block-container { padding-top: 1.5rem; }
    /* Nav buttons */
    div[data-testid="stSidebarContent"] div.nav-btn button {
        width: 100%;
        border-radius: 8px;
        margin-bottom: 4px;
    }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Initialise database
# ---------------------------------------------------------------------------
database.init_db()


# ---------------------------------------------------------------------------
# Top-level navigation (sidebar)
# ---------------------------------------------------------------------------
# resolve any pending navigation before the widget is drawn
_NAV_OPTIONS = ["🏠 Home", "💶 Budget", "📊 Portfolio"]
if "_nav_pending" in st.session_state:
    _pending = st.session_state.pop("_nav_pending")
    if _pending in _NAV_OPTIONS:
        st.session_state["main_nav"] = _pending

with st.sidebar:
    st.title("📈 Mijn Financiën")
    st.markdown("---")

    _section = st.radio(
        "Navigatie",
        options=_NAV_OPTIONS,
        label_visibility="collapsed",
        key="main_nav",
    )

    st.markdown("---")

# collapse sidebar entirely on the home screen
if _section == "🏠 Home":
    st.markdown("""
    <style>
        [data-testid="stSidebar"] { display: none; }
        [data-testid="collapsedControl"] { display: none; }
    </style>
    """, unsafe_allow_html=True)

with st.sidebar:
    # ---- Portfolio sidebar ----
    if _section == "📊 Portfolio":
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
                save_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads", uploaded_file.name)
                with open(save_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
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
        st.subheader("Live Prices")
        st.caption("Fetches the latest market prices from Yahoo Finance.")

        if st.button("🔄 Refresh Prices", use_container_width=True):
            with st.spinner("Fetching prices — this may take a moment..."):
                portfolio.refresh_all_prices()
            st.success("Prices updated!")
            st.rerun()

        prices = database.load_prices()
        if prices:
            timestamps = [p["fetched_at"] for p in prices.values() if p.get("fetched_at")]
            if timestamps:
                st.caption(f"Last updated: {max(timestamps)}")

        st.markdown("---")
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

    # ---- Budget sidebar ----
    elif _section == "💶 Budget":
        _all_months = database.list_budget_months()
        _cur_month  = finance_tracker.today_month()
        _month_options = sorted(set(_all_months + [_cur_month]), reverse=True)
        _month_labels  = {m: finance_tracker.month_label(m) for m in _month_options}

        st.subheader("📅 Maand")
        _new_month_raw = st.text_input(
            "Nieuwe maand aanmaken (YYYY-MM)",
            placeholder="bijv. 2026-05",
            key="bgt_new_month",
        )
        _copy_from = st.selectbox(
            "Kopieer structuur van",
            options=["— leeg —"] + _month_options,
            key="bgt_copy_from",
        )
        if st.button("➕ Maand aanmaken", key="bgt_create_month"):
            _nm = _new_month_raw.strip()
            try:
                datetime.strptime(_nm, "%Y-%m")
                if _copy_from != "— leeg —":
                    database.copy_budget_structure(_copy_from, _nm)
                else:
                    finance_tracker.seed_month_defaults(database, _nm)
                st.success(f"Maand {_nm} aangemaakt!")
                st.rerun()
            except ValueError:
                st.error("Gebruik het formaat YYYY-MM (bijv. 2026-04)")

        st.markdown("---")
        st.subheader("📥 Excel importeren")
        _xlsx_upload = st.file_uploader(
            "Upload Template money.xlsx",
            type=["xlsx"],
            key="bgt_xlsx",
        )
        _import_month = st.selectbox(
            "In maand",
            options=_month_options if _month_options else [_cur_month],
            format_func=lambda m: _month_labels.get(m, m),
            key="bgt_import_month",
        )
        if _xlsx_upload and st.button("📥 Importeer", key="bgt_do_import"):
            import tempfile as _tf
            with _tf.NamedTemporaryFile(delete=False, suffix=".xlsx") as _tmp:
                _tmp.write(_xlsx_upload.getbuffer())
                _tmp_path = _tmp.name
            try:
                _res = finance_tracker.import_from_excel(_tmp_path, _import_month, database)
                st.success(
                    f"Geïmporteerd: {_res['income']} inkomsten, "
                    f"{_res['expenses']} uitgaven, {_res['transactions']} transacties"
                )
                st.rerun()
            except Exception as _e:
                st.error(f"Import mislukt: {_e}")
            finally:
                os.unlink(_tmp_path)

        st.markdown("---")
        st.caption("Data stored locally in `data/portfolio.db`")


# ---------------------------------------------------------------------------
# Load portfolio data (always needed for portfolio section)
# ---------------------------------------------------------------------------
prices            = database.load_prices()
transactions      = database.load_transactions()
dividends         = database.load_dividends()
dividends_by_isin = database.load_dividends_by_isin()
positions         = portfolio.calculate_positions(transactions, prices)
summary           = portfolio.calculate_portfolio_summary(positions, transactions, dividends)


# ===========================================================================
# HOME / WELCOME SCREEN
# ===========================================================================
if _section == "🏠 Home":
    st.title("👋 Welkom bij Mijn Financiën")
    st.markdown("Kies een sectie om te beginnen.")
    st.markdown("---")

    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown("### 💶 Budget")
        st.markdown(
            "Bijhouden van maandelijkse inkomsten en uitgaven. "
            "Stel budgetten in, log transacties en bekijk je financiële overzicht."
        )
        total_invested_display = f"€{summary['net_investment']:,.2f}" if summary.get('net_investment') else "—"
        _bgt_months = database.list_budget_months()
        st.metric("Maanden bijgehouden", len(_bgt_months))
        if st.button("Ga naar Budget →", key="home_to_budget", use_container_width=True):
            st.session_state["_nav_pending"] = "💶 Budget"
            st.rerun()

    with col_b:
        st.markdown("### 📊 Portfolio")
        st.markdown(
            "Inzicht in je beleggingsportfolio. "
            "Bekijk posities, rendement, dividenden en herbalanceer je allocatie."
        )
        if summary.get("prices_available"):
            _pf_val = summary.get("total_value", 0)
            st.metric("Huidige waarde", f"€{_pf_val:,.2f}")
        else:
            st.metric("Transacties", len(transactions))
        if st.button("Ga naar Portfolio →", key="home_to_portfolio", use_container_width=True):
            st.session_state["_nav_pending"] = "📊 Portfolio"
            st.rerun()

    st.stop()

# ===========================================================================
# BUDGET SECTION
# ===========================================================================
elif _section == "💶 Budget":

    st.title("💶 Maandbudget")

    # ---- Month selector ----
    _all_months = database.list_budget_months()
    _cur_month  = finance_tracker.today_month()

    _month_options = sorted(set(_all_months + [_cur_month]), reverse=True)
    _month_labels  = {m: finance_tracker.month_label(m) for m in _month_options}

    _sel_month = st.selectbox(
        "Maand",
        options=_month_options,
        format_func=lambda m: _month_labels[m],
        key="budget_month",
    )

    # Sidebar: new month + Excel import
    # ---- Seed defaults if month has no data ----
    _inc_items  = database.load_budget_income(_sel_month)
    _exp_items  = database.load_budget_expenses(_sel_month)
    _txns       = database.load_budget_transactions(_sel_month)

    if not _inc_items and not _exp_items:
        finance_tracker.seed_month_defaults(database, _sel_month)
        _inc_items  = database.load_budget_income(_sel_month)
        _exp_items  = database.load_budget_expenses(_sel_month)

    _summary = finance_tracker.calculate_summary(_inc_items, _exp_items, _txns)

    # ---- Summary cards at top ----
    st.subheader(f"📊 Overzicht — {_month_labels[_sel_month]}")
    _sc1, _sc2, _sc3, _sc4 = st.columns(4)
    with _sc1:
        st.metric("💶 Totale inkomsten", f"€{_summary['total_income']:,.2f}")
    with _sc2:
        st.metric("💸 Vaste lasten + sparen", f"€{(_summary['fixed_expenses'] + _summary['savings']):,.2f}")
    with _sc3:
        st.metric("🛒 Variabel (werkelijk)", f"€{_summary['total_actual_variable']:,.2f}",
                  delta=f"budget: €{_summary['total_var_budget']:,.0f}", delta_color="off")
    with _sc4:
        _res = _summary["actual_result"]
        _sign = "+" if _res >= 0 else ""
        st.metric("✅ Resultaat", f"{_sign}€{_res:,.2f}",
                  delta="verwacht: " + (f"+€{_summary['expected_result']:,.0f}" if _summary['expected_result'] >= 0 else f"€{_summary['expected_result']:,.0f}"),
                  delta_color="off")

    st.markdown("---")

    # ---- Charts ----
    _ch1, _ch2 = st.columns(2)
    with _ch1:
        st.plotly_chart(
            finance_tracker.income_vs_expenses_chart(_summary),
            key="bgt_overview_chart",
            width="stretch",
        )
    with _ch2:
        st.plotly_chart(
            finance_tracker.variable_spending_chart(_summary["actual_by_cat"], _summary["var_budgets"]),
            key="bgt_variable_chart",
            width="stretch",
        )

    st.markdown("---")

    # ---- Main editing area (4 columns) ----
    _col_inc, _col_exp, _col_sav, _col_var = st.columns(4)

    # ==================== INKOMSTEN ====================
    with _col_inc:
        st.subheader("💰 Inkomsten")

        _fixed_inc = [r for r in _inc_items if r["category"] == "fixed"]
        _var_inc   = [r for r in _inc_items if r["category"] == "variable"]

        st.caption("**Vast**")
        for _row in _fixed_inc:
            _c1, _c2 = st.columns([3, 2])
            with _c1:
                st.write(_row["name"])
            with _c2:
                _new_val = st.number_input(
                    f"€", value=float(_row["amount"]), step=1.0,
                    key=f"inc_f_{_row['id']}", label_visibility="collapsed",
                )
                if _new_val != _row["amount"]:
                    database.save_budget_income(_sel_month, "fixed", _row["name"], _new_val)
                    st.rerun()

        # Add fixed income row
        with st.expander("＋ Vast inkomen toevoegen"):
            _ni_name = st.text_input("Naam", key="ni_fname")
            _ni_val  = st.number_input("Bedrag (€)", min_value=0.0, step=1.0, key="ni_fval")
            if st.button("Toevoegen", key="ni_fadd") and _ni_name.strip():
                database.save_budget_income(_sel_month, "fixed", _ni_name.strip(), _ni_val)
                st.rerun()

        st.divider()
        st.caption(f"**Vast totaal: €{sum(r['amount'] for r in _fixed_inc):,.2f}**")

        st.caption("**Variabel**")
        for _row in _var_inc:
            _c1, _c2 = st.columns([3, 2])
            with _c1:
                st.write(_row["name"])
            with _c2:
                _new_val = st.number_input(
                    f"€", value=float(_row["amount"]), step=1.0,
                    key=f"inc_v_{_row['id']}", label_visibility="collapsed",
                )
                if _new_val != _row["amount"]:
                    database.save_budget_income(_sel_month, "variable", _row["name"], _new_val)
                    st.rerun()

        with st.expander("＋ Variabel inkomen toevoegen"):
            _ni_name2 = st.text_input("Naam", key="ni_vname")
            _ni_val2  = st.number_input("Bedrag (€)", step=1.0, key="ni_vval")
            if st.button("Toevoegen", key="ni_vadd") and _ni_name2.strip():
                database.save_budget_income(_sel_month, "variable", _ni_name2.strip(), _ni_val2)
                st.rerun()

        st.divider()
        st.caption(f"**Totale inkomsten: €{_summary['total_income']:,.2f}**")

    # ==================== VASTE UITGAVEN ====================
    with _col_exp:
        st.subheader("🏠 Vaste uitgaven")

        _fixed_exp = [r for r in _exp_items if r["category"] == "fixed"]
        for _row in _fixed_exp:
            _c1, _c2 = st.columns([3, 2])
            with _c1:
                st.write(_row["name"])
            with _c2:
                _new_val = st.number_input(
                    "€", value=float(_row["amount"]), min_value=0.0, step=0.01,
                    key=f"exp_f_{_row['id']}", label_visibility="collapsed",
                )
                if _new_val != _row["amount"]:
                    database.save_budget_expense(_sel_month, "fixed", _row["name"], _new_val)
                    st.rerun()

        with st.expander("＋ Vaste uitgave toevoegen"):
            _ne_name = st.text_input("Naam", key="ne_fname")
            _ne_val  = st.number_input("Bedrag (€)", min_value=0.0, step=0.01, key="ne_fval")
            if st.button("Toevoegen", key="ne_fadd") and _ne_name.strip():
                database.save_budget_expense(_sel_month, "fixed", _ne_name.strip(), _ne_val)
                st.rerun()

        st.divider()
        st.caption(f"**Totaal vaste lasten: €{_summary['fixed_expenses']:,.2f}**")

    # ==================== SPAREN ====================
    with _col_sav:
        st.subheader("🏦 Sparen")

        _sav_items = [r for r in _exp_items if r["category"] == "savings"]
        for _row in _sav_items:
            _c1, _c2 = st.columns([3, 2])
            with _c1:
                st.write(_row["name"])
            with _c2:
                _new_val = st.number_input(
                    "€", value=float(_row["amount"]), min_value=0.0, step=1.0,
                    key=f"sav_{_row['id']}", label_visibility="collapsed",
                )
                if _new_val != _row["amount"]:
                    database.save_budget_expense(_sel_month, "savings", _row["name"], _new_val)
                    st.rerun()

        with st.expander("＋ Spaardoel toevoegen"):
            _ns_name = st.text_input("Naam", key="ns_name")
            _ns_val  = st.number_input("Bedrag (€)", min_value=0.0, step=1.0, key="ns_val")
            if st.button("Toevoegen", key="ns_add") and _ns_name.strip():
                database.save_budget_expense(_sel_month, "savings", _ns_name.strip(), _ns_val)
                st.rerun()

        st.divider()
        st.caption(f"**Totaal sparen: €{_summary['savings']:,.2f}**")

    # ==================== VARIABELE BUDGETTEN ====================
    with _col_var:
        st.subheader("🎯 Variabele budgets")

        _vb_items = [r for r in _exp_items if r["category"] == "variable_budget"]
        for _row in _vb_items:
            _c1, _c2 = st.columns([3, 2])
            _actual_spent = _summary["actual_by_cat"].get(_row["name"], 0.0)
            _budget       = _row["amount"]
            _over = _actual_spent > _budget > 0
            with _c1:
                _icon = "🔴 " if _over else ""
                st.write(f"{_icon}{_row['name']}")
                st.caption(f"werkelijk: €{_actual_spent:,.2f}")
            with _c2:
                _new_val = st.number_input(
                    "Budget €", value=float(_budget), min_value=0.0, step=5.0,
                    key=f"vb_{_row['id']}", label_visibility="collapsed",
                )
                if _new_val != _budget:
                    database.save_budget_expense(_sel_month, "variable_budget", _row["name"], _new_val)
                    st.rerun()

        with st.expander("＋ Categorie toevoegen"):
            _nb_name = st.text_input("Naam", key="nb_name")
            _nb_val  = st.number_input("Budget (€)", min_value=0.0, step=5.0, key="nb_val")
            if st.button("Toevoegen", key="nb_add") and _nb_name.strip():
                database.save_budget_expense(_sel_month, "variable_budget", _nb_name.strip(), _nb_val)
                st.rerun()

        st.divider()
        st.caption(f"**Budget: €{_summary['total_var_budget']:,.2f}  ·  Werkelijk: €{_summary['total_actual_variable']:,.2f}**")

    st.markdown("---")

    # ==================== VARIABELE TRANSACTIES ====================
    st.subheader("🧾 Variabele uitgaven — transacties")

    # Add transaction form
    _vcat_options = [r["name"] for r in _exp_items if r["category"] == "variable_budget"] or finance_tracker.VARIABLE_CATEGORIES
    with st.expander("➕ Transactie toevoegen", expanded=not bool(_txns)):
        _tf1, _tf2, _tf3, _tf4 = st.columns([2, 3, 2, 1])
        with _tf1:
            _t_date = st.date_input("Datum", value=date.today(), key="txn_date")
        with _tf2:
            _t_desc = st.text_input("Omschrijving", key="txn_desc")
        with _tf3:
            _t_cat = st.selectbox("Categorie", options=_vcat_options, key="txn_cat")
        with _tf4:
            _t_amt = st.number_input("Bedrag €", min_value=0.0, step=0.01, key="txn_amt")
        if st.button("💾 Toevoegen", key="txn_add") and _t_desc.strip() and _t_amt > 0:
            database.save_budget_transaction(
                _sel_month, str(_t_date), _t_desc.strip(), _t_cat, _t_amt
            )
            st.rerun()

    if _txns:
        _txn_df = pd.DataFrame(_txns)
        _txn_display = _txn_df[["id", "date", "description", "category", "amount"]].copy()
        _txn_display.columns = ["ID", "Datum", "Omschrijving", "Categorie", "Bedrag (€)"]
        _txn_display = _txn_display.sort_values("Datum", ascending=False)

        # Show table + delete button per row
        _del_col, _tbl_col = st.columns([1, 6])
        with _tbl_col:
            st.dataframe(
                _txn_display.style.format({"Bedrag (€)": "€{:,.2f}"}),
                width="stretch",
                hide_index=True,
            )
        with _del_col:
            st.caption("🗑 Verwijder")
            for _, _tr in _txn_display.iterrows():
                if st.button("✕", key=f"del_txn_{_tr['ID']}"):
                    database.delete_budget_transaction(int(_tr["ID"]))
                    st.rerun()
    else:
        st.info("Nog geen transacties. Voeg ze hierboven toe.")

    st.markdown("---")

    # ==================== BOTTOM SUMMARY ====================
    st.subheader("📋 Totaaloverzicht")
    _bs1, _bs2, _bs3 = st.columns(3)
    with _bs1:
        st.metric("Totale inkomsten",    f"€{_summary['total_income']:,.2f}")
        st.metric("Besteedbaar inkomen", f"€{_summary['disposable_income']:,.2f}",
                  help="Inkomsten minus spaardoelen")
    with _bs2:
        st.metric("Verwachte uitgaven",  f"€{_summary['expected_expenses']:,.2f}")
        st.metric("Werkelijke uitgaven", f"€{_summary['total_actual_expenses']:,.2f}")
    with _bs3:
        _er = _summary["expected_result"]
        _ar = _summary["actual_result"]
        st.metric("Verwacht resultaat",  f"{'+'if _er>=0 else ''}€{_er:,.2f}")
        st.metric("Werkelijk resultaat", f"{'+'if _ar>=0 else ''}€{_ar:,.2f}",
                  delta=f"{'+'if (_ar-_er)>=0 else ''}€{(_ar-_er):,.2f} vs verwacht",
                  delta_color="normal")

    st.stop()

# ===========================================================================
# PORTFOLIO SECTION
# ===========================================================================
# Main dashboard
# ---------------------------------------------------------------------------
st.title("📊 My Portfolio Dashboard")

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
