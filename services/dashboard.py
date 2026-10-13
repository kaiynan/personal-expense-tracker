"""Service dashboard: agregat ringkasan keuangan untuk dashboard pengguna."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from database import queries
from services import balances
from services.balances import MethodBalance
from services.budgets import MonthlyBudgetEvaluation, _resolve_month, evaluate_budgets
from services.common import require_user, resolve_date
from services.runway import RunwayMetrics, evaluate_runway_metrics
from utils.dates import today


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
