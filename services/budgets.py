"""Service budget: kelola budget bulanan, mingguan, rollover, serta evaluasi peringatan 80%/100%."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

import config
from database import queries
from models import TYPE_EXPENSE, Budget, WeeklyBudget
from services.common import require_user
from utils.dates import today
from utils.errors import ValidationError
from utils.formatting import format_rupiah
from utils.validators import parse_date, validate_amount, validate_category

_MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}$")


def _resolve_month(month: object | None, timezone_name: str) -> str:
    """Validasi teks YYYY-MM atau gunakan bulan ini menurut zona waktu pengguna."""
    if month is None:
        return today(timezone_name).strftime("%Y-%m")
    if not isinstance(month, str):
        raise ValidationError("Bulan harus berupa teks berformat YYYY-MM.")
    text = month.strip()
    if not _MONTH_PATTERN.fullmatch(text):
        raise ValidationError("Format bulan harus YYYY-MM (contoh: 2026-10).")
    try:
        datetime.strptime(text, "%Y-%m")
    except ValueError:
        raise ValidationError("Bulan tidak valid.") from None
    return text


def _previous_month(month_str: str) -> str:
    """Hitung string YYYY-MM satu bulan sebelum `month_str`."""
    year, month_num = (int(x) for x in month_str.split("-"))
    if month_num == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month_num - 1:02d}"


def _next_month(month_str: str) -> str:
    """Hitung string YYYY-MM satu bulan setelah `month_str`."""
    year, month_num = (int(x) for x in month_str.split("-"))
    if month_num == 12:
        return f"{year + 1:04d}-01"
    return f"{year:04d}-{month_num + 1:02d}"


def _resolve_week_start(on: object | None, timezone_name: str) -> date:
    """Kembalikan tanggal Senin dari minggu yang memuat `on` (atau hari ini jika None)."""
    ref_date = today(timezone_name) if on is None else parse_date(on, "Tanggal minggu")
    return ref_date - timedelta(days=ref_date.weekday())


@dataclass(frozen=True)
class BudgetItemStatus:
    """Status satu anggaran (total atau per kategori) terhadap pengeluaran aktual."""

    category_id: int | None
    category_name: str
    amount: int
    spent: int
    remaining: int
    percentage: float
    warning_level: str  # "safe" (< 80%), "warning" (>= 80%), "exceeded" (>= 100%)
    base_amount: int = 0
    carryover_amount: int = 0
    is_rollover_enabled: bool = False

    @property
    def is_warning(self) -> bool:
        """True bila mencapai ambang batas peringatan atau melewatinya."""
        return self.warning_level in ("warning", "exceeded")


@dataclass(frozen=True)
class MonthlyBudgetEvaluation:
    """Hasil evaluasi seluruh budget untuk satu bulan."""

    month: str
    total_budget: BudgetItemStatus | None
    category_budgets: list[BudgetItemStatus]
    has_warnings: bool
    warning_messages: list[str]


@dataclass(frozen=True)
class WeeklyBudgetEvaluation:
    """Hasil evaluasi seluruh budget mingguan untuk satu minggu (Senin s.d. Minggu)."""

    week_start: date
    week_end: date
    total_budget: BudgetItemStatus | None
    category_budgets: list[BudgetItemStatus]
    has_warnings: bool
    warning_messages: list[str]


@dataclass(frozen=True)
class RolloverResultItem:
    """Hasil rollover satu budget bulanan (total atau per kategori) ke bulan tujuan."""

    category_id: int | None
    category_name: str
    from_month: str
    to_month: str
    previous_effective_budget: int
    previous_spent: int
    surplus_carried_over: int
    new_base_amount: int
    new_effective_budget: int


def _validate_carryover(carryover_amount: object | None) -> int | None:
    """Validasi nominal sisa budget bawaan (carryover >= 0)."""
    if carryover_amount is None:
        return None
    if isinstance(carryover_amount, bool) or not isinstance(carryover_amount, int):
        raise ValidationError("Nominal carryover harus berupa bilangan bulat.")
    if carryover_amount < 0:
        raise ValidationError("Sisa budget bawaan (carryover) tidak boleh negatif.")
    return carryover_amount


def set_monthly_budget(
    session: Session,
    user_id: int,
    amount: object,
    month: str | None = None,
    *,
    is_rollover_enabled: bool | None = None,
    carryover_amount: int | None = None,
) -> Budget:
    """Atur budget total bulanan milik `user_id`."""
    user = require_user(session, user_id)
    budget_amount = validate_amount(amount, "Nominal budget")
    budget_month = _resolve_month(month, user.timezone)
    valid_carryover = _validate_carryover(carryover_amount)
    return queries.set_budget(
        session,
        user_id,
        budget_month,
        budget_amount,
        category_id=None,
        carryover_amount=valid_carryover,
        is_rollover_enabled=is_rollover_enabled,
    )


def get_monthly_budget(
    session: Session,
    user_id: int,
    month: str | None = None,
) -> Budget | None:
    """Ambil budget total bulanan milik `user_id`."""
    user = require_user(session, user_id)
    budget_month = _resolve_month(month, user.timezone)
    return queries.get_budget(session, user_id, budget_month, category_id=None)


def set_category_budget(
    session: Session,
    user_id: int,
    category_id: int,
    amount: object,
    month: str | None = None,
    *,
    is_rollover_enabled: bool | None = None,
    carryover_amount: int | None = None,
) -> Budget:
    """Atur budget untuk satu kategori pengeluaran milik `user_id`."""
    user = require_user(session, user_id)
    category = validate_category(session, user_id, category_id, expected_type=TYPE_EXPENSE)
    budget_amount = validate_amount(amount, f"Nominal budget kategori '{category.name}'")
    budget_month = _resolve_month(month, user.timezone)
    valid_carryover = _validate_carryover(carryover_amount)
    return queries.set_budget(
        session,
        user_id,
        budget_month,
        budget_amount,
        category_id=category.id,
        carryover_amount=valid_carryover,
        is_rollover_enabled=is_rollover_enabled,
    )


def get_category_budget(
    session: Session,
    user_id: int,
    category_id: int,
    month: str | None = None,
) -> Budget | None:
    """Ambil budget satu kategori pengeluaran milik `user_id`."""
    user = require_user(session, user_id)
    budget_month = _resolve_month(month, user.timezone)
    return queries.get_budget(session, user_id, budget_month, category_id=category_id)


def list_budgets(
    session: Session,
    user_id: int,
    month: str | None = None,
) -> list[Budget]:
    """Daftar semua budget milik `user_id` pada bulan tertentu (total & per kategori)."""
    user = require_user(session, user_id)
    budget_month = _resolve_month(month, user.timezone)
    return queries.list_budgets(session, user_id, budget_month)


def evaluate_budgets(
    session: Session,
    user_id: int,
    month: str | None = None,
) -> MonthlyBudgetEvaluation:
    """Evaluasi pemakaian budget bulanan dan per kategori terhadap pengeluaran aktual.

    Peringatan:
    - >= config.BUDGET_EXCEEDED_PERCENT (100%): "exceeded" (melewati batas)
    - >= config.BUDGET_WARNING_PERCENT (80%): "warning" (mendekati batas)
    - lainnya: "safe"
    """
    user = require_user(session, user_id)
    budget_month = _resolve_month(month, user.timezone)
    year, month_num = (int(x) for x in budget_month.split("-"))
    _, last_day = calendar.monthrange(year, month_num)
    start_date = date(year, month_num, 1)
    end_date = date(year, month_num, last_day)

    # Ambil pengeluaran aktual lewat query agregat database
    _, total_expense, _ = queries.get_monthly_summary(session, user_id, start_date, end_date)
    cat_expenses = queries.get_monthly_category_expenses(session, user_id, start_date, end_date)

    # Peta kategori id -> nama
    cats = queries.list_categories(session, user_id, include_archived=True, include_system=True)
    cat_names = {c.id: c.name for c in cats}

    budgets = queries.list_budgets(session, user_id, budget_month)

    total_status: BudgetItemStatus | None = None
    category_statuses: list[BudgetItemStatus] = []
    warning_messages: list[str] = []

    for b in budgets:
        is_total = b.category_id is None
        label = (
            "Total Bulanan"
            if is_total
            else cat_names.get(b.category_id, f"Kategori #{b.category_id}")
        )
        spent = total_expense if is_total else cat_expenses.get(b.category_id, 0)
        effective_amount = b.amount + b.carryover_amount
        remaining = effective_amount - spent
        pct = round((spent / effective_amount) * 100, 1) if effective_amount > 0 else 0.0

        if pct >= config.BUDGET_EXCEEDED_PERCENT:
            level = "exceeded"
            warning_messages.append(
                f"Budget {label} MELEWATI BATAS! Terpakai {pct:.1f}% "
                f"({format_rupiah(spent)} dari {format_rupiah(effective_amount)}, "
                f"lebih {format_rupiah(abs(remaining))})."
            )
        elif pct >= config.BUDGET_WARNING_PERCENT:
            level = "warning"
            warning_messages.append(
                f"Budget {label} MENDEKATI BATAS! Terpakai {pct:.1f}% "
                f"({format_rupiah(spent)} dari {format_rupiah(effective_amount)}, "
                f"sisa {format_rupiah(remaining)})."
            )
        else:
            level = "safe"

        status_item = BudgetItemStatus(
            category_id=b.category_id,
            category_name=label,
            amount=effective_amount,
            spent=spent,
            remaining=remaining,
            percentage=pct,
            warning_level=level,
            base_amount=b.amount,
            carryover_amount=b.carryover_amount,
            is_rollover_enabled=b.is_rollover_enabled,
        )

        if is_total:
            total_status = status_item
        else:
            category_statuses.append(status_item)

    return MonthlyBudgetEvaluation(
        month=budget_month,
        total_budget=total_status,
        category_budgets=category_statuses,
        has_warnings=bool(warning_messages),
        warning_messages=warning_messages,
    )


def check_budget_warnings(
    session: Session,
    user_id: int,
    month: str | None = None,
) -> list[str]:
    """Kembalikan daftar pesan peringatan budget mendekati (>=80%) atau melewati (>=100%)."""
    evaluation = evaluate_budgets(session, user_id, month=month)
    return evaluation.warning_messages


def set_weekly_budget(
    session: Session,
    user_id: int,
    amount: object,
    on: object | None = None,
    category_id: int | None = None,
) -> WeeklyBudget:
    """Atur budget mingguan (total jika `category_id=None`, atau per kategori pengeluaran)."""
    user = require_user(session, user_id)
    if category_id is not None:
        category = validate_category(session, user_id, category_id, expected_type=TYPE_EXPENSE)
        cid: int | None = category.id
        field_label = f"Nominal budget mingguan kategori '{category.name}'"
    else:
        cid = None
        field_label = "Nominal budget mingguan"
    budget_amount = validate_amount(amount, field_label)
    w_start = _resolve_week_start(on, user.timezone)
    return queries.set_weekly_budget(session, user_id, w_start, budget_amount, category_id=cid)


def get_weekly_budget(
    session: Session,
    user_id: int,
    on: object | None = None,
    category_id: int | None = None,
) -> WeeklyBudget | None:
    """Ambil budget mingguan milik `user_id` untuk minggu yang memuat `on`."""
    user = require_user(session, user_id)
    w_start = _resolve_week_start(on, user.timezone)
    return queries.get_weekly_budget(session, user_id, w_start, category_id=category_id)


def list_weekly_budgets(
    session: Session,
    user_id: int,
    on: object | None = None,
) -> list[WeeklyBudget]:
    """Daftar seluruh budget mingguan milik `user_id` pada minggu yang memuat `on`."""
    user = require_user(session, user_id)
    w_start = _resolve_week_start(on, user.timezone)
    return queries.list_weekly_budgets(session, user_id, w_start)


def evaluate_weekly_budgets(
    session: Session,
    user_id: int,
    on: object | None = None,
) -> WeeklyBudgetEvaluation:
    """Evaluasi pemakaian budget mingguan (Senin s.d. Minggu) terhadap pengeluaran aktual."""
    user = require_user(session, user_id)
    w_start = _resolve_week_start(on, user.timezone)
    w_end = w_start + timedelta(days=6)

    _, total_expense, _ = queries.get_monthly_summary(session, user_id, w_start, w_end)
    cat_expenses = queries.get_monthly_category_expenses(session, user_id, w_start, w_end)

    cats = queries.list_categories(session, user_id, include_archived=True, include_system=True)
    cat_names = {c.id: c.name for c in cats}

    w_budgets = queries.list_weekly_budgets(session, user_id, w_start)

    total_status: BudgetItemStatus | None = None
    category_statuses: list[BudgetItemStatus] = []
    warning_messages: list[str] = []

    for wb in w_budgets:
        is_total = wb.category_id is None
        label = (
            "Total Mingguan"
            if is_total
            else cat_names.get(wb.category_id, f"Kategori #{wb.category_id}")
        )
        spent = total_expense if is_total else cat_expenses.get(wb.category_id, 0)
        remaining = wb.amount - spent
        pct = round((spent / wb.amount) * 100, 1) if wb.amount > 0 else 0.0

        if pct >= config.BUDGET_EXCEEDED_PERCENT:
            level = "exceeded"
            warning_messages.append(
                f"Budget Mingguan {label} MELEWATI BATAS! Terpakai {pct:.1f}% "
                f"({format_rupiah(spent)} dari {format_rupiah(wb.amount)}, "
                f"lebih {format_rupiah(abs(remaining))})."
            )
        elif pct >= config.BUDGET_WARNING_PERCENT:
            level = "warning"
            warning_messages.append(
                f"Budget Mingguan {label} MENDEKATI BATAS! Terpakai {pct:.1f}% "
                f"({format_rupiah(spent)} dari {format_rupiah(wb.amount)}, "
                f"sisa {format_rupiah(remaining)})."
            )
        else:
            level = "safe"

        status_item = BudgetItemStatus(
            category_id=wb.category_id,
            category_name=label,
            amount=wb.amount,
            spent=spent,
            remaining=remaining,
            percentage=pct,
            warning_level=level,
            base_amount=wb.amount,
            carryover_amount=0,
            is_rollover_enabled=False,
        )

        if is_total:
            total_status = status_item
        else:
            category_statuses.append(status_item)

    return WeeklyBudgetEvaluation(
        week_start=w_start,
        week_end=w_end,
        total_budget=total_status,
        category_budgets=category_statuses,
        has_warnings=bool(warning_messages),
        warning_messages=warning_messages,
    )


def rollover_budgets(
    session: Session,
    user_id: int,
    from_month: str | None = None,
    to_month: str | None = None,
    *,
    only_enabled: bool = False,
) -> list[RolloverResultItem]:
    """Bawa sisa budget positif dari `from_month` ke `to_month` (rollover).

    Aturan:
    - Jika `from_month` dan `to_month` keduanya None, `to_month` adalah bulan ini
      dan `from_month` adalah bulan sebelumnya.
    - Jika hanya `from_month` yang diberikan, `to_month` otomatis bulan berikutnya.
    - Jika hanya `to_month` yang diberikan, `from_month` otomatis bulan sebelumnya.
    - Sisa budget yang dibawa (`surplus_carried_over`) = `max(0, effective_budget - spent)`.
      Defisit pengeluaran (sisa negatif) tidak mengurangi budget bulan berikutnya.
    - Jika budget pada `to_month` belum ada, dibuatkan dengan nominal dasar (`amount`)
      yang sama dengan `from_month` ditambah `carryover_amount = surplus`.
      Jika sudah ada, `amount` dasar pada `to_month` dipertahankan dan `carryover_amount`
      diperbarui dengan `surplus`.
    """
    user = require_user(session, user_id)
    if from_month is None and to_month is None:
        target_month = _resolve_month(None, user.timezone)
        source_month = _previous_month(target_month)
    elif from_month is not None and to_month is None:
        source_month = _resolve_month(from_month, user.timezone)
        target_month = _next_month(source_month)
    elif from_month is None and to_month is not None:
        target_month = _resolve_month(to_month, user.timezone)
        source_month = _previous_month(target_month)
    else:
        source_month = _resolve_month(from_month, user.timezone)
        target_month = _resolve_month(to_month, user.timezone)

    if source_month >= target_month:
        raise ValidationError("Bulan asal rollover harus lebih awal dari bulan tujuan.")

    source_budgets = queries.list_budgets(session, user_id, source_month)
    if only_enabled:
        source_budgets = [b for b in source_budgets if b.is_rollover_enabled]

    if not source_budgets:
        return []

    year, month_num = (int(x) for x in source_month.split("-"))
    _, last_day = calendar.monthrange(year, month_num)
    start_date = date(year, month_num, 1)
    end_date = date(year, month_num, last_day)

    _, total_expense, _ = queries.get_monthly_summary(session, user_id, start_date, end_date)
    cat_expenses = queries.get_monthly_category_expenses(session, user_id, start_date, end_date)

    cats = queries.list_categories(session, user_id, include_archived=True, include_system=True)
    cat_names = {c.id: c.name for c in cats}

    results: list[RolloverResultItem] = []
    for b in source_budgets:
        is_total = b.category_id is None
        label = (
            "Total Bulanan"
            if is_total
            else cat_names.get(b.category_id, f"Kategori #{b.category_id}")
        )
        spent = total_expense if is_total else cat_expenses.get(b.category_id, 0)
        prev_effective = b.amount + b.carryover_amount
        surplus = max(0, prev_effective - spent)

        existing_target = queries.get_budget(session, user_id, target_month, b.category_id)
        new_base = existing_target.amount if existing_target is not None else b.amount

        b.is_rollover_enabled = True
        queries.set_budget(
            session,
            user_id,
            target_month,
            new_base,
            category_id=b.category_id,
            carryover_amount=surplus,
            is_rollover_enabled=True,
        )

        results.append(
            RolloverResultItem(
                category_id=b.category_id,
                category_name=label,
                from_month=source_month,
                to_month=target_month,
                previous_effective_budget=prev_effective,
                previous_spent=spent,
                surplus_carried_over=surplus,
                new_base_amount=new_base,
                new_effective_budget=new_base + surplus,
            )
        )

    session.flush()
    return results
