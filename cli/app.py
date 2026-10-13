"""Aplikasi CLI: menu interaktif terminal Personal Expense Tracker."""

from __future__ import annotations

import logging
from typing import Any

from cli import actions
from cli.display import print_error, print_info
from cli.inputs import InputCancelled, _raw_prompt

logger = logging.getLogger(__name__)

MENU_ITEMS: list[tuple[str, Any]] = [
    ("Tambah Pemasukan", actions.action_add_income),
    ("Tambah Pengeluaran", actions.action_add_expense),
    ("Transfer Antar Metode", actions.action_add_transfer),
    ("Atur Saldo Awal", actions.action_set_opening_balance),
    ("Koreksi Saldo (Adjustment)", actions.action_adjust_balance),
    ("Riwayat Transaksi", actions.action_list_transactions),
    ("Filter Transaksi", actions.action_filter_transactions),
    ("Cari Transaksi", actions.action_search_transactions),
    ("Edit Transaksi", actions.action_edit_transaction),
    ("Hapus Transaksi (Soft Delete)", actions.action_delete_transaction),
    ("Dashboard Keuangan", actions.action_dashboard),
    ("Laporan Keuangan & Statistik", actions.action_reports),
    ("Buat Grafik Keuangan (4 Chart PNG)", actions.action_charts),
    ("Kelola Anggaran (Budget)", actions.action_set_budget),
    ("Cek Konsistensi Saldo", actions.action_check_consistency),
    ("Kelola Kategori Kustom", actions.action_manage_categories),
    ("Transaksi Rutin (Kos, Internet, Langganan)", actions.action_manage_recurring),
    ("Backup dan Restore Data", actions.action_manage_backup),
    ("Riwayat Perubahan & Undo", actions.action_manage_audit),
    ("Kelola Alias Input Cepat", actions.action_manage_aliases),
    ("Pengingat Harian & Ringkasan Otomatis", actions.action_manage_reminders),
    ("Export Data & Laporan (CSV, Excel, PDF)", actions.action_export_data),
    ("Deteksi Pengeluaran Tidak Wajar", actions.action_detect_anomalies),
]


def _print_menu() -> None:
    """Tampilkan daftar menu utama."""
    print()
    print("=" * 50)
    print("           PERSONAL EXPENSE TRACKER")
    print("=" * 50)
    for idx, (label, _) in enumerate(MENU_ITEMS, start=1):
        print(f" {idx:2d}. {label}")
    print("  0. Keluar")
    print("=" * 50)


def run_cli(factory: Any, user_id: int) -> None:
    """Jalankan antarmuka terminal interaktif utama."""
    while True:
        _print_menu()
        try:
            choice = _raw_prompt(f"Pilih menu [0-{len(MENU_ITEMS)}]: ").strip()
        except InputCancelled:
            print("\nTerima kasih telah menggunakan Personal Expense Tracker. Sampai jumpa!")
            break

        if choice == "0":
            print("\nTerima kasih telah menggunakan Personal Expense Tracker. Sampai jumpa!")
            break

        if not choice.isdigit():
            print_error(
                f"Pilihan tidak valid. Silakan pilih nomor antara 0 dan {len(MENU_ITEMS)}.\n"
            )
            continue

        idx = int(choice)
        if not (1 <= idx <= len(MENU_ITEMS)):
            print_error(
                f"Nomor menu '{choice}' tidak tersedia. Silakan pilih 0-{len(MENU_ITEMS)}.\n"
            )
            continue

        _, action_fn = MENU_ITEMS[idx - 1]
        try:
            action_fn(factory, user_id)
        except InputCancelled:
            print_info("Operasi dibatalkan.")
        except Exception as err:
            logger.exception("Terjadi kesalahan tidak terduga saat menjalankan aksi menu")
            print_error(f"Terjadi kesalahan tidak terduga: {err}")
