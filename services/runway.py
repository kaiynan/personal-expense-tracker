"""Modul runway, laju pembakaran (burn rate), dan peringatan prediktif (Fitur V4).

Menyediakan fungsi murni (pure functions) tanpa efek samping DB/IO untuk:
1. Perhitungan laju pengeluaran harian (daily burn rate).
2. Proyeksi total pengeluaran akhir bulan (projected month-end spend).
3. Perkiraan tanggal anggaran (budget) habis.
4. Perkiraan durasi hari daya tahan saldo (runway days) dan perkiraan tanggal saldo habis.
5. Batas pengeluaran harian yang disarankan (recommended daily spending limit).
6. Evaluasi ambang batas peringatan prediktif bulanan.

Semua perhitungan nominal mata uang menggunakan integer Rupiah (tidak floating point).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from models import NOTIFICATION_PREDICTIVE_BUDGET, NOTIFICATION_PREDICTIVE_RUNWAY

# Ambang batas peringatan prediktif
PREDICTIVE_THRESH_BUDGET_PROJECTED_EXCEEDED = "budget_proj_exceeded"
PREDICTIVE_THRESH_BUDGET_PROJECTED_WARNING = "budget_proj_warning"
PREDICTIVE_THRESH_BUDGET_EARLY_EXHAUST = "budget_early_exhaust"
PREDICTIVE_THRESH_RUNWAY_CRITICAL = "runway_critical"
PREDICTIVE_THRESH_RUNWAY_WARNING = "runway_warning"


@dataclass(frozen=True)
class RunwayMetrics:
    """Kumpulan metrik burn rate, proyeksi, dan daya tahan saldo (runway)."""

    as_of: date
    days_in_month: int
    elapsed_days: int
    remaining_days_inclusive: int
    total_expense: int
    total_balance: int
    daily_burn_rate: int
    projected_monthly_spend: int
    budget_amount: int | None
    budget_remaining: int | None
    budget_exhaustion_date: date | None
    budget_days_left: int | None
    is_budget_exceeded: bool
    balance_runway_days: int | None
    balance_exhaustion_date: date | None
    is_balance_exhausted: bool
    recommended_daily_limit: int | None


@dataclass(frozen=True)
class PredictiveAlert:
    """Item peringatan prediktif yang dihasilkan dari evaluasi ambang batas."""

    threshold_code: str
    notification_type: str  # 'predictive_budget' atau 'predictive_runway'
    title: str
    message: str
    severity: str  # 'warning' atau 'danger'


def calculate_daily_burn_rate(total_expense: int, elapsed_days: int) -> int:
    """Hitung rata-rata pengeluaran per hari yang sudah berjalan (integer Rupiah).

    Jika total_expense <= 0 atau elapsed_days <= 0, mengembalikan 0.
    """
    if total_expense <= 0 or elapsed_days <= 0:
        return 0
    return total_expense // elapsed_days


def calculate_projected_spend(total_expense: int, elapsed_days: int, days_in_month: int) -> int:
    """Hitung proyeksi pengeluaran hingga akhir bulan berbasis laju harian saat ini.

    - Jika bulan belum berjalan / data kosong (total_expense <= 0), mengembalikan 0.
    - Jika sudah di akhir bulan (elapsed_days >= days_in_month), mengembalikan total_expense.
    - Rumus skala proporsional integer: (total_expense * days_in_month) // elapsed_days.
    """
    if total_expense <= 0 or elapsed_days <= 0 or days_in_month <= 0:
        return 0
    if elapsed_days >= days_in_month:
        return total_expense
    return (total_expense * days_in_month) // elapsed_days


def calculate_budget_exhaustion_date(
    budget_amount: int | None,
    total_expense: int,
    burn_rate: int,
    as_of: date,
) -> tuple[date | None, int | None, bool]:
    """Hitung perkiraan tanggal habisnya budget.

    Mengembalikan tuple: (exhaustion_date, days_left, is_exceeded).
    - Jika budget_amount is None atau budget_amount <= 0: (None, None, False).
    - Jika total_expense >= budget_amount: (as_of, 0, True) -> sudah habis/terlampaui.
    - Jika burn_rate <= 0: (None, None, False) -> pengeluaran 0, budget tidak akan habis.
    - Jika burn_rate > 0:
        sisa = budget_amount - total_expense
        days_left = sisa // burn_rate
        exhaustion_date = as_of + timedelta(days=days_left)
        mengembalikan (exhaustion_date, days_left, False).
    """
    if budget_amount is None or budget_amount <= 0:
        return None, None, False

    if total_expense >= budget_amount:
        return as_of, 0, True

    if burn_rate <= 0:
        return None, None, False

    remaining_budget = budget_amount - total_expense
    days_left = remaining_budget // burn_rate
    exhaustion_date = as_of + timedelta(days=days_left)
    return exhaustion_date, days_left, False


def calculate_balance_runway(
    total_balance: int,
    burn_rate: int,
    as_of: date,
) -> tuple[int | None, date | None, bool]:
    """Hitung sisa hari runway saldo kas dan perkiraan tanggal saldo habis.

    Mengembalikan tuple: (runway_days, exhaustion_date, is_exhausted).
    - Jika total_balance <= 0: (0, as_of, True).
    - Jika burn_rate <= 0: (None, None, False) -> tidak ada pengeluaran, saldo tidak habis.
    - Jika total_balance > 0 dan burn_rate > 0:
        runway_days = total_balance // burn_rate
        exhaustion_date = as_of + timedelta(days=runway_days)
        mengembalikan (runway_days, exhaustion_date, False).
    """
    if total_balance <= 0:
        return 0, as_of, True

    if burn_rate <= 0:
        return None, None, False

    runway_days = total_balance // burn_rate
    exhaustion_date = as_of + timedelta(days=runway_days)
    return runway_days, exhaustion_date, False


def calculate_recommended_daily_limit(
    budget_amount: int | None,
    total_expense: int,
    elapsed_days: int,
    days_in_month: int,
) -> int | None:
    """Hitung batas pengeluaran harian yang disarankan untuk sisa hari di bulan ini.

    - Menghitung hari tersisa secara inklusif: max(1, days_in_month - elapsed_days + 1).
    - Jika budget tidak diatur: mengembalikan None.
    - Jika budget sudah terlampaui (total_expense >= budget_amount): mengembalikan 0.
    - Rumus: (budget_amount - total_expense) // remaining_days_inclusive.
    """
    if budget_amount is None or budget_amount <= 0:
        return None

    if total_expense >= budget_amount:
        return 0

    remaining_days = max(1, days_in_month - elapsed_days + 1)
    remaining_budget = budget_amount - total_expense
    limit = remaining_budget // remaining_days
    return max(0, limit)


def evaluate_runway_metrics(
    as_of: date,
    total_expense: int,
    total_balance: int,
    budget_amount: int | None = None,
) -> RunwayMetrics:
    """Kalkulasi agregat murni metrik runway & proyeksi untuk tanggal evaluasi tertentu."""
    total_days = calendar.monthrange(as_of.year, as_of.month)[1]
    elapsed = max(1, min(as_of.day, total_days))
    remaining_inclusive = max(1, total_days - elapsed + 1)

    burn_rate = calculate_daily_burn_rate(total_expense, elapsed)
    projected_spend = calculate_projected_spend(total_expense, elapsed, total_days)

    b_date, b_days_left, b_exceeded = calculate_budget_exhaustion_date(
        budget_amount=budget_amount,
        total_expense=total_expense,
        burn_rate=burn_rate,
        as_of=as_of,
    )

    r_days, bal_date, bal_exhausted = calculate_balance_runway(
        total_balance=total_balance,
        burn_rate=burn_rate,
        as_of=as_of,
    )

    rec_limit = calculate_recommended_daily_limit(
        budget_amount=budget_amount,
        total_expense=total_expense,
        elapsed_days=elapsed,
        days_in_month=total_days,
    )

    budget_rem = (budget_amount - total_expense) if budget_amount is not None else None

    return RunwayMetrics(
        as_of=as_of,
        days_in_month=total_days,
        elapsed_days=elapsed,
        remaining_days_inclusive=remaining_inclusive,
        total_expense=total_expense,
        total_balance=total_balance,
        daily_burn_rate=burn_rate,
        projected_monthly_spend=projected_spend,
        budget_amount=budget_amount,
        budget_remaining=budget_rem,
        budget_exhaustion_date=b_date,
        budget_days_left=b_days_left,
        is_budget_exceeded=b_exceeded,
        balance_runway_days=r_days,
        balance_exhaustion_date=bal_date,
        is_balance_exhausted=bal_exhausted,
        recommended_daily_limit=rec_limit,
    )


def evaluate_predictive_alerts(metrics: RunwayMetrics) -> list[PredictiveAlert]:
    """Evaluasi metrik runway terhadap ambang batas peringatan prediktif bulanan.

    Menghasilkan daftar peringatan prediktif yang relevan. Setiap alert memiliki
    `threshold_code` unik yang akan digunakan untuk idempotensi per bulan.
    """
    alerts: list[PredictiveAlert] = []
    end_of_month = date(
        metrics.as_of.year,
        metrics.as_of.month,
        metrics.days_in_month,
    )

    # 1. Peringatan Prediktif Budget
    if metrics.budget_amount is not None and metrics.budget_amount > 0:
        # 1a. Proyeksi akhir bulan melewati 100% budget
        if metrics.projected_monthly_spend >= metrics.budget_amount:
            burn = metrics.daily_burn_rate
            proj = metrics.projected_monthly_spend
            bud = metrics.budget_amount
            alerts.append(
                PredictiveAlert(
                    threshold_code=PREDICTIVE_THRESH_BUDGET_PROJECTED_EXCEEDED,
                    notification_type=NOTIFICATION_PREDICTIVE_BUDGET,
                    title="Proyeksi Melebihi Anggaran Bulanan",
                    message=(
                        f"Berdasarkan laju pengeluaran saat ini ({burn:,}/hari), "
                        f"proyeksi belanja akhir bulan Anda mencapai {proj:,}, "
                        f"melebihi anggaran yang disiapkan ({bud:,})."
                    ),
                    severity="danger",
                )
            )
        # 1b. Proyeksi akhir bulan mencapai >= 80% budget (tapi belum 100%)
        elif metrics.projected_monthly_spend >= (metrics.budget_amount * 80) // 100:
            proj = metrics.projected_monthly_spend
            bud = metrics.budget_amount
            alerts.append(
                PredictiveAlert(
                    threshold_code=PREDICTIVE_THRESH_BUDGET_PROJECTED_WARNING,
                    notification_type=NOTIFICATION_PREDICTIVE_BUDGET,
                    title="Proyeksi Mendekati Batas Anggaran",
                    message=(
                        f"Proyeksi pengeluaran akhir bulan Anda diperkirakan mencapai "
                        f"{proj:,} (>=80% dari anggaran {bud:,})."
                    ),
                    severity="warning",
                )
            )

        # 1c. Perkiraan budget habis sebelum akhir bulan (dan belum berstatus terlampaui hari ini)
        if (
            not metrics.is_budget_exceeded
            and metrics.budget_exhaustion_date is not None
            and metrics.budget_exhaustion_date <= end_of_month
        ):
            b_exp_str = metrics.budget_exhaustion_date.strftime("%d-%m-%Y")
            alerts.append(
                PredictiveAlert(
                    threshold_code=PREDICTIVE_THRESH_BUDGET_EARLY_EXHAUST,
                    notification_type=NOTIFICATION_PREDICTIVE_BUDGET,
                    title="Perkiraan Anggaran Habis Sebelum Akhir Bulan",
                    message=(
                        f"Dengan laju pengeluaran saat ini, anggaran bulanan Anda diperkirakan "
                        f"akan habis pada {b_exp_str} "
                        f"({metrics.budget_days_left} hari lagi)."
                    ),
                    severity="danger",
                )
            )

    # 2. Peringatan Prediktif Runway Saldo
    if metrics.balance_runway_days is not None:
        bal_exp_str = (
            metrics.balance_exhaustion_date.strftime("%d-%m-%Y")
            if metrics.balance_exhaustion_date
            else "-"
        )
        r_days = metrics.balance_runway_days
        if metrics.balance_runway_days <= 7:
            alerts.append(
                PredictiveAlert(
                    threshold_code=PREDICTIVE_THRESH_RUNWAY_CRITICAL,
                    notification_type=NOTIFICATION_PREDICTIVE_RUNWAY,
                    title="Runway Saldo Kritis (<= 7 Hari)",
                    message=(
                        f"Perhatian! Sisa saldo kas Anda hanya cukup untuk bertahan {r_days} hari "
                        f"dengan laju harian saat ini (perkiraan habis: {bal_exp_str})."
                    ),
                    severity="danger",
                )
            )
        elif metrics.balance_runway_days <= 14:
            alerts.append(
                PredictiveAlert(
                    threshold_code=PREDICTIVE_THRESH_RUNWAY_WARNING,
                    notification_type=NOTIFICATION_PREDICTIVE_RUNWAY,
                    title="Peringatan Runway Saldo (<= 14 Hari)",
                    message=(
                        f"Sisa saldo kas Anda diperkirakan bertahan {r_days} hari lagi "
                        f"pada laju pengeluaran saat ini."
                    ),
                    severity="warning",
                )
            )

    return alerts
