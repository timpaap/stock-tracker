"""
finance_tracker.py — Budget & monthly income/expense tracking

Mirrors the 'Template money.xlsx' layout:
  - Vaste inkomsten   (fixed income)
  - Variabele inkomsten (variable income)
  - Vaste uitgaven    (fixed expenses)
  - Sparen            (savings goals)
  - Variabele uitgaven (variable expense transactions with category budgets)

All data is stored in SQLite via database.py functions.
"""

import calendar
from datetime import date, datetime

import openpyxl
import plotly.graph_objects as go


# ---------------------------------------------------------------------------
# Default line items — pre-populated from the Excel template
# ---------------------------------------------------------------------------

DEFAULT_FIXED_INCOME = [
    ("Salaris netto",           0.0),
    ("Telefoonvergoeding",      0.0),
    ("Sportvergoeding",         0.0),
    ("Reiskostenvergoeding",    0.0),
]

DEFAULT_VARIABLE_INCOME = [
    ("Vorige maand",  0.0),
    ("Koopzegels",    0.0),
]

DEFAULT_FIXED_EXPENSES = [
    ("Huur",                          0.0),
    ("Buffer huisrekening",           0.0),
    ("Energie",                       0.0),
    ("Ziggo",                         0.0),
    ("Wasmachine en droger",          0.0),
    ("Water",                         0.0),
    ("Afvalstofheffing",              0.0),
    ("Waterschapsbelasting",          0.0),
    ("Woonverzekering",               0.0),
    ("Aansprakelijkheidsverzekering", 0.0),
    ("Zorgverzekering",               0.0),
    ("Vodafone",                      0.0),
    ("Apple",                         0.0),
    ("Soundcloud",                    0.0),
    ("Prime",                         0.0),
    ("ING",                           0.0),
    ("Studieschuld",                  0.0),
]

DEFAULT_SAVINGS = [
    ("Vakantie",          0.0),
    ("Beleggersrekening", 0.0),
    ("Kleding",           0.0),
    ("Leuke uitgaven",    0.0),
    ("Buffer",            0.0),
]

DEFAULT_VARIABLE_BUDGETS = [
    ("Supermarkt",              0.0),
    ("Horeca + drank",          0.0),
    ("Sport/apotheek/verzorging", 0.0),
    ("Openbaar vervoer",        0.0),
    ("Cadeaus",                 0.0),
    ("Overige",                 0.0),
]

VARIABLE_CATEGORIES = [b[0] for b in DEFAULT_VARIABLE_BUDGETS]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def month_label(month_str: str) -> str:
    """'2026-04' → 'April 2026'"""
    try:
        dt = datetime.strptime(month_str, "%Y-%m")
        return dt.strftime("%B %Y")
    except ValueError:
        return month_str


def today_month() -> str:
    return date.today().strftime("%Y-%m")


def seed_month_defaults(db, month: str) -> None:
    """Insert default line items for a new month (only if they don't exist yet)."""
    for name, amount in DEFAULT_FIXED_INCOME:
        db.save_budget_income(month, "fixed", name, amount)
    for name, amount in DEFAULT_VARIABLE_INCOME:
        db.save_budget_income(month, "variable", name, amount)
    for name, amount in DEFAULT_FIXED_EXPENSES:
        db.save_budget_expense(month, "fixed", name, amount)
    for name, amount in DEFAULT_SAVINGS:
        db.save_budget_expense(month, "savings", name, amount)
    for name, amount in DEFAULT_VARIABLE_BUDGETS:
        db.save_budget_expense(month, "variable_budget", name, amount)


# ---------------------------------------------------------------------------
# Summary calculation
# ---------------------------------------------------------------------------

def calculate_summary(income_items, expense_items, transactions):
    """
    Returns a dict with all the summary figures shown in the Excel bottom rows.
    """
    fixed_income    = sum(r["amount"] for r in income_items if r["category"] == "fixed")
    variable_income = sum(r["amount"] for r in income_items if r["category"] == "variable")
    total_income    = fixed_income + variable_income

    fixed_expenses  = sum(r["amount"] for r in expense_items if r["category"] == "fixed")
    savings         = sum(r["amount"] for r in expense_items if r["category"] == "savings")
    var_budgets     = {r["name"]: r["amount"] for r in expense_items if r["category"] == "variable_budget"}
    total_var_budget = sum(var_budgets.values())

    expected_expenses = fixed_expenses + savings + total_var_budget
    disposable_income = total_income - savings  # besteedbaar inkomen

    # Actual variable spend from transactions
    actual_by_cat: dict[str, float] = {}
    for t in transactions:
        actual_by_cat[t["category"]] = actual_by_cat.get(t["category"], 0.0) + t["amount"]

    total_actual_variable = sum(actual_by_cat.values())
    total_actual_expenses = fixed_expenses + total_actual_variable

    expected_result = total_income - expected_expenses
    actual_result   = total_income - fixed_expenses - savings - total_actual_variable

    # Day-of-month progress for pace indicator
    today = date.today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    day_fraction  = today.day / days_in_month

    return {
        "fixed_income":          fixed_income,
        "variable_income":       variable_income,
        "total_income":          total_income,
        "disposable_income":     disposable_income,
        "fixed_expenses":        fixed_expenses,
        "savings":               savings,
        "total_var_budget":      total_var_budget,
        "expected_expenses":     expected_expenses,
        "total_actual_variable": total_actual_variable,
        "total_actual_expenses": total_actual_expenses,
        "expected_result":       expected_result,
        "actual_result":         actual_result,
        "actual_by_cat":         actual_by_cat,
        "var_budgets":           var_budgets,
        "day_fraction":          day_fraction,
    }


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def variable_spending_chart(actual_by_cat: dict[str, float], var_budgets: dict[str, float]) -> go.Figure:
    """Horizontal bar chart: actual spend vs budget per variable category."""
    cats    = list(var_budgets.keys())
    budgets = [var_budgets.get(c, 0.0) for c in cats]
    actuals = [actual_by_cat.get(c, 0.0) for c in cats]

    colors = []
    for a, b in zip(actuals, budgets):
        if b == 0:
            colors.append("#90A4AE")
        elif a <= b * 0.75:
            colors.append("#4CAF50")
        elif a <= b:
            colors.append("#FF9800")
        else:
            colors.append("#F44336")

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Budget",
        y=cats,
        x=budgets,
        orientation="h",
        marker_color="rgba(100,149,237,0.2)",
        marker_line_color="rgba(100,149,237,0.8)",
        marker_line_width=2,
        hovertemplate="<b>%{y}</b><br>Budget: €%{x:,.2f}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Werkelijk",
        y=cats,
        x=actuals,
        orientation="h",
        marker_color=colors,
        hovertemplate="<b>%{y}</b><br>Werkelijk: €%{x:,.2f}<extra></extra>",
    ))
    fig.update_layout(
        barmode="overlay",
        title="Variabele uitgaven: werkelijk vs budget",
        xaxis=dict(title="€", gridcolor="#eee"),
        yaxis=dict(autorange="reversed"),
        plot_bgcolor="white",
        paper_bgcolor="white",
        legend=dict(orientation="h", y=1.08, x=1, xanchor="right"),
        margin=dict(t=60, b=20, l=10, r=10),
    )
    return fig


def income_vs_expenses_chart(summary: dict) -> go.Figure:
    """Simple bar overview: income, fixed costs, savings, variable spend, result."""
    labels = ["Inkomsten", "Vaste lasten", "Sparen", "Variabel", "Resultaat"]
    values = [
        summary["total_income"],
        summary["fixed_expenses"],
        summary["savings"],
        summary["total_actual_variable"],
        summary["actual_result"],
    ]
    colors = ["#4CAF50", "#EF5350", "#42A5F5", "#FF9800",
              "#4CAF50" if summary["actual_result"] >= 0 else "#EF5350"]

    fig = go.Figure(go.Bar(
        x=labels,
        y=values,
        marker_color=colors,
        text=[f"€{v:,.0f}" for v in values],
        textposition="outside",
        hovertemplate="<b>%{x}</b><br>€%{y:,.2f}<extra></extra>",
    ))
    fig.update_layout(
        title="Maandoverzicht",
        yaxis=dict(title="€", gridcolor="#eee"),
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(t=60, b=20, l=10, r=10),
    )
    return fig


# ---------------------------------------------------------------------------
# Excel import
# ---------------------------------------------------------------------------

def import_from_excel(path: str, month: str, db) -> dict:
    """
    Parse a 'Template money.xlsx'-style workbook and load its values into the DB
    for the given month.  Returns a summary of what was imported.
    """
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    imported = {"income": 0, "expenses": 0, "transactions": 0}

    # ---- Fixed income (col A=name, col B=value, rows 2–5) ----
    fixed_income_map = {}
    for r in rows[1:6]:  # rows 2-6
        name, val = r[0], r[1]
        if name and name != "Totaal" and val is not None:
            try:
                db.save_budget_income(month, "fixed", str(name), float(val))
                fixed_income_map[name] = float(val)
                imported["income"] += 1
            except Exception:
                pass

    # ---- Variable income (col A=name, col B=value, rows 10–11) ----
    for r in rows[9:12]:
        name, val = r[0], r[1]
        if name and name != "Totaal" and val is not None:
            try:
                db.save_budget_income(month, "variable", str(name), float(val))
                imported["income"] += 1
            except Exception:
                pass

    # ---- Fixed expenses (col D=name, col E=value) ----
    for r in rows[1:20]:
        name, val = r[3], r[4]
        if name and name not in ("Vaste uitgaven", "Variabale uitgaven", "Totaal") and val is not None:
            try:
                db.save_budget_expense(month, "fixed", str(name), float(val))
                imported["expenses"] += 1
            except Exception:
                pass

    # ---- Savings (col G=name, col H=value) ----
    for r in rows[1:8]:
        name, val = r[6], r[7]
        if name and name != "Totaal" and val is not None:
            try:
                db.save_budget_expense(month, "savings", str(name), float(val))
                imported["expenses"] += 1
            except Exception:
                pass

    # ---- Variable budgets (col D=name, col F=expected, rows 23–29) ----
    for r in rows[21:30]:
        name, _actual, budget = r[3], r[4], r[5]
        if name and name != "Totaal" and budget is not None:
            try:
                db.save_budget_expense(month, "variable_budget", str(name), float(budget))
                imported["expenses"] += 1
            except Exception:
                pass

    # ---- Variable transactions (col I=amount, col J=amount, col K=category) ----
    # Columns J (index 9) = bedrag, K (index 10) = categorie
    for i, r in enumerate(rows[1:], start=2):
        amount = r[9]
        cat    = r[10]
        if amount is not None and cat is not None:
            try:
                db.save_budget_transaction(month, None, f"Import rij {i}", str(cat), float(amount))
                imported["transactions"] += 1
            except Exception:
                pass

    return imported
