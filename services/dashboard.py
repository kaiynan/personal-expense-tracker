"""Service dashboard: agregat ringkasan keuangan untuk dashboard pengguna."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from database import queries
from database import reports as report_queries
from database.balances import total_balance
from models import TYPE_EXPENSE, TYPE_INCOME
from services import balances
from services.balances import MethodBalance
from services.budgets import MonthlyBudgetEvaluation, _resolve_month, evaluate_budgets
from services.common import require_user, resolve_date
from services.runway import RunwayMetrics, evaluate_runway_metrics
from utils.dates import format_display, month_bounds, today, week_bounds
from utils.errors import ValidationError


@dataclass(frozen=True)
class DashboardSummary:
    """Ringkasan keuangan untuk tampilan dashboard."""

    month: str
    method_balances: list[MethodBalance]
    total_balance: int
    monthly_income: int
    monthly_expense: int
    monthly_net: int
    transaction_count: int
    budget_amount: int | None
    budget_spent: int
    budget_remaining: int | None
    budget_percentage: float | None
    budget_evaluation: MonthlyBudgetEvaluation | None = None
    runway: RunwayMetrics | None = None


def get_runway_metrics(
    session: Session,
    user_id: int,
    as_of: date | None = None,
) -> RunwayMetrics:
    """Ambil metrik runway, laju harian, dan perkiraan tanggal habis untuk pengguna."""
    user = require_user(session, user_id)
    target_date = resolve_date(user, as_of)
    month_str = target_date.strftime("%Y-%m")
    year = target_date.year
    month_num = target_date.month

    start_date = date(year, month_num, 1)

    # Pengeluaran bulan berjalan hingga target_date
    _, expense, _ = queries.get_monthly_summary(session, user_id, start_date, target_date)
    total_bal = balances.get_total_balance(session, user_id)
    budget = queries.get_budget(session, user_id, month_str, category_id=None)
    budget_amount = budget.amount if budget is not None else None

    return evaluate_runway_metrics(
        as_of=target_date,
        total_expense=expense,
        total_balance=total_bal,
        budget_amount=budget_amount,
    )


def get_dashboard_summary(
    session: Session,
    user_id: int,
    month: str | None = None,
) -> DashboardSummary:
    """Ambil ringkasan dashboard: saldo metode & total, arus kas bulan ini, budget, dan runway."""
    user = require_user(session, user_id)
    target_month = _resolve_month(month, user.timezone)
    year, month_num = (int(x) for x in target_month.split("-"))
    _, last_day = calendar.monthrange(year, month_num)
    start_date = date(year, month_num, 1)
    end_date = date(year, month_num, last_day)

    method_bals = balances.get_method_balances(session, user_id)
    total_bal = balances.get_total_balance(session, user_id)
    income, expense, count = queries.get_monthly_summary(session, user_id, start_date, end_date)
    budget = queries.get_budget(session, user_id, target_month, category_id=None)

    budget_amount = budget.amount if budget is not None else None
    budget_spent = expense
    budget_remaining = (budget_amount - budget_spent) if budget_amount is not None else None
    budget_percentage = (
        round((budget_spent / budget_amount) * 100, 1)
        if budget_amount is not None and budget_amount > 0
        else (0.0 if budget_amount is not None else None)
    )

    budget_eval = evaluate_budgets(session, user_id, target_month)

    # Tentukan tanggal evaluasi runway: hari ini jika bulan berjalan,
    # atau akhir bulan jika melihat bulan historis.
    today_in_tz = today(user.timezone)
    current_month_str = today_in_tz.strftime("%Y-%m")
    as_of_runway = today_in_tz if target_month == current_month_str else end_date

    runway = evaluate_runway_metrics(
        as_of=as_of_runway,
        total_expense=expense,
        total_balance=total_bal,
        budget_amount=budget_amount,
    )

    return DashboardSummary(
        month=target_month,
        method_balances=method_bals,
        total_balance=total_bal,
        monthly_income=income,
        monthly_expense=expense,
        monthly_net=income - expense,
        transaction_count=count,
        budget_amount=budget_amount,
        budget_spent=budget_spent,
        budget_remaining=budget_remaining,
        budget_percentage=budget_percentage,
        budget_evaluation=budget_eval,
        runway=runway,
    )


def _resolve_chart_bounds(
    user_timezone: str, period: str, ref_date: date | None = None
) -> tuple[date, date, str]:
    """Tentukan batas awal dan akhir rentang tanggal berdasarkan nama periode."""
    cleaned = (period or "monthly").lower().strip()
    if cleaned in ("weekly", "mingguan", "week"):
        start, end = week_bounds(user_timezone, on=ref_date)
        label = f"{format_display(start)} s/d {format_display(end)}"
        return start, end, label
    elif cleaned in ("monthly", "bulanan", "month"):
        start, end = month_bounds(user_timezone, on=ref_date)
        label = f"{format_display(start)} s/d {format_display(end)}"
        return start, end, label
    else:
        raise ValidationError(f"Periode '{period}' tidak valid. Pilih 'monthly' atau 'weekly'.")


def get_category_chart_data(
    session: Session,
    user_id: int,
    type_: str,
    period: str = "monthly",
    ref_date: date | None = None,
) -> dict[str, Any]:
    """Data pengeluaran atau pemasukan per kategori untuk Chart.js donut chart."""
    user = require_user(session, user_id)
    if type_ not in (TYPE_EXPENSE, TYPE_INCOME):
        raise ValidationError(f"Tipe '{type_}' tidak valid. Pilih 'expense' atau 'income'.")
    start, end, period_label = _resolve_chart_bounds(user.timezone, period, ref_date=ref_date)
    breakdown = report_queries.get_category_breakdown(session, user_id, type_, start, end)
    labels = [name for _, name, _ in breakdown]
    values = [amt for _, _, amt in breakdown]
    total = sum(values)

    default_colors = [
        "#10b981",
        "#6366f1",
        "#f59e0b",
        "#ec4899",
        "#3b82f6",
        "#8b5cf6",
        "#14b8a6",
        "#f97316",
        "#06b6d4",
        "#84cc16",
        "#ef4444",
        "#64748b",
    ]
    colors = [default_colors[i % len(default_colors)] for i in range(len(values))]

    return {
        "labels": labels,
        "values": values,
        "colors": colors,
        "total": total,
        "period": period,
        "period_label": period_label,
        "type": type_,
    }


def get_cash_flow_chart_data(
    session: Session,
    user_id: int,
    period: str = "monthly",
    ref_date: date | None = None,
) -> dict[str, Any]:
    """Data pemasukan vs pengeluaran harian untuk Chart.js bar chart.

    Hanya menghitung transaksi aktif berjenis 'income' dan 'expense'.
    Transfer dan adjustment dieksklusikan secara ketat.
    """
    user = require_user(session, user_id)
    start, end, period_label = _resolve_chart_bounds(user.timezone, period, ref_date=ref_date)
    daily_flows = report_queries.get_daily_flows(session, user_id, start, end)
    flow_map = {d: (inc, exp) for d, inc, exp in daily_flows}

    labels: list[str] = []
    incomes: list[int] = []
    expenses: list[int] = []

    cur = start
    while cur <= end:
        labels.append(cur.strftime("%d/%m"))
        inc, exp = flow_map.get(cur, (0, 0))
        incomes.append(inc)
        expenses.append(exp)
        cur += timedelta(days=1)

    return {
        "labels": labels,
        "incomes": incomes,
        "expenses": expenses,
        "period": period,
        "period_label": period_label,
        "total_income": sum(incomes),
        "total_expense": sum(expenses),
    }


def get_balance_trend_chart_data(
    session: Session,
    user_id: int,
    period: str = "monthly",
    ref_date: date | None = None,
) -> dict[str, Any]:
    """Data perkembangan saldo kumulatif untuk Chart.js line chart."""
    user = require_user(session, user_id)
    start, end, period_label = _resolve_chart_bounds(user.timezone, period, ref_date=ref_date)

    labels: list[str] = []
    balances_vals: list[int] = []

    cur = start
    while cur <= end:
        labels.append(cur.strftime("%d/%m"))
        balances_vals.append(total_balance(session, user_id, as_of=cur))
        cur += timedelta(days=1)

    return {
        "labels": labels,
        "values": balances_vals,
        "period": period,
        "period_label": period_label,
        "current_balance": balances_vals[-1] if balances_vals else 0,
    }
