"""Modul deteksi pengeluaran tidak wajar (anomaly & outlier detection) (Fitur V4).

Menyediakan fungsi murni (pure functions) berbasis statistik IQR dan deviasi median,
serta fungsi service untuk memeriksa dan menginspeksi transaksi anomali.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from database import queries
from models import TYPE_EXPENSE, Transaction
from services.common import require_user

SEVERITY_NORMAL = "normal"
SEVERITY_WARNING = "warning"
SEVERITY_EXTREME = "extreme"

AnomalySeverity = Literal["normal", "warning", "extreme"]

MIN_SAMPLE_SIZE_FOR_IQR = 4


@dataclass(frozen=True)
class AnomalyDetectionResult:
    """Hasil evaluasi deteksi anomali pada suatu nominal pengeluaran."""

    is_anomaly: bool
    severity: AnomalySeverity
    reason: str
    sample_size: int
    median_amount: int | None = None
    q1_amount: int | None = None
    q3_amount: int | None = None
    iqr_amount: int | None = None
    upper_bound_amount: int | None = None
    multiplier_of_median: float | None = None


@dataclass(frozen=True)
class AnomalousTransactionItem:
    """Item transaksi yang terdeteksi tidak wajar / anomali."""

    transaction: Transaction
    severity: AnomalySeverity
    reason: str
    median_amount: int
    upper_bound_amount: int


def calculate_quartiles_and_iqr(values: list[int]) -> tuple[int, int, int, int]:
    """Hitung Q1, Median (Q2), Q3, dan IQR dari sekumpulan nominal (integer Rupiah).

    Mengembalikan (q1, median, q3, iqr).
    Memerlukan minimal 1 elemen (jika kosong, kembalikan 0, 0, 0, 0).
    """
    if not values:
        return 0, 0, 0, 0

    sorted_vals = sorted(values)
    n = len(sorted_vals)

    def _median(sub: list[int]) -> int:
        k = len(sub)
        if k == 0:
            return 0
        mid = k // 2
        if k % 2 == 1:
            return sub[mid]
        return (sub[mid - 1] + sub[mid]) // 2

    med = _median(sorted_vals)

    if n == 1:
        return sorted_vals[0], sorted_vals[0], sorted_vals[0], 0

    # Metode Tukey / Moore-McCabe untuk pembagian kuartil
    mid = n // 2
    if n % 2 == 0:
        lower_half = sorted_vals[:mid]
        upper_half = sorted_vals[mid:]
    else:
        lower_half = sorted_vals[:mid]
        upper_half = sorted_vals[mid + 1 :]

    q1 = _median(lower_half)
    q3 = _median(upper_half)
    iqr = max(0, q3 - q1)

    return q1, med, q3, iqr


def detect_expense_anomaly(
    amount: int,
    history_amounts: list[int],
) -> AnomalyDetectionResult:
    """Fungsi murni untuk mendeteksi apakah pengeluaran `amount` adalah anomali / tidak wajar.

    Metodologi statistik:
    1. Jika data riwayat kurang dari MIN_SAMPLE_SIZE_FOR_IQR (4 transaksi), data belum cukup:
       - Kecuali jika nominal >= 10x dari rata-rata data minim yang ada, tandai warning.
       - Selain itu, dianggap normal karena sampel data minim.
    2. Jika sampel cukup (>= 4 transaksi):
       - Dihitung Q1, Median, Q3, dan IQR.
       - Batas atas wajar ringan (mild threshold) = Q3 + (1.5 * IQR)
       - Batas atas ekstrem (extreme threshold) = Q3 + (3.0 * IQR)
       - Jika IQR == 0 (mis. semua transaksi riwayat bernilai sama):
         Batas atas fallback adalah 2x median (warning) dan 3x median (extreme).
    3. Jika amount > extreme_bound -> severity = 'extreme', is_anomaly = True.
    4. Jika amount > mild_bound -> severity = 'warning', is_anomaly = True.
    5. Jika tidak -> severity = 'normal', is_anomaly = False.
    """
    clean_history = [v for v in history_amounts if v > 0]
    n = len(clean_history)

    if amount <= 0:
        return AnomalyDetectionResult(
            is_anomaly=False,
            severity=SEVERITY_NORMAL,
            reason="Nominal tidak bernilai positif.",
            sample_size=n,
        )

    # Kasus data minim (< 4 data historis)
    if n < MIN_SAMPLE_SIZE_FOR_IQR:
        if n == 0:
            return AnomalyDetectionResult(
                is_anomaly=False,
                severity=SEVERITY_NORMAL,
                reason="Belum ada riwayat transaksi sebelumnya untuk pembanding.",
                sample_size=0,
            )
        avg_small = sum(clean_history) // n
        multiplier = round(amount / avg_small, 1) if avg_small > 0 else 1.0
        # Jika nominal sangat fantastis (>= 10x rata-rata data awal)
        if multiplier >= 10.0 and amount >= 500_000:
            return AnomalyDetectionResult(
                is_anomaly=True,
                severity=SEVERITY_WARNING,
                reason=(
                    f"Nominal ({amount:,}) mencapai {multiplier}x dari rata-rata transaksi "
                    f"sebelumnya ({avg_small:,}) meskipun sampel data masih minim ({n} transaksi)."
                ),
                sample_size=n,
                median_amount=avg_small,
                multiplier_of_median=multiplier,
            )
        return AnomalyDetectionResult(
            is_anomaly=False,
            severity=SEVERITY_NORMAL,
            reason=(
                f"Data riwayat belum cukup untuk evaluasi statistik "
                f"({n}/{MIN_SAMPLE_SIZE_FOR_IQR} data)."
            ),
            sample_size=n,
            median_amount=avg_small,
            multiplier_of_median=multiplier,
        )

    q1, median, q3, iqr = calculate_quartiles_and_iqr(clean_history)
    multiplier = round(amount / median, 1) if median > 0 else None

    # Tentukan batas atas
    if iqr > 0:
        mild_upper = q3 + (iqr * 3 // 2)  # Q3 + 1.5 * IQR
        extreme_upper = q3 + (iqr * 3)  # Q3 + 3.0 * IQR
    else:
        # Fallback jika variasi 0 (IQR == 0)
        mild_upper = median * 2
        extreme_upper = median * 3

    # Pastikan batas atas minimal adalah median
    mild_upper = max(mild_upper, median)
    extreme_upper = max(extreme_upper, mild_upper)

    if amount >= extreme_upper:
        reason = (
            f"Nominal Rp {amount:,} jauh melampaui batas wajar ekstrem (Rp {extreme_upper:,}). "
            f"Median pengeluaran adalah Rp {median:,} (anomali {multiplier}x lipat)."
        )
        return AnomalyDetectionResult(
            is_anomaly=True,
            severity=SEVERITY_EXTREME,
            reason=reason,
            sample_size=n,
            median_amount=median,
            q1_amount=q1,
            q3_amount=q3,
            iqr_amount=iqr,
            upper_bound_amount=extreme_upper,
            multiplier_of_median=multiplier,
        )

    if amount >= mild_upper:
        reason = (
            f"Nominal Rp {amount:,} melebihi batas pengeluaran wajar (Rp {mild_upper:,}). "
            f"Median pengeluaran adalah Rp {median:,} ({multiplier}x median)."
        )
        return AnomalyDetectionResult(
            is_anomaly=True,
            severity=SEVERITY_WARNING,
            reason=reason,
            sample_size=n,
            median_amount=median,
            q1_amount=q1,
            q3_amount=q3,
            iqr_amount=iqr,
            upper_bound_amount=mild_upper,
            multiplier_of_median=multiplier,
        )

    return AnomalyDetectionResult(
        is_anomaly=False,
        severity=SEVERITY_NORMAL,
        reason=f"Nominal Rp {amount:,} dalam rentang wajar (batas aman Rp {mild_upper:,}).",
        sample_size=n,
        median_amount=median,
        q1_amount=q1,
        q3_amount=q3,
        iqr_amount=iqr,
        upper_bound_amount=mild_upper,
        multiplier_of_median=multiplier,
    )


# ==============================================================================
# Service Functions (menerima session, user_id)
# ==============================================================================
def check_expense_anomaly(
    session: Session,
    user_id: int,
    amount: int,
    category_id: int | None = None,
) -> AnomalyDetectionResult:
    """Evaluasi apakah suatu nominal pengeluaran tidak wajar dibanding riwayat pengguna.

    Prioritas:
    1. Menggunakan riwayat pengeluaran pada `category_id` yang sama jika mencukupi.
    2. Jika riwayat kategori < MIN_SAMPLE_SIZE_FOR_IQR, menggunakan riwayat seluruh pengeluaran.
    """
    require_user(session, user_id)

    cat_history: list[int] = []
    if category_id is not None:
        cat_txs = queries.list_transactions(
            session,
            user_id,
            type_=TYPE_EXPENSE,
            category_id=category_id,
        )
        cat_history = [t.amount for t in cat_txs if t.amount > 0]

    if len(cat_history) >= MIN_SAMPLE_SIZE_FOR_IQR:
        return detect_expense_anomaly(amount, cat_history)

    # Fallback ke seluruh riwayat pengeluaran pengguna
    all_txs = queries.list_transactions(
        session,
        user_id,
        type_=TYPE_EXPENSE,
    )
    all_history = [t.amount for t in all_txs if t.amount > 0]
    return detect_expense_anomaly(amount, all_history)


def list_anomalous_transactions(
    session: Session,
    user_id: int,
    limit: int = 50,
) -> list[AnomalousTransactionItem]:
    """Cari dan kumpulkan daftar transaksi pengeluaran pengguna yang terdeteksi tidak wajar."""
    require_user(session, user_id)

    all_txs = queries.list_transactions(
        session,
        user_id,
        type_=TYPE_EXPENSE,
    )
    if not all_txs:
        return []

    # Kelompokkan pengeluaran per kategori
    grouped_by_cat: dict[int | None, list[int]] = {}
    for t in all_txs:
        if t.amount > 0:
            grouped_by_cat.setdefault(t.category_id, []).append(t.amount)

    all_amounts = [t.amount for t in all_txs if t.amount > 0]

    anomalies: list[AnomalousTransactionItem] = []
    for t in all_txs:
        cat_history = grouped_by_cat.get(t.category_id, [])
        # Jangan bandingkan transaksi terhadap dirinya sendiri jika sampel kecil
        ref_history = [v for v in cat_history if v != t.amount] or cat_history
        if len(ref_history) < MIN_SAMPLE_SIZE_FOR_IQR:
            ref_history = [v for v in all_amounts if v != t.amount] or all_amounts

        res = detect_expense_anomaly(t.amount, ref_history)
        if res.is_anomaly:
            anomalies.append(
                AnomalousTransactionItem(
                    transaction=t,
                    severity=res.severity,
                    reason=res.reason,
                    median_amount=res.median_amount or 0,
                    upper_bound_amount=res.upper_bound_amount or 0,
                )
            )
            if len(anomalies) >= limit:
                break

    return anomalies
