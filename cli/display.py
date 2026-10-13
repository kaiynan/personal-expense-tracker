"""Komponen presentasi visual terminal: header, tabel transaksi, kartu dashboard, dan peringatan."""

from __future__ import annotations

import config
from models import (
    TYPE_ADJUSTMENT,
    TYPE_EXPENSE,
    TYPE_INCOME,
    TYPE_TRANSFER,
    AuditLog,
    Transaction,
)
from services.dashboard import DashboardSummary
from services.results import ServiceWarning
from utils.dates import format_display
from utils.formatting import format_rupiah

_TYPE_LABELS = {
    TYPE_INCOME: "Pemasukan",
    TYPE_EXPENSE: "Pengeluaran",
    TYPE_TRANSFER: "Transfer",
    TYPE_ADJUSTMENT: "Koreksi",
}


def print_header(title: str) -> None:
    """Cetak header seksi terminal."""
    print()
    print("=" * 60)
    print(f"  {title.upper()}")
    print("=" * 60)


def print_success(message: str) -> None:
    """Cetak pesan sukses."""
    print(f"[OK] {message}")


def print_error(message: str) -> None:
    """Cetak pesan kesalahan."""
    print(f"[!] {message}")


def print_warning(message: str) -> None:
    """Cetak pesan peringatan."""
    print(f"[PERINGATAN] {message}")


def print_info(message: str) -> None:
    """Cetak pesan informasi."""
    print(f"[*] {message}")


def show_warnings(warnings: list[ServiceWarning]) -> None:
    """Tampilkan peringatan dari service tanpa memblokir alur aplikasi."""
    for w in warnings:
        print_warning(w.message)


def _truncate(text: str, max_len: int) -> str:
    """Pangkas teks jika melebihi panjang maksimum."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def display_transactions(
    transactions: list[Transaction],
    method_map: dict[int, str],
    category_map: dict[int, str],
) -> None:
    """Tampilkan tabel transaksi dalam format ASCII yang rapi."""
    if not transactions:
        print_info("Tidak ada transaksi untuk ditampilkan.")
        return

    headers = ("ID", "Tanggal", "Jenis", "Kategori", "Nominal", "Metode", "Keterangan")
    rows: list[tuple[str, str, str, str, str, str, str]] = []

    for tx in transactions:
        tx_id = f"#{tx.id}"
        tx_date = format_display(tx.date)
        tx_type = _TYPE_LABELS.get(tx.type, tx.type)

        if tx.type == TYPE_TRANSFER:
            cat_name = "-"
            from_name = method_map.get(tx.method_id, f"ID:{tx.method_id}")
            to_name = (
                method_map.get(tx.to_method_id, f"ID:{tx.to_method_id}") if tx.to_method_id else "-"
            )
            method_str = f"{from_name} -> {to_name}"
            amount_str = format_rupiah(tx.amount)
        elif tx.type == TYPE_ADJUSTMENT:
            cat_name = "Koreksi Saldo"
            method_str = method_map.get(tx.method_id, f"ID:{tx.method_id}")
            amount_str = format_rupiah(tx.amount)
        elif tx.type == TYPE_EXPENSE:
            cat_name = category_map.get(tx.category_id, "-") if tx.category_id else "-"
            method_str = method_map.get(tx.method_id, f"ID:{tx.method_id}")
            amount_str = f"-{format_rupiah(tx.amount)}"
        else:  # income
            cat_name = category_map.get(tx.category_id, "-") if tx.category_id else "-"
            method_str = method_map.get(tx.method_id, f"ID:{tx.method_id}")
            amount_str = f"+{format_rupiah(tx.amount)}"

        note_str = _truncate(tx.note or "-", 25)
        rows.append(
            (
                tx_id,
                tx_date,
                tx_type,
                _truncate(cat_name, 16),
                amount_str,
                _truncate(method_str, 20),
                note_str,
            )
        )

    # Hitung lebar kolom
    col_widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            col_widths[idx] = max(col_widths[idx], len(cell))

    sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"
    header_line = "| " + " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers)) + " |"

    print(sep)
    print(header_line)
    print(sep)
    for row in rows:
        # Ratakan kanan untuk nominal, kiri untuk kolom lainnya
        formatted_cells = []
        for i, cell in enumerate(row):
            if i == 4:  # Nominal
                formatted_cells.append(cell.rjust(col_widths[i]))
            else:
                formatted_cells.append(cell.ljust(col_widths[i]))
        print("| " + " | ".join(formatted_cells) + " |")
    print(sep)
    print(f"Total: {len(transactions)} transaksi")


def display_dashboard(summary: DashboardSummary) -> None:
    """Tampilkan ringkasan dashboard keuangan lengkap."""
    print()
    print("=" * 60)
    print(f"                  DASHBOARD KEUANGAN ({summary.month})")
    print("=" * 60)

    # 1. Saldo Saat Ini
    print("\n[SALDO SAAT INI]")
    active_methods = [m for m in summary.method_balances if not m.is_archived]
    if active_methods:
        max_name_len = max(len(m.name) for m in active_methods)
        for m in active_methods:
            print(f"  * {m.name.ljust(max_name_len)} : {format_rupiah(m.balance)}")
    else:
        print("  (Belum ada metode pembayaran aktif)")
    print("  " + "-" * 32)
    print(f"  TOTAL SALDO : {format_rupiah(summary.total_balance)}")

    # 2. Arus Kas Bulan Ini
    print("\n[ARUS KAS BULAN INI]")
    print(f"  Total Pemasukan   : {format_rupiah(summary.monthly_income)}")
    print(f"  Total Pengeluaran : {format_rupiah(summary.monthly_expense)}")
    print("  " + "-" * 32)
    net_sign = "+" if summary.monthly_net > 0 else ""
    print(f"  Arus Kas Bersih   : {net_sign}{format_rupiah(summary.monthly_net)}")
    print(f"  Jumlah Transaksi  : {summary.transaction_count} transaksi")

    # 3. Status Anggaran / Budget
    print("\n[ANGGARAN (BUDGET) BULAN INI]")
    if summary.budget_amount is None:
        print("  Status: Belum diatur untuk bulan ini.")
    else:
        pct = summary.budget_percentage or 0.0
        print(f"  Anggaran Disiapkan : {format_rupiah(summary.budget_amount)}")
        print(f"  Pengeluaran Bulan  : {format_rupiah(summary.budget_spent)} ({pct:.1f}%)")
        remaining = summary.budget_remaining or 0
        rem_sign = "-" if remaining < 0 else ""
        print(f"  Sisa Anggaran      : {rem_sign}{format_rupiah(abs(remaining))}")

        if pct >= config.BUDGET_EXCEEDED_PERCENT:
            print(
                f"  Status             : [!] MELEWATI BATAS ANGGARAN "
                f"(>= {config.BUDGET_EXCEEDED_PERCENT}%)"
            )
        elif pct >= config.BUDGET_WARNING_PERCENT:
            print(
                f"  Status             : [!] MENDEKATI BATAS ANGGARAN "
                f"(>= {config.BUDGET_WARNING_PERCENT}%)"
            )
        else:
            print("  Status             : [OK] Aman (pengeluaran dalam batas)")

    if summary.budget_evaluation and summary.budget_evaluation.category_budgets:
        print("\n[ANGGARAN PER KATEGORI]")
        max_c = max(len(cb.category_name) for cb in summary.budget_evaluation.category_budgets)
        for cb in summary.budget_evaluation.category_budgets:
            tag = (
                "[!] MELEWATI"
                if cb.warning_level == "exceeded"
                else ("[!] MENDEKATI" if cb.warning_level == "warning" else "[OK] Aman")
            )
            print(
                f"  * {cb.category_name.ljust(max_c)} : "
                f"{format_rupiah(cb.spent)} / {format_rupiah(cb.amount)} "
                f"({cb.percentage:5.1f}%) - {tag}"
            )

    if summary.budget_evaluation and summary.budget_evaluation.has_warnings:
        print("\n[PERINGATAN ANGGARAN]")
        for msg in summary.budget_evaluation.warning_messages:
            print(f"  [!] {msg}")

    # 4. Runway & Prediksi Pengeluaran (V4)
    if summary.runway:
        r = summary.runway
        print("\n[RUNWAY & PREDIKSI PENGELUARAN (V4)]")
        print(f"  Laju Pengeluaran Harian : {format_rupiah(r.daily_burn_rate)} / hari")
        print(f"  Proyeksi Akhir Bulan    : {format_rupiah(r.projected_monthly_spend)}")

        if r.recommended_daily_limit is not None:
            if r.is_budget_exceeded:
                print("  Batas Harian Disarankan : Rp 0 / hari (anggaran sudah terlampaui)")
            else:
                print(
                    f"  Batas Harian Disarankan : {format_rupiah(r.recommended_daily_limit)} / hari"
                )

        if r.is_budget_exceeded:
            print("  Perkiraan Anggaran Habis: [!] Sudah terlampaui pada hari ini")
        elif r.budget_exhaustion_date:
            print(
                f"  Perkiraan Anggaran Habis: {r.budget_exhaustion_date.strftime('%d-%m-%Y')} "
                f"({r.budget_days_left} hari lagi)"
            )
        elif r.budget_amount is not None:
            print("  Perkiraan Anggaran Habis: Aman (tidak diproyeksikan habis)")

        if r.is_balance_exhausted:
            print("  Runway Saldo Kas        : [!] Saldo kas telah habis (Rp 0)")
        elif r.balance_runway_days is not None:
            exp_date_str = (
                r.balance_exhaustion_date.strftime("%d-%m-%Y") if r.balance_exhaustion_date else "-"
            )
            print(
                f"  Runway Saldo Kas        : {r.balance_runway_days} hari (habis: {exp_date_str})"
            )
        else:
            print("  Runway Saldo Kas        : Aman (pengeluaran Rp 0 / saldo tidak berkurang)")

    print("=" * 60)


def display_audit_logs(logs: list[AuditLog]) -> None:
    """Tampilkan riwayat perubahan (audit log) dalam format tabel ASCII."""
    if not logs:
        print_info("Tidak ada riwayat perubahan yang tercatat.")
        return

    print()
    print("-" * 80)
    print(f"{'ID':<6} {'WAKTU (UTC)':<17} {'AKSI':<10} {'STATUS':<15} {'KETERANGAN':<30}")
    print("-" * 80)
    for entry in logs:
        time_str = entry.created_at.strftime("%Y-%m-%d %H:%M")
        status_str = "[SUDAH DI-UNDO]" if entry.is_undone else "[AKTIF]"
        desc = _truncate(entry.description or "-", 30)
        print(
            f"#{entry.id:<5} {time_str:<17} {entry.action.upper():<10} {status_str:<15} {desc:<30}"
        )
    print("-" * 80)
