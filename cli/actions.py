"""Aksi menu CLI: orkestrasi input pengguna dan pemanggilan service lewat session_scope."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from charts import generate_all_charts
from cli.display import (
    display_audit_logs,
    display_dashboard,
    display_transactions,
    print_error,
    print_header,
    print_info,
    print_success,
    print_warning,
    show_warnings,
)
from cli.inputs import (
    prompt_amount,
    prompt_choice,
    prompt_confirm,
    prompt_date,
    prompt_int,
    prompt_str,
)
from database.engine import session_scope
from models import (
    STATUS_ACTIVE,
    TARGET_CATEGORY,
    TARGET_PAYMENT_METHOD,
    TYPE_ADJUSTMENT,
    TYPE_EXPENSE,
    TYPE_INCOME,
    TYPE_TRANSFER,
)
from reports import (
    format_comparison_text,
    format_report_text,
    format_statistics_text,
    get_daily_report,
    get_financial_statistics,
    get_monthly_comparison,
    get_monthly_report,
    get_period_comparison,
    get_weekly_report,
)
from services import (
    adjustments,
    aliases,
    audit,
    backup,
    balances,
    budgets,
    categories,
    dashboard,
    export,
    opening_balances,
    recurring,
    reminders,
    transactions,
    transfers,
)
from utils.dates import format_display, format_timezone_label, today, week_bounds
from utils.errors import NotFoundError, ValidationError
from utils.formatting import format_rupiah
from utils.validators import parse_date, validate_amount


def _user_today(factory: Any, user_id: int) -> Any:
    """Ambil tanggal hari ini sesuai zona waktu pengguna dari database."""
    with session_scope(factory) as session:
        us = reminders.get_user_settings(session, user_id)
        tz = us.timezone
    return today(tz)


def _load_maps(factory: Any, user_id: int) -> tuple[dict[int, str], dict[int, str]]:
    """Muat peta id -> nama untuk metode dan kategori milik pengguna."""
    with session_scope(factory) as session:
        all_methods = balances.get_method_balances(session, user_id)
        all_cats = categories.list_categories(
            session, user_id, include_archived=True, include_system=True
        )
    method_map = {m.method_id: m.name for m in all_methods}
    category_map = {c.id: c.name for c in all_cats}
    return method_map, category_map


# 1. Tambah Pemasukan
def action_add_income(factory: Any, user_id: int) -> None:
    """Alur input pemasukan berulang dan simpan lewat service."""
    print_header("Tambah Pemasukan")
    with session_scope(factory) as session:
        income_cats = categories.list_categories(session, user_id, type_=TYPE_INCOME)
        active_methods = [
            m for m in balances.get_method_balances(session, user_id) if not m.is_archived
        ]

    if not income_cats:
        print_error("Tidak ada kategori pemasukan aktif yang tersedia.")
        return
    if not active_methods:
        print_error("Tidak ada metode pembayaran aktif yang tersedia.")
        return

    amount = prompt_amount("Nominal pemasukan (Rp): ")
    print("\nPilih kategori pemasukan:")
    chosen_cat = prompt_choice("Pilih kategori: ", income_cats, lambda c: c.name)
    print("\nPilih metode pembayaran:")
    chosen_method = prompt_choice(
        "Pilih metode: ",
        active_methods,
        lambda m: f"{m.name} (Saldo saat ini: {format_rupiah(m.balance)})",
    )
    tx_date = prompt_date(
        "Tanggal transaksi (DD-MM-YYYY) [Enter untuk hari ini]: ",
        default=_user_today(factory, user_id),
    )
    note = prompt_str("Keterangan (opsional, tekan Enter untuk lewati): ")

    with session_scope(factory) as session:
        res = transactions.add_income(
            session,
            user_id,
            amount=amount,
            category_id=chosen_cat.id,
            method_id=chosen_method.method_id,
            on=tx_date,
            note=note,
        )
        tx = res.transaction
        warnings = res.warnings

    print_success("Pemasukan berhasil dicatat!")
    print(f"  ID Transaksi : #{tx.id}")
    print(f"  Tanggal      : {format_display(tx.date)}")
    print(f"  Nominal      : {format_rupiah(tx.amount)}")
    print(f"  Kategori     : {chosen_cat.name}")
    print(f"  Metode       : {chosen_method.name}")
    print(f"  Keterangan   : {tx.note or '-'}")
    show_warnings(warnings)


# 2. Tambah Pengeluaran
def action_add_expense(factory: Any, user_id: int) -> None:
    """Alur input pengeluaran berulang dan simpan lewat service."""
    print_header("Tambah Pengeluaran")
    with session_scope(factory) as session:
        expense_cats = categories.list_categories(session, user_id, type_=TYPE_EXPENSE)
        active_methods = [
            m for m in balances.get_method_balances(session, user_id) if not m.is_archived
        ]

    if not expense_cats:
        print_error("Tidak ada kategori pengeluaran aktif yang tersedia.")
        return
    if not active_methods:
        print_error("Tidak ada metode pembayaran aktif yang tersedia.")
        return

    amount = prompt_amount("Nominal pengeluaran (Rp): ")
    print("\nPilih kategori pengeluaran:")
    chosen_cat = prompt_choice("Pilih kategori: ", expense_cats, lambda c: c.name)
    print("\nPilih metode pembayaran:")
    chosen_method = prompt_choice(
        "Pilih metode: ",
        active_methods,
        lambda m: f"{m.name} (Saldo saat ini: {format_rupiah(m.balance)})",
    )
    tx_date = prompt_date(
        "Tanggal transaksi (DD-MM-YYYY) [Enter untuk hari ini]: ",
        default=_user_today(factory, user_id),
    )
    note = prompt_str("Keterangan (opsional, tekan Enter untuk lewati): ")

    with session_scope(factory) as session:
        from services.anomalies import check_expense_anomaly

        anomaly_check = check_expense_anomaly(
            session, user_id, amount=amount, category_id=chosen_cat.id
        )

    if anomaly_check.is_anomaly:
        print_warning("\n[!] PERINGATAN PENGELUARAN TIDAK WAJAR:")
        print(f"  {anomaly_check.reason}")
        if not prompt_confirm(
            "Apakah Anda yakin nominal ini sudah benar dan ingin menyimpannya?", default=False
        ):
            print_info("Penyimpanan transaksi pengeluaran dibatalkan.")
            return

    with session_scope(factory) as session:
        res = transactions.add_expense(
            session,
            user_id,
            amount=amount,
            category_id=chosen_cat.id,
            method_id=chosen_method.method_id,
            on=tx_date,
            note=note,
        )
        tx = res.transaction
        warnings = res.warnings

    print_success("Pengeluaran berhasil dicatat!")
    print(f"  ID Transaksi : #{tx.id}")
    print(f"  Tanggal      : {format_display(tx.date)}")
    print(f"  Nominal      : {format_rupiah(tx.amount)}")
    print(f"  Kategori     : {chosen_cat.name}")
    print(f"  Metode       : {chosen_method.name}")
    print(f"  Keterangan   : {tx.note or '-'}")
    show_warnings(warnings)


# 3. Transfer Antar Metode
def action_add_transfer(factory: Any, user_id: int) -> None:
    """Alur transfer dana antar metode dengan opsi biaya admin."""
    print_header("Transfer Antar Metode")
    with session_scope(factory) as session:
        active_methods = [
            m for m in balances.get_method_balances(session, user_id) if not m.is_archived
        ]

    if len(active_methods) < 2:
        print_error("Dibutuhkan minimal 2 metode pembayaran aktif untuk melakukan transfer.")
        return

    print("\nPilih metode asal (sumber dana):")
    source_method = prompt_choice(
        "Pilih metode asal: ",
        active_methods,
        lambda m: f"{m.name} (Saldo: {format_rupiah(m.balance)})",
    )

    dest_candidates = [m for m in active_methods if m.method_id != source_method.method_id]
    print("\nPilih metode tujuan (penerima dana):")
    dest_method = prompt_choice(
        "Pilih metode tujuan: ",
        dest_candidates,
        lambda m: f"{m.name} (Saldo: {format_rupiah(m.balance)})",
    )

    amount = prompt_amount("Nominal transfer (Rp): ")
    fee = prompt_amount("Biaya admin (Rp) [Enter jika Rp0]: ", allow_zero=True, default=0)
    tx_date = prompt_date(
        "Tanggal transfer (DD-MM-YYYY) [Enter untuk hari ini]: ",
        default=_user_today(factory, user_id),
    )
    note = prompt_str("Keterangan transfer (opsional): ")

    with session_scope(factory) as session:
        res = transfers.add_transfer(
            session,
            user_id,
            amount=amount,
            from_method_id=source_method.method_id,
            to_method_id=dest_method.method_id,
            on=tx_date,
            note=note,
            admin_fee=fee,
        )
        tf = res.transfer
        admin_fee_tx = res.admin_fee
        warnings = res.warnings

    print_success("Transfer berhasil dicatat!")
    print(f"  ID Transfer : #{tf.id}")
    print(f"  Tanggal     : {format_display(tf.date)}")
    print(f"  Dari        : {source_method.name}")
    print(f"  Ke          : {dest_method.name}")
    print(f"  Nominal     : {format_rupiah(tf.amount)}")
    if admin_fee_tx:
        print(
            f"  Biaya Admin : {format_rupiah(admin_fee_tx.amount)} "
            f"(ID Transaksi: #{admin_fee_tx.id})"
        )
    show_warnings(warnings)


# 4. Atur Saldo Awal
def action_set_opening_balance(factory: Any, user_id: int) -> None:
    """Atur saldo awal metode pembayaran beserta tanggal berlakunya."""
    print_header("Atur Saldo Awal")
    with session_scope(factory) as session:
        methods = balances.get_method_balances(session, user_id)

    if not methods:
        print_error("Tidak ada metode pembayaran yang ditemukan.")
        return

    print("\nPilih metode pembayaran:")
    chosen_method = prompt_choice("Pilih metode: ", methods, lambda m: m.name)

    with session_scope(factory) as session:
        existing = opening_balances.get_opening_balance(session, user_id, chosen_method.method_id)
    if existing:
        print_info(
            f"Saldo awal saat ini untuk {chosen_method.name}: "
            f"{format_rupiah(existing.amount)} per {format_display(existing.effective_date)}"
        )

    amount = prompt_amount("Saldo awal baru (Rp): ", allow_zero=True)
    eff_date = prompt_date(
        "Tanggal berlaku saldo awal (DD-MM-YYYY) [Enter untuk hari ini]: ",
        default=_user_today(factory, user_id),
    )

    with session_scope(factory) as session:
        res = opening_balances.set_opening_balance(
            session,
            user_id,
            method_id=chosen_method.method_id,
            amount=amount,
            effective_date=eff_date,
        )
        ob = res.opening_balance
        warnings = res.warnings

    print_success(f"Saldo awal untuk {chosen_method.name} berhasil disimpan!")
    print(f"  Nominal         : {format_rupiah(ob.amount)}")
    print(f"  Tanggal Berlaku : {format_display(ob.effective_date)}")
    show_warnings(warnings)


# 5. Koreksi Saldo (Adjustment)
def action_adjust_balance(factory: Any, user_id: int) -> None:
    """Koreksi saldo tercatat agar sesuai dengan saldo riil fisik."""
    print_header("Koreksi Saldo (Adjustment)")
    with session_scope(factory) as session:
        active_methods = [
            m for m in balances.get_method_balances(session, user_id) if not m.is_archived
        ]

    if not active_methods:
        print_error("Tidak ada metode pembayaran aktif yang tersedia.")
        return

    print("\nPilih metode pembayaran yang akan dikoreksi:")
    chosen_method = prompt_choice(
        "Pilih metode: ",
        active_methods,
        lambda m: f"{m.name} (Saldo tercatat saat ini: {format_rupiah(m.balance)})",
    )

    adj_date = prompt_date(
        "Tanggal koreksi (DD-MM-YYYY) [Enter untuk hari ini]: ",
        default=_user_today(factory, user_id),
    )
    actual_balance = prompt_amount("Saldo sebenarnya di rekening/dompet (Rp): ", allow_zero=True)
    note = prompt_str("Keterangan koreksi (opsional): ")

    try:
        with session_scope(factory) as session:
            res = adjustments.adjust_balance(
                session,
                user_id,
                method_id=chosen_method.method_id,
                actual_balance=actual_balance,
                on=adj_date,
                note=note,
            )
            tx = res.transaction
            prev = res.previous_balance
            act = res.actual_balance
            diff = res.difference
            warnings = res.warnings
    except ValidationError as err:
        print_error(str(err))
        return

    if tx is None:
        print_info(
            f"Saldo tercatat sudah sama dengan saldo sebenarnya ({format_rupiah(act)}). "
            "Tidak ada transaksi penyesuaian yang dibuat."
        )
        return

    print_success("Koreksi saldo berhasil dicatat!")
    print(f"  ID Transaksi     : #{tx.id}")
    print(f"  Metode           : {chosen_method.name}")
    print(f"  Saldo Tercatat   : {format_rupiah(prev)}")
    print(f"  Saldo Sebenarnya : {format_rupiah(act)}")
    diff_sign = "+" if diff > 0 else ""
    print(f"  Selisih Koreksi  : {diff_sign}{format_rupiah(diff)}")
    show_warnings(warnings)


# 6. Riwayat Transaksi
def action_list_transactions(factory: Any, user_id: int) -> None:
    """Tampilkan riwayat transaksi terbaru."""
    print_header("Riwayat Transaksi Terbaru")
    limit = prompt_int(
        "Jumlah transaksi yang ingin ditampilkan [default: 20]: ",
        min_val=1,
        default=20,
        allow_cancel=True,
    )
    with session_scope(factory) as session:
        txs = transactions.list_transactions(session, user_id, limit=limit)
    method_map, category_map = _load_maps(factory, user_id)
    display_transactions(txs, method_map, category_map)


# 7. Filter Transaksi
def action_filter_transactions(factory: Any, user_id: int) -> None:
    """Filter transaksi berdasarkan jenis, kategori, metode, atau rentang tanggal."""
    print_header("Filter Transaksi")
    with session_scope(factory) as session:
        all_methods = balances.get_method_balances(session, user_id)
        all_cats = categories.list_categories(session, user_id, include_archived=True)

    # 1. Filter jenis
    type_options = [
        ("Semua jenis transaksi", None),
        ("Pemasukan (income)", TYPE_INCOME),
        ("Pengeluaran (expense)", TYPE_EXPENSE),
        ("Transfer (transfer)", TYPE_TRANSFER),
        ("Koreksi saldo (adjustment)", TYPE_ADJUSTMENT),
    ]
    print("\nFilter berdasarkan jenis:")
    _, chosen_type = prompt_choice("Pilih filter jenis: ", type_options, lambda o: o[0])

    # 2. Filter kategori (hanya jika jenis bukan transfer/adjustment)
    chosen_cat_id = None
    if chosen_type not in (TYPE_TRANSFER, TYPE_ADJUSTMENT):
        filtered_cats = [c for c in all_cats if chosen_type is None or c.type == chosen_type]
        if filtered_cats:
            cat_options: list[tuple[str, int | None]] = [("Semua kategori", None)] + [
                (c.name, c.id) for c in filtered_cats
            ]
            print("\nFilter berdasarkan kategori:")
            _, chosen_cat_id = prompt_choice("Pilih filter kategori: ", cat_options, lambda o: o[0])

    # 3. Filter metode
    method_options: list[tuple[str, int | None]] = [("Semua metode pembayaran", None)] + [
        (m.name, m.method_id) for m in all_methods
    ]
    print("\nFilter berdasarkan metode pembayaran:")
    _, chosen_method_id = prompt_choice("Pilih filter metode: ", method_options, lambda o: o[0])

    # 4. Filter rentang tanggal
    print("\nFilter rentang tanggal:")
    start_str = prompt_str("Tanggal awal (DD-MM-YYYY) [Enter untuk tanpa batas awal]: ")
    end_str = prompt_str("Tanggal akhir (DD-MM-YYYY) [Enter untuk tanpa batas akhir]: ")

    start_date = None
    end_date = None
    if start_str:
        try:
            start_date = parse_date(start_str, "Tanggal awal")
        except ValidationError as err:
            print_error(str(err))
            return
    if end_str:
        try:
            end_date = parse_date(end_str, "Tanggal akhir")
        except ValidationError as err:
            print_error(str(err))
            return

    try:
        with session_scope(factory) as session:
            txs = transactions.list_transactions(
                session,
                user_id,
                type_=chosen_type,
                category_id=chosen_cat_id,
                method_id=chosen_method_id,
                start=start_date,
                end=end_date,
            )
    except ValidationError as err:
        print_error(str(err))
        return

    method_map, category_map = _load_maps(factory, user_id)
    print("\n--- HASIL FILTER ---")
    display_transactions(txs, method_map, category_map)


# 8. Cari Transaksi
def action_search_transactions(factory: Any, user_id: int) -> None:
    """Cari transaksi berdasarkan kata kunci pada keterangan atau nama kategori."""
    print_header("Cari Transaksi")
    keyword = prompt_str("Masukkan kata kunci pencarian: ", required=True)
    if not keyword:
        return

    with session_scope(factory) as session:
        txs = transactions.list_transactions(session, user_id, keyword=keyword)

    method_map, category_map = _load_maps(factory, user_id)
    print(f"\n--- HASIL PENCARIAN KATA KUNCI '{keyword}' ---")
    display_transactions(txs, method_map, category_map)


# 9. Edit Transaksi
def action_edit_transaction(factory: Any, user_id: int) -> None:
    """Ubah transaksi aktif milik pengguna."""
    print_header("Edit Transaksi")
    tx_id = prompt_int(
        "Masukkan ID Transaksi yang ingin diedit [0 untuk batal]: ", allow_cancel=True
    )

    with session_scope(factory) as session:
        try:
            tx = transactions.get_transaction(session, user_id, tx_id, include_deleted=True)
        except NotFoundError:
            print_error(f"Transaksi #{tx_id} tidak ditemukan.")
            return

        method_map, category_map = _load_maps(factory, user_id)
        active_methods = [
            m for m in balances.get_method_balances(session, user_id) if not m.is_archived
        ]
        active_cats = (
            categories.list_categories(session, user_id, type_=tx.type)
            if tx.type in (TYPE_INCOME, TYPE_EXPENSE)
            else []
        )

    if tx.status != STATUS_ACTIVE:
        print_error(
            f"Transaksi #{tx.id} sudah dihapus; pulihkan terlebih dahulu jika ingin mengedit."
        )
        return
    if tx.type == TYPE_ADJUSTMENT:
        print_error(
            "Koreksi saldo tidak bisa diedit langsung; "
            "silakan hapus transaksi ini lalu buat koreksi baru."
        )
        return
    if tx.related_transaction_id is not None:
        print_error(
            f"Transaksi #{tx.id} adalah biaya admin yang terhubung ke transfer "
            f"#{tx.related_transaction_id}; perubahan harus dilakukan lewat transaksi transfernya."
        )
        return

    print(f"\nData saat ini untuk Transaksi #{tx.id}:")
    print(f"  Jenis      : {tx.type}")
    print(f"  Tanggal    : {format_display(tx.date)}")
    print(f"  Nominal    : {format_rupiah(tx.amount)}")
    if tx.category_id:
        print(f"  Kategori   : {category_map.get(tx.category_id, '-')}")
    print(f"  Metode     : {method_map.get(tx.method_id, '-')}")
    if tx.to_method_id:
        print(f"  Ke Metode  : {method_map.get(tx.to_method_id, '-')}")
    print(f"  Keterangan : {tx.note or '-'}")
    print("\n(Tekan Enter langsung pada kolom yang tidak ingin diubah)")

    # 1. Nominal
    raw_amt = prompt_str(
        f"Nominal baru [saat ini: {format_rupiah(tx.amount)}, Enter untuk tetap]: "
    )
    new_amount = None
    if raw_amt:
        try:
            new_amount = validate_amount(raw_amt)
        except ValidationError as err:
            print_error(str(err))
            return

    # 2. Tanggal
    raw_date = prompt_str(
        f"Tanggal baru [saat ini: {format_display(tx.date)}, Enter untuk tetap]: "
    )
    new_date = None
    if raw_date:
        try:
            new_date = parse_date(raw_date)
        except ValidationError as err:
            print_error(str(err))
            return

    # 3. Kategori
    new_cat_id = None
    if (
        tx.type in (TYPE_INCOME, TYPE_EXPENSE)
        and active_cats
        and prompt_confirm("Ingin mengubah kategori?", default=False)
    ):
        print("\nPilih kategori baru:")
        c = prompt_choice("Pilih kategori: ", active_cats, lambda x: x.name)
        new_cat_id = c.id

    # 4. Metode
    new_method_id = None
    if prompt_confirm("Ingin mengubah metode pembayaran?", default=False):
        print("\nPilih metode pembayaran baru:")
        m = prompt_choice("Pilih metode: ", active_methods, lambda x: x.name)
        new_method_id = m.method_id

    # 5. Metode tujuan (transfer)
    new_to_method_id = None
    if tx.type == TYPE_TRANSFER and prompt_confirm(
        "Ingin mengubah metode tujuan transfer?", default=False
    ):
        print("\nPilih metode tujuan baru:")
        m = prompt_choice("Pilih metode tujuan: ", active_methods, lambda x: x.name)
        new_to_method_id = m.method_id

    # 6. Keterangan
    raw_note = prompt_str(
        f"Keterangan baru [saat ini: '{tx.note or '-'}', "
        "ketik '-' untuk kosongkan, Enter untuk tetap]: "
    )
    new_note = None
    if raw_note is not None:
        new_note = "" if raw_note == "-" else raw_note

    has_changes = any(
        [
            new_amount is not None,
            new_date is not None,
            new_cat_id is not None,
            new_method_id is not None,
            new_to_method_id is not None,
            new_note is not None,
        ]
    )
    if not has_changes:
        print_info("Tidak ada perubahan yang dimasukkan.")
        return

    if not prompt_confirm("Simpan perubahan transaksi ini?", default=True):
        print_info("Perubahan dibatalkan.")
        return

    try:
        with session_scope(factory) as session:
            res = transactions.edit_transaction(
                session,
                user_id,
                tx.id,
                amount=new_amount,
                on=new_date,
                category_id=new_cat_id,
                method_id=new_method_id,
                to_method_id=new_to_method_id,
                note=new_note,
            )
            warnings = res.warnings
    except ValidationError as err:
        print_error(str(err))
        return

    print_success(f"Transaksi #{tx.id} berhasil diperbarui.")
    show_warnings(warnings)


# 10. Hapus Transaksi (Soft Delete)
def action_delete_transaction(factory: Any, user_id: int) -> None:
    """Hapus transaksi dengan konfirmasi (soft delete)."""
    print_header("Hapus Transaksi (Soft Delete)")
    tx_id = prompt_int(
        "Masukkan ID Transaksi yang ingin dihapus [0 untuk batal]: ", allow_cancel=True
    )

    with session_scope(factory) as session:
        try:
            tx = transactions.get_transaction(session, user_id, tx_id, include_deleted=True)
        except NotFoundError:
            print_error(f"Transaksi #{tx_id} tidak ditemukan.")
            return

        method_map, category_map = _load_maps(factory, user_id)
        related = transactions.get_related_transactions(
            session, user_id, tx.id, include_deleted=False
        )

    if tx.status != STATUS_ACTIVE:
        print_error(f"Transaksi #{tx.id} sudah berstatus terhapus sebelumnya.")
        return
    if tx.related_transaction_id is not None:
        print_error(
            f"Transaksi #{tx.id} adalah biaya admin yang terhubung ke transfer "
            f"#{tx.related_transaction_id}. Penghapusan harus dilakukan lewat transfer induknya."
        )
        return

    print(f"\nDetail Transaksi #{tx.id} yang akan dihapus:")
    print(f"  Jenis      : {tx.type}")
    print(f"  Tanggal    : {format_display(tx.date)}")
    print(f"  Nominal    : {format_rupiah(tx.amount)}")
    if tx.category_id:
        print(f"  Kategori   : {category_map.get(tx.category_id, '-')}")
    print(f"  Metode     : {method_map.get(tx.method_id, '-')}")
    if tx.to_method_id:
        print(f"  Ke Metode  : {method_map.get(tx.to_method_id, '-')}")
    print(f"  Keterangan : {tx.note or '-'}")
    if related:
        print(
            f"  PERHATIAN  : Transaksi ini memiliki {len(related)} biaya admin "
            "yang akan ikut dihapus!"
        )

    if not prompt_confirm(f"Apakah Anda yakin ingin menghapus Transaksi #{tx.id}?", default=False):
        print_info("Penghapusan dibatalkan.")
        return

    with session_scope(factory) as session:
        res = transactions.delete_transaction(session, user_id, tx.id)
        warnings = res.warnings

    print_success(f"Transaksi #{tx.id} berhasil dihapus.")
    show_warnings(warnings)


# 11. Dashboard Sederhana
def action_dashboard(factory: Any, user_id: int) -> None:
    """Tampilkan dashboard keuangan terkini."""
    with session_scope(factory) as session:
        summary = dashboard.get_dashboard_summary(session, user_id)
    display_dashboard(summary)


# 12. Kelola Anggaran (Budget)
def action_set_budget(factory: Any, user_id: int) -> None:
    """Kelola anggaran bulanan, mingguan, dan rollover sisa anggaran."""
    print_header("Kelola Anggaran (Budget)")
    options = [
        "Atur Budget Total Bulanan",
        "Atur Budget per Kategori Pengeluaran",
        "Lihat Evaluasi & Peringatan Budget Bulan Ini",
        "Atur Budget Total Mingguan",
        "Atur Budget Kategori Mingguan",
        "Lihat Evaluasi & Peringatan Budget Mingguan",
        "Bawa Sisa Budget ke Bulan Berikutnya (Rollover)",
    ]
    choice = prompt_choice("Pilih aksi budget: ", options)

    if choice == "Atur Budget Total Bulanan":
        amount = prompt_amount("Nominal budget total bulanan (Rp): ")
        month_input = prompt_str("Bulan budget (YYYY-MM) [Enter untuk bulan ini]: ")
        try:
            with session_scope(factory) as session:
                b = budgets.set_monthly_budget(session, user_id, amount=amount, month=month_input)
                b_month = b.month
                b_amount = b.amount
        except ValidationError as err:
            print_error(str(err))
            return
        print_success(
            f"Budget total bulan {b_month} berhasil diatur sebesar {format_rupiah(b_amount)}."
        )

    elif choice == "Atur Budget per Kategori Pengeluaran":
        with session_scope(factory) as session:
            expense_cats = categories.list_categories(session, user_id, type_=TYPE_EXPENSE)
        if not expense_cats:
            print_error("Tidak ada kategori pengeluaran aktif.")
            return
        print("\nPilih kategori pengeluaran:")
        chosen_cat = prompt_choice("Pilih kategori: ", expense_cats, lambda c: c.name)
        amount = prompt_amount(f"Nominal budget kategori '{chosen_cat.name}' (Rp): ")
        month_input = prompt_str("Bulan budget (YYYY-MM) [Enter untuk bulan ini]: ")
        try:
            with session_scope(factory) as session:
                b = budgets.set_category_budget(
                    session, user_id, category_id=chosen_cat.id, amount=amount, month=month_input
                )
                b_month = b.month
                b_amount = b.amount
        except ValidationError as err:
            print_error(str(err))
            return
        print_success(
            f"Budget kategori '{chosen_cat.name}' bulan {b_month} berhasil diatur "
            f"sebesar {format_rupiah(b_amount)}."
        )

    elif choice == "Lihat Evaluasi & Peringatan Budget Bulan Ini":
        month_input = prompt_str("Bulan evaluasi (YYYY-MM) [Enter untuk bulan ini]: ")
        try:
            with session_scope(factory) as session:
                eval_res = budgets.evaluate_budgets(session, user_id, month=month_input)
        except ValidationError as err:
            print_error(str(err))
            return

        print(f"\n--- EVALUASI BUDGET BULAN {eval_res.month} ---")
        if eval_res.total_budget:
            tb = eval_res.total_budget
            extra_info = (
                f" [Dasar: {format_rupiah(tb.base_amount)} + "
                f"Rollover: {format_rupiah(tb.carryover_amount)}]"
                if tb.carryover_amount > 0
                else ""
            )
            print(
                f"Total Bulanan: {format_rupiah(tb.spent)} / {format_rupiah(tb.amount)}"
                f"{extra_info} ({tb.percentage:.1f}%) - Status: {tb.warning_level.upper()}"
            )
        else:
            print("Total Bulanan: Belum diatur")

        if eval_res.category_budgets:
            print("\nBudget per Kategori:")
            for cb in eval_res.category_budgets:
                stat_text = f"({cb.percentage:.1f}%) - {cb.warning_level.upper()}"
                extra_cb = (
                    f" [+{format_rupiah(cb.carryover_amount)} rollover]"
                    if cb.carryover_amount > 0
                    else ""
                )
                print(
                    f"  * {cb.category_name} : {format_rupiah(cb.spent)} / "
                    f"{format_rupiah(cb.amount)}{extra_cb} {stat_text}"
                )
        else:
            print("\nBudget per Kategori: Belum ada budget kategori yang diatur.")

        if eval_res.has_warnings:
            print("\n[PERINGATAN ANGGARAN]")
            for msg in eval_res.warning_messages:
                print(f"  [!] {msg}")
        else:
            print("\n[OK] Semua anggaran dalam batas aman (<80%).")

    elif choice == "Atur Budget Total Mingguan":
        amount = prompt_amount("Nominal budget total mingguan (Rp): ")
        date_input = prompt_str("Tanggal acuan minggu (DD-MM-YYYY) [Enter untuk minggu ini]: ")
        try:
            with session_scope(factory) as session:
                wb = budgets.set_weekly_budget(
                    session, user_id, amount=amount, on=date_input or None
                )
                w_start = wb.week_start
                w_amount = wb.amount
        except ValidationError as err:
            print_error(str(err))
            return
        w_end = w_start + timedelta(days=6)
        print_success(
            f"Budget total mingguan ({format_display(w_start)} s.d. {format_display(w_end)}) "
            f"berhasil diatur sebesar {format_rupiah(w_amount)}."
        )

    elif choice == "Atur Budget Kategori Mingguan":
        with session_scope(factory) as session:
            expense_cats = categories.list_categories(session, user_id, type_=TYPE_EXPENSE)
        if not expense_cats:
            print_error("Tidak ada kategori pengeluaran aktif.")
            return
        print("\nPilih kategori pengeluaran:")
        chosen_cat = prompt_choice("Pilih kategori: ", expense_cats, lambda c: c.name)
        amount = prompt_amount(f"Nominal budget mingguan kategori '{chosen_cat.name}' (Rp): ")
        date_input = prompt_str("Tanggal acuan minggu (DD-MM-YYYY) [Enter untuk minggu ini]: ")
        try:
            with session_scope(factory) as session:
                wb = budgets.set_weekly_budget(
                    session,
                    user_id,
                    amount=amount,
                    on=date_input or None,
                    category_id=chosen_cat.id,
                )
                w_start = wb.week_start
                w_amount = wb.amount
        except ValidationError as err:
            print_error(str(err))
            return
        w_end = w_start + timedelta(days=6)
        print_success(
            f"Budget mingguan kategori '{chosen_cat.name}' "
            f"({format_display(w_start)} s.d. {format_display(w_end)}) "
            f"berhasil diatur sebesar {format_rupiah(w_amount)}."
        )

    elif choice == "Lihat Evaluasi & Peringatan Budget Mingguan":
        date_input = prompt_str("Tanggal acuan minggu (DD-MM-YYYY) [Enter untuk minggu ini]: ")
        try:
            with session_scope(factory) as session:
                w_eval = budgets.evaluate_weekly_budgets(session, user_id, on=date_input or None)
        except ValidationError as err:
            print_error(str(err))
            return

        w_label = f"{format_display(w_eval.week_start)} s.d. {format_display(w_eval.week_end)}"
        print(f"\n--- EVALUASI BUDGET MINGGUAN ({w_label}) ---")
        if w_eval.total_budget:
            tb = w_eval.total_budget
            print(
                f"Total Mingguan: {format_rupiah(tb.spent)} / {format_rupiah(tb.amount)} "
                f"({tb.percentage:.1f}%) - Status: {tb.warning_level.upper()}"
            )
        else:
            print("Total Mingguan: Belum diatur")

        if w_eval.category_budgets:
            print("\nBudget Mingguan per Kategori:")
            for cb in w_eval.category_budgets:
                stat_text = f"({cb.percentage:.1f}%) - {cb.warning_level.upper()}"
                print(
                    f"  * {cb.category_name} : {format_rupiah(cb.spent)} / "
                    f"{format_rupiah(cb.amount)} {stat_text}"
                )
        else:
            print("\nBudget Mingguan per Kategori: Belum diatur.")

        if w_eval.has_warnings:
            print("\n[PERINGATAN ANGGARAN MINGGUAN]")
            for msg in w_eval.warning_messages:
                print(f"  [!] {msg}")
        else:
            print("\n[OK] Semua anggaran mingguan dalam batas aman (<80%).")

    else:  # Bawa Sisa Budget ke Bulan Berikutnya (Rollover)
        from_input = prompt_str("Bulan asal (YYYY-MM) [Enter untuk bulan lalu]: ")
        to_input = prompt_str("Bulan tujuan (YYYY-MM) [Enter untuk bulan berikutnya]: ")
        try:
            with session_scope(factory) as session:
                rolled = budgets.rollover_budgets(
                    session,
                    user_id,
                    from_month=from_input or None,
                    to_month=to_input or None,
                )
        except ValidationError as err:
            print_error(str(err))
            return

        if not rolled:
            print_info("Tidak ada budget pada bulan asal untuk di-rollover.")
            return

        print_success(
            f"Rollover berhasil dijalankan dari {rolled[0].from_month} ke {rolled[0].to_month}:"
        )
        for item in rolled:
            print(
                f"  * {item.category_name}: Sisa dibawa {format_rupiah(item.surplus_carried_over)} "
                f"-> Total Budget Baru: {format_rupiah(item.new_effective_budget)} "
                f"(Dasar {format_rupiah(item.new_base_amount)} + "
                f"Rollover {format_rupiah(item.surplus_carried_over)})"
            )


# 13. Laporan Keuangan
def action_reports(factory: Any, user_id: int) -> None:
    """Tampilkan laporan keuangan, perbandingan periode, atau statistik."""
    print_header("Laporan Keuangan & Statistik")
    report_types = [
        "Laporan Harian",
        "Laporan Mingguan (Senin s.d. Minggu)",
        "Laporan Bulanan",
        "Perbandingan Periode",
        "Statistik Keuangan & Insight",
    ]
    choice = prompt_choice("Pilih jenis laporan: ", report_types)

    if choice == "Laporan Harian":
        on_str = prompt_str("Tanggal laporan (DD-MM-YYYY) [Enter untuk hari ini]: ")
        try:
            with session_scope(factory) as session:
                rep = get_daily_report(session, user_id, on=on_str)
        except ValidationError as err:
            print_error(str(err))
            return
        print()
        print(format_report_text(rep))

    elif choice == "Laporan Mingguan (Senin s.d. Minggu)":
        on_str = prompt_str("Tanggal patokan (DD-MM-YYYY) [Enter untuk hari ini]: ")
        try:
            with session_scope(factory) as session:
                rep = get_weekly_report(session, user_id, on=on_str)
        except ValidationError as err:
            print_error(str(err))
            return
        print()
        print(format_report_text(rep))

    elif choice == "Laporan Bulanan":
        month_str = prompt_str("Bulan laporan (YYYY-MM) [Enter untuk bulan ini]: ")
        try:
            with session_scope(factory) as session:
                rep = get_monthly_report(session, user_id, month=month_str)
        except ValidationError as err:
            print_error(str(err))
            return
        print()
        print(format_report_text(rep))

    elif choice == "Perbandingan Periode":
        print("\nPilih mode perbandingan:")
        comp_modes = [
            "Bulan Ini vs Bulan Lalu",
            "Bandingkan Dua Bulan (YYYY-MM)",
            "Bandingkan Dua Rentang Tanggal Kustom",
        ]
        mode = prompt_choice("Pilih mode: ", comp_modes)
        try:
            with session_scope(factory) as session:
                if mode == "Bulan Ini vs Bulan Lalu":
                    comp_rep = get_monthly_comparison(session, user_id)
                elif mode == "Bandingkan Dua Bulan (YYYY-MM)":
                    m1 = prompt_str("Bulan pertama (YYYY-MM) [Basis]: ")
                    m2 = prompt_str("Bulan kedua (YYYY-MM) [Pembanding]: ")
                    comp_rep = get_monthly_comparison(session, user_id, month1=m1, month2=m2)
                else:
                    p1_s = prompt_str("Periode 1 - Tanggal awal (DD-MM-YYYY): ")
                    p1_e = prompt_str("Periode 1 - Tanggal akhir (DD-MM-YYYY): ")
                    p2_s = prompt_str("Periode 2 - Tanggal awal (DD-MM-YYYY): ")
                    p2_e = prompt_str("Periode 2 - Tanggal akhir (DD-MM-YYYY): ")
                    comp_rep = get_period_comparison(session, user_id, p1_s, p1_e, p2_s, p2_e)
        except ValidationError as err:
            print_error(str(err))
            return
        print()
        print(format_comparison_text(comp_rep))

    else:  # Statistik Keuangan & Insight
        print("\nPilih rentang waktu statistik:")
        stat_modes = ["Bulan Berjalan / Bulanan", "Rentang Tanggal Kustom"]
        sm = prompt_choice("Pilih rentang: ", stat_modes)
        try:
            with session_scope(factory) as session:
                if sm == "Bulan Berjalan / Bulanan":
                    m_str = prompt_str("Bulan (YYYY-MM) [Enter untuk bulan ini]: ")
                    stats = get_financial_statistics(session, user_id, month=m_str)
                else:
                    s_str = prompt_str("Tanggal awal (DD-MM-YYYY): ")
                    e_str = prompt_str("Tanggal akhir (DD-MM-YYYY): ")
                    stats = get_financial_statistics(session, user_id, start=s_str, end=e_str)
        except ValidationError as err:
            print_error(str(err))
            return
        print()
        print(format_statistics_text(stats))


# 14. Buat Grafik Keuangan (Charts)
def action_charts(factory: Any, user_id: int) -> None:
    """Buat dan simpan 4 grafik keuangan dalam format PNG ke folder reports_output."""
    print_header("Buat Grafik Keuangan (PNG)")
    print("Grafik yang akan dibuat:")
    print("  1. Pengeluaran per Kategori")
    print("  2. Pemasukan per Kategori")
    print("  3. Pemasukan vs Pengeluaran per Periode")
    print("  4. Perkembangan Saldo Kumulatif\n")

    start_str = prompt_str("Tanggal awal (DD-MM-YYYY) [Enter untuk 30 hari terakhir]: ")
    today_date = _user_today(factory, user_id)
    if start_str:
        try:
            start_date = parse_date(start_str, "Tanggal awal")
        except ValidationError as err:
            print_error(str(err))
            return
        end_str = prompt_str("Tanggal akhir (DD-MM-YYYY) [Enter untuk hari ini]: ")
        try:
            end_date = parse_date(end_str, "Tanggal akhir") if end_str else today_date
        except ValidationError as err:
            print_error(str(err))
            return
    else:
        start_date = today_date - timedelta(days=29)
        end_date = today_date

    if start_date > end_date:
        print_error("Tanggal awal tidak boleh setelah tanggal akhir.")
        return

    with session_scope(factory) as session:
        charts = generate_all_charts(session, user_id, start_date, end_date)

    print_success("Keempat grafik keuangan berhasil disimpan:")
    print(f"  * Pengeluaran per Kategori : {charts['expense_by_category']}")
    print(f"  * Pemasukan per Kategori   : {charts['income_by_category']}")
    print(f"  * Pemasukan vs Pengeluaran : {charts['income_vs_expense']}")
    print(f"  * Perkembangan Saldo       : {charts['balance_trend']}")


# 15. Cek Konsistensi Saldo
def action_check_consistency(factory: Any, user_id: int) -> None:
    """Cek konsistensi saldo antara compute_balance agregat dengan data mentah."""
    print_header("Cek Konsistensi Saldo")
    with session_scope(factory) as session:
        results = balances.check_balance_consistency(session, user_id)

    all_consistent = all(r.is_consistent for r in results)
    print("Hasil Pengecekan Konsistensi Saldo:")
    print("-" * 65)
    print(f"{'Metode Pembayaran':<20} {'Agregat':<15} {'Data Mentah':<15} {'Status':<10}")
    print("-" * 65)
    for r in results:
        status_str = "KONSISTEN" if r.is_consistent else "SELISIH!"
        print(
            f"{r.name:<20} {format_rupiah(r.compute_balance):<15} "
            f"{format_rupiah(r.raw_balance):<15} {status_str:<10}"
        )
    print("-" * 65)
    if all_consistent:
        print_success("Semua saldo konsisten! Perhitungan agregat sesuai dengan data mentah.")
    else:
        print_warning("Ditemukan selisih pada satu atau lebih metode pembayaran!")


# 16. Kelola Kategori Kustom
def action_manage_categories(factory: Any, user_id: int) -> None:
    """Kelola kategori kustom: tambah, ubah nama, arsipkan, pulihkan."""
    print_header("Kelola Kategori Kustom")
    sub_options = [
        "Lihat Daftar Kategori",
        "Tambah Kategori Baru",
        "Ubah Nama Kategori",
        "Arsipkan Kategori",
        "Pulihkan Kategori dari Arsip",
    ]
    choice = prompt_choice("Pilih aksi kelola kategori: ", sub_options, allow_cancel=True)
    if choice is None:
        return

    type_options = [("Pengeluaran (Expense)", TYPE_EXPENSE), ("Pemasukan (Income)", TYPE_INCOME)]

    if choice == "Lihat Daftar Kategori":
        with session_scope(factory) as session:
            cats = categories.list_categories(
                session, user_id, include_archived=True, include_system=True
            )
        print("\nDaftar Kategori:")
        print("-" * 55)
        print(f"{'ID':<6} {'Jenis':<12} {'Nama Kategori':<25} {'Status':<12}")
        print("-" * 55)
        for c in cats:
            status = "Sistem" if c.is_system else ("Diarsipkan" if c.is_archived else "Aktif")
            print(f"#{c.id:<5} {c.type:<12} {c.name:<25} {status:<12}")
        print("-" * 55)

    elif choice == "Tambah Kategori Baru":
        _, chosen_type = prompt_choice("Pilih jenis kategori: ", type_options, lambda o: o[0])
        name = prompt_str("Masukkan nama kategori baru: ", required=True)
        if not name:
            return
        try:
            with session_scope(factory) as session:
                new_cat = categories.add_category(session, user_id, name, chosen_type)
                cat_id, cat_name, cat_type = new_cat.id, new_cat.name, new_cat.type
            print_success(
                f"Kategori '{cat_name}' ({cat_type}) berhasil ditambahkan (ID: #{cat_id})."
            )
        except ValidationError as err:
            print_error(str(err))

    elif choice == "Ubah Nama Kategori":
        _, chosen_type = prompt_choice("Pilih jenis kategori: ", type_options, lambda o: o[0])
        with session_scope(factory) as session:
            cats = categories.list_categories(
                session, user_id, type_=chosen_type, include_archived=True, include_system=False
            )
        if not cats:
            print_info("Belum ada kategori yang dapat diubah namanya.")
            return

        cat = prompt_choice(
            "Pilih kategori yang ingin diubah: ",
            cats,
            lambda c: f"{c.name} {'(Diarsipkan)' if c.is_archived else ''}",
        )
        new_name = prompt_str(f"Nama baru [saat ini: '{cat.name}']: ", required=True)
        if not new_name:
            return
        try:
            with session_scope(factory) as session:
                categories.rename_category(session, user_id, cat.id, new_name)
            print_success(
                f"Kategori #{cat.id} berhasil diubah namanya menjadi '{new_name.strip()}'."
            )
        except (ValidationError, NotFoundError) as err:
            print_error(str(err))

    elif choice == "Arsipkan Kategori":
        _, chosen_type = prompt_choice("Pilih jenis kategori: ", type_options, lambda o: o[0])
        with session_scope(factory) as session:
            cats = categories.list_categories(
                session, user_id, type_=chosen_type, include_archived=False, include_system=False
            )
        if not cats:
            print_info("Tidak ada kategori aktif yang dapat diarsipkan.")
            return

        cat = prompt_choice("Pilih kategori yang akan diarsipkan: ", cats, lambda c: c.name)
        if not prompt_confirm(f"Yakin ingin mengarsipkan kategori '{cat.name}'?", default=False):
            print_info("Pengarsipan dibatalkan.")
            return
        try:
            with session_scope(factory) as session:
                categories.archive_category(session, user_id, cat.id)
            print_success(f"Kategori '{cat.name}' berhasil diarsipkan.")
        except (ValidationError, NotFoundError) as err:
            print_error(str(err))

    elif choice == "Pulihkan Kategori dari Arsip":
        _, chosen_type = prompt_choice("Pilih jenis kategori: ", type_options, lambda o: o[0])
        with session_scope(factory) as session:
            all_cats = categories.list_categories(
                session, user_id, type_=chosen_type, include_archived=True, include_system=False
            )
            archived_cats = [c for c in all_cats if c.is_archived]
        if not archived_cats:
            print_info("Tidak ada kategori yang sedang diarsipkan.")
            return

        cat = prompt_choice(
            "Pilih kategori yang ingin dipulihkan: ", archived_cats, lambda c: c.name
        )
        try:
            with session_scope(factory) as session:
                categories.unarchive_category(session, user_id, cat.id)
            print_success(f"Kategori '{cat.name}' berhasil dipulihkan dari arsip.")
        except (ValidationError, NotFoundError) as err:
            print_error(str(err))


# 17. Kelola Transaksi Rutin (Kos, Internet, Langganan)
def action_manage_recurring(factory: Any, user_id: int) -> None:
    """Menu kelola transaksi rutin (kos, internet, langganan)."""
    print_header("Kelola Transaksi Rutin (Kos, Internet, Langganan)")
    sub_options = [
        "Lihat Daftar Transaksi Rutin",
        "Tambah Transaksi Rutin Baru",
        "Ubah Transaksi Rutin",
        "Aktifkan / Nonaktifkan Transaksi Rutin",
        "Catat Transaksi Rutin Sekarang (Apply)",
        "Proses Transaksi Rutin yang Jatuh Tempo",
        "Hapus Transaksi Rutin",
    ]
    choice = prompt_choice("Pilih aksi: ", sub_options, allow_cancel=True)
    if choice is None:
        return

    user_today_date = _user_today(factory, user_id)

    if choice == "Lihat Daftar Transaksi Rutin":
        with session_scope(factory) as session:
            recs = recurring.list_recurring(session, user_id)
            due_map = {r.id: recurring.get_next_due_date(session, user_id, r.id) for r in recs}

        if not recs:
            print_info("Belum ada transaksi rutin yang terdaftar.")
            return

        print("\nDaftar Transaksi Rutin:")
        print("-" * 85)
        print(
            f"{'ID':<5} {'Nama':<20} {'Jenis':<9} {'Nominal':<14} "
            f"{'Frekuensi':<10} {'Jatuh Tempo':<13} {'Status':<9}"
        )
        print("-" * 85)
        for r in recs:
            status = "Aktif" if r.is_active else "Nonaktif"
            next_due = due_map.get(r.id)
            next_due_str = format_display(next_due) if next_due else "-"
            freq_str = f"Tgl {r.day_of_month}" if r.day_of_month else r.frequency
            print(
                f"#{r.id:<4} {r.name:<20} {r.type:<9} {format_rupiah(r.amount):<14} "
                f"{freq_str:<10} {next_due_str:<13} {status:<9}"
            )
        print("-" * 85)

    elif choice == "Tambah Transaksi Rutin Baru":
        type_options = [
            ("Pengeluaran (Expense)", TYPE_EXPENSE),
            ("Pemasukan (Income)", TYPE_INCOME),
        ]
        _, chosen_type = prompt_choice("Pilih jenis: ", type_options, lambda o: o[0])

        name = prompt_str(
            "Nama transaksi rutin (mis. Kos, Internet WiFi, Spotify): ", required=True
        )
        if not name:
            return

        amount = prompt_amount("Nominal: ")

        with session_scope(factory) as session:
            cats = categories.list_categories(session, user_id, type_=chosen_type)
            methods = [
                m for m in balances.get_method_balances(session, user_id) if not m.is_archived
            ]

        if not cats:
            print_error(f"Tidak ada kategori {chosen_type} yang tersedia.")
            return
        if not methods:
            print_error("Tidak ada metode pembayaran aktif yang tersedia.")
            return

        cat = prompt_choice("Pilih kategori: ", cats, lambda c: c.name)
        method = prompt_choice("Pilih metode pembayaran default: ", methods, lambda m: m.name)

        freq_options = [
            ("Bulanan (Monthly)", "monthly"),
            ("Mingguan (Weekly)", "weekly"),
            ("Harian (Daily)", "daily"),
            ("Tahunan (Yearly)", "yearly"),
        ]
        _, chosen_freq = prompt_choice(
            "Pilih frekuensi pengulangan: ", freq_options, lambda f: f[0]
        )

        day_of_month = None
        if chosen_freq == "monthly":
            day_of_month = prompt_int("Tanggal jatuh tempo setiap bulannya (1-31): ")

        start_date = prompt_date(
            f"Tanggal mulai berlaku [Enter untuk hari ini: {format_display(user_today_date)}]: ",
            default=user_today_date,
        )

        has_end = prompt_confirm("Apakah ada tanggal batas akhir transaksi rutin?", default=False)
        end_date = (
            prompt_date("Tanggal batas akhir: ", default=user_today_date) if has_end else None
        )

        note = prompt_str("Catatan/keterangan tambahan [opsional]: ")

        try:
            with session_scope(factory) as session:
                new_rec = recurring.create_recurring(
                    session,
                    user_id,
                    name=name,
                    type_=chosen_type,
                    amount=amount,
                    category_id=cat.id,
                    method_id=method.method_id,
                    frequency=chosen_freq,
                    day_of_month=day_of_month,
                    start_date=start_date,
                    end_date=end_date,
                    note=note,
                )
                rec_id = new_rec.id
            print_success(
                f"Transaksi rutin '{name}' berhasil dibuat (ID: #{rec_id}, "
                f"{format_rupiah(amount)})."
            )
        except ValidationError as err:
            print_error(str(err))

    elif choice == "Ubah Transaksi Rutin":
        with session_scope(factory) as session:
            recs = recurring.list_recurring(session, user_id)
        if not recs:
            print_info("Belum ada transaksi rutin yang dapat diubah.")
            return

        rec = prompt_choice(
            "Pilih transaksi rutin yang ingin diubah: ", recs, lambda r: f"#{r.id} {r.name}"
        )

        new_name = prompt_str(f"Nama baru [saat ini: '{rec.name}', Enter untuk tetap]: ")
        new_amount_str = prompt_str(
            f"Nominal baru [saat ini: {format_rupiah(rec.amount)}, Enter untuk tetap]: "
        )
        new_amount = None
        if new_amount_str:
            new_amount = validate_amount(new_amount_str)

        new_day = None
        if rec.frequency == "monthly" and prompt_confirm(
            "Ingin mengubah tanggal jatuh tempo bulanan?", default=False
        ):
            new_day = prompt_int("Tanggal baru (1-31): ")

        new_note = prompt_str(
            f"Catatan baru [saat ini: '{rec.note or '-'}', ketik '-' kosongkan, Enter tetap]: "
        )
        if new_note == "-":
            new_note = ""

        try:
            with session_scope(factory) as session:
                kwargs: dict[str, Any] = {}
                if new_name:
                    kwargs["name"] = new_name
                if new_amount:
                    kwargs["amount"] = new_amount
                if new_day is not None:
                    kwargs["day_of_month"] = new_day
                if new_note is not None:
                    kwargs["note"] = new_note

                if not kwargs:
                    print_info("Tidak ada perubahan yang dimasukkan.")
                    return

                recurring.update_recurring(session, user_id, rec.id, **kwargs)
            print_success(f"Transaksi rutin #{rec.id} berhasil diperbarui.")
        except (ValidationError, NotFoundError) as err:
            print_error(str(err))

    elif choice == "Aktifkan / Nonaktifkan Transaksi Rutin":
        with session_scope(factory) as session:
            recs = recurring.list_recurring(session, user_id)
        if not recs:
            print_info("Belum ada transaksi rutin.")
            return

        rec = prompt_choice(
            "Pilih transaksi rutin: ",
            recs,
            lambda r: f"#{r.id} {r.name} ({'Aktif' if r.is_active else 'Nonaktif'})",
        )
        try:
            with session_scope(factory) as session:
                if rec.is_active:
                    recurring.deactivate_recurring(session, user_id, rec.id)
                    print_success(f"Transaksi rutin #{rec.id} '{rec.name}' dinonaktifkan.")
                else:
                    recurring.activate_recurring(session, user_id, rec.id)
                    print_success(f"Transaksi rutin #{rec.id} '{rec.name}' diaktifkan kembali.")
        except (ValidationError, NotFoundError) as err:
            print_error(str(err))

    elif choice == "Catat Transaksi Rutin Sekarang (Apply)":
        with session_scope(factory) as session:
            recs = recurring.list_recurring(session, user_id, is_active=True)
        if not recs:
            print_info("Tidak ada transaksi rutin aktif yang dapat dicatat.")
            return

        rec = prompt_choice(
            "Pilih transaksi rutin untuk dicatat sekarang: ",
            recs,
            lambda r: f"#{r.id} {r.name} ({format_rupiah(r.amount)})",
        )
        tx_date = prompt_date(
            f"Tanggal transaksi [Enter untuk hari ini: {format_display(user_today_date)}]: ",
            default=user_today_date,
        )

        if not prompt_confirm(
            f"Catat {rec.type} '{rec.name}' ({format_rupiah(rec.amount)}) "
            f"pada tanggal {format_display(tx_date)}?",
            default=True,
        ):
            print_info("Pencatatan dibatalkan.")
            return

        try:
            with session_scope(factory) as session:
                res = recurring.apply_recurring(session, user_id, rec.id, on=tx_date)
                tx_id = res.transaction.id
                warnings = res.warnings
            print_success(f"Transaksi #{tx_id} berhasil dicatat dari transaksi rutin '{rec.name}'.")
            show_warnings(warnings)
        except (ValidationError, NotFoundError) as err:
            print_error(str(err))

    elif choice == "Proses Transaksi Rutin yang Jatuh Tempo":
        as_of = prompt_date(
            f"Proses jatuh tempo per tanggal "
            f"[Enter untuk hari ini: {format_display(user_today_date)}]: ",
            default=user_today_date,
        )
        try:
            with session_scope(factory) as session:
                results = recurring.process_due_recurring(session, user_id, as_of=as_of)
                count = len(results)
                all_warnings: list[str] = []
                for r in results:
                    all_warnings.extend(r.warnings)
            if count == 0:
                print_info(
                    f"Tidak ada transaksi rutin yang jatuh tempo per {format_display(as_of)}."
                )
            else:
                print_success(
                    f"Berhasil memproses {count} transaksi rutin yang jatuh tempo per "
                    f"{format_display(as_of)}."
                )
                show_warnings(all_warnings)
        except (ValidationError, NotFoundError) as err:
            print_error(str(err))

    elif choice == "Hapus Transaksi Rutin":
        with session_scope(factory) as session:
            recs = recurring.list_recurring(session, user_id)
        if not recs:
            print_info("Belum ada transaksi rutin yang dapat dihapus.")
            return

        rec = prompt_choice(
            "Pilih transaksi rutin yang akan dihapus: ", recs, lambda r: f"#{r.id} {r.name}"
        )
        if not prompt_confirm(
            f"Yakin ingin menghapus aturan transaksi rutin '{rec.name}'?", default=False
        ):
            print_info("Penghapusan dibatalkan.")
            return

        try:
            with session_scope(factory) as session:
                recurring.delete_recurring(session, user_id, rec.id)
            print_success(f"Transaksi rutin '{rec.name}' berhasil dihapus.")
        except (ValidationError, NotFoundError) as err:
            print_error(str(err))


# 18. Backup dan Restore Data
def action_manage_backup(factory: Any, user_id: int) -> None:
    """Kelola backup dan restore data keuangan."""
    print_header("Backup dan Restore Data")
    options = [
        "Buat File Cadangan (Backup)",
        "Pulihkan Data dari Cadangan (Restore)",
        "Lihat Daftar File Cadangan",
    ]
    choice = prompt_choice("Pilih aksi backup/restore: ", options)

    if choice == "Buat File Cadangan (Backup)":
        custom_name = prompt_str("Nama file atau path lengkap [Enter untuk nama otomatis]: ")
        custom_path = custom_name.strip() if custom_name.strip() else None
        try:
            with session_scope(factory) as session:
                target_path = backup.export_backup(session, user_id, file_path=custom_path)
            print_success(f"Data keuangan berhasil dicadangkan ke:\n  {target_path}")
        except (ValidationError, NotFoundError) as err:
            print_error(str(err))

    elif choice == "Pulihkan Data dari Cadangan (Restore)":
        with session_scope(factory) as session:
            available_backups = backup.list_backups(session, user_id)

        restore_options = ["Pilih dari folder cadangan (backups/)", "Masukkan path file manual"]
        sub_choice = prompt_choice("Sumber file cadangan: ", restore_options)

        target_file: str | None = None
        if sub_choice == "Pilih dari folder cadangan (backups/)":
            if not available_backups:
                print_info("Belum ada file cadangan yang ditemukan di folder backups/.")
                return
            chosen = prompt_choice(
                "Pilih file cadangan: ",
                available_backups,
                lambda b: (
                    f"{b['filename']} ({b.get('created_at', '-')} | "
                    f"{b['transactions_count']} transaksi)"
                ),
            )
            target_file = str(chosen["path"])
        else:
            file_input = prompt_str("Path file cadangan (.json): ").strip()
            if not file_input:
                print_error("Path file tidak boleh kosong.")
                return
            target_file = file_input

        try:
            with session_scope(factory) as session:
                summary = backup.get_backup_summary(session, user_id, target_file)
        except (ValidationError, NotFoundError) as err:
            print_error(str(err))
            return

        print("\n--- INFORMASI FILE CADANGAN ---")
        print(f"File      : {summary['filename']}")
        print(f"Versi     : {summary.get('version', '-')}")
        print(f"Dibuat    : {summary.get('created_at', '-')}")
        print(f"Pengguna  : {summary.get('user_name', '-')} (ID: {summary.get('user_id', '-')})")
        s = summary.get("summary", {})
        print(
            f"Rincian   : {s.get('transactions', 0)} transaksi, {s.get('categories', 0)} kategori, "
            f"{s.get('payment_methods', 0)} metode, {s.get('recurring', 0)} transaksi rutin, "
            f"{s.get('quick_aliases', 0)} alias"
        )
        print("-------------------------------")

        print_warning(
            "PERINGATAN: Memulihkan cadangan akan MENGGANTIKAN seluruh data transaksi, "
            "anggaran, saldo awal, dan transaksi rutin saat ini dengan isi cadangan."
        )
        if not prompt_confirm("Apakah Anda yakin ingin melanjutkan pemulihan?", default=False):
            print_info("Pemulihan dibatalkan.")
            return

        try:
            with session_scope(factory) as session:
                res = backup.restore_backup(session, user_id, target_file)
            print_success(
                f"Data berhasil dipulihkan dari {summary['filename']}:\n"
                f"  - {res['transactions']} transaksi\n"
                f"  - {res['categories']} kategori\n"
                f"  - {res['payment_methods']} metode pembayaran\n"
                f"  - {res['opening_balances']} saldo awal\n"
                f"  - {res['budgets']} anggaran\n"
                f"  - {res['recurring']} transaksi rutin\n"
                f"  - {res.get('quick_aliases', 0)} alias input cepat"
            )
        except (ValidationError, NotFoundError) as err:
            print_error(str(err))

    elif choice == "Lihat Daftar File Cadangan":
        with session_scope(factory) as session:
            available_backups = backup.list_backups(session, user_id)

        if not available_backups:
            print_info("Belum ada file cadangan yang ditemukan di folder backups/.")
            return

        print(f"\nDitemukan {len(available_backups)} file cadangan:")
        for idx, b in enumerate(available_backups, start=1):
            created = b.get("created_at") or "-"
            size_kb = b.get("size_bytes", 0) / 1024
            tx_count = b.get("transactions_count", 0)
            print(f" {idx:2d}. {b['filename']} ({size_kb:.1f} KB)")
            print(f"     Dibuat: {created} | Transaksi: {tx_count}")


# 19. Riwayat Perubahan & Undo
def action_manage_audit(factory: Any, user_id: int) -> None:
    """Kelola riwayat perubahan dan pembatalan aksi (undo)."""
    while True:
        print_header("Riwayat Perubahan & Undo")
        print(" 1. Batalkan Aksi Terakhir (Undo)")
        print(" 2. Lihat Riwayat Perubahan")
        print(" 3. Batalkan Aksi Berdasarkan ID")
        print(" 0. Kembali ke Menu Utama")

        pilihan = prompt_str("Pilih menu [0-3]: ").strip()
        if pilihan == "0":
            break
        elif pilihan == "1":
            with session_scope(factory) as session:
                try:
                    res = audit.undo_last(session, user_id)
                    print_success(f"Berhasil membatalkan aksi: {res.audit_log.description}")
                    show_warnings(res.warnings)
                except (ValidationError, NotFoundError) as err:
                    print_error(str(err))
        elif pilihan == "2":
            with session_scope(factory) as session:
                logs = audit.list_audit_logs(session, user_id, limit=30)
            display_audit_logs(logs)
        elif pilihan == "3":
            log_id = prompt_int(
                "Masukkan ID Riwayat yang ingin dibatalkan [0 untuk batal]: ", allow_cancel=True
            )
            if log_id == 0:
                continue
            with session_scope(factory) as session:
                try:
                    target_log = audit.get_audit_log(session, user_id, log_id)
                except NotFoundError:
                    print_error(f"Riwayat #{log_id} tidak ditemukan.")
                    continue

                if target_log.is_undone:
                    print_error(f"Aksi #{log_id} sudah pernah dibatalkan sebelumnya.")
                    continue

                print(f"\nDetail Aksi #{target_log.id}:")
                print(f"  Aksi       : {target_log.action.upper()}")
                print(f"  Entitas    : {target_log.entity_type} #{target_log.entity_id}")
                print(f"  Keterangan : {target_log.description or '-'}")

                if not prompt_confirm(
                    f"Yakin ingin membatalkan aksi #{target_log.id}?", default=True
                ):
                    print_info("Pembatalan dibatalkan.")
                    continue

                try:
                    res = audit.undo_action(session, user_id, log_id)
                    print_success(f"Berhasil membatalkan aksi: {res.audit_log.description}")
                    show_warnings(res.warnings)
                except (ValidationError, NotFoundError) as err:
                    print_error(str(err))
        else:
            print_error("Pilihan tidak valid.")


# 20. Kelola Alias Input Cepat
def action_manage_aliases(factory: Any, user_id: int) -> None:
    """Kelola alias kata kunci untuk input cepat transaksi."""
    while True:
        print_header("Kelola Alias Input Cepat")
        print(" 1. Lihat Seluruh Alias")
        print(" 2. Tambah Alias Kategori")
        print(" 3. Tambah Alias Metode Pembayaran")
        print(" 4. Hapus Alias")
        print(" 0. Kembali ke Menu Utama")

        pilihan = prompt_str("Pilih menu [0-4]: ").strip()
        if pilihan == "0":
            break
        elif pilihan == "1":
            with session_scope(factory) as session:
                user_aliases = aliases.list_aliases(session, user_id)
                cats = {
                    c.id: c.name
                    for c in categories.list_categories(session, user_id, include_archived=True)
                }
                mets = {m.method_id: m.name for m in balances.get_method_balances(session, user_id)}

            if not user_aliases:
                print_info("Belum ada alias yang terdaftar.")
                continue

            cat_aliases = [a for a in user_aliases if a.target_type == TARGET_CATEGORY]
            met_aliases = [a for a in user_aliases if a.target_type == TARGET_PAYMENT_METHOD]

            print(f"\nDitemukan {len(user_aliases)} alias:")
            print("\n[ Alias Kategori ]")
            if cat_aliases:
                for a in cat_aliases:
                    target_name = cats.get(a.category_id, f"ID #{a.category_id}")
                    print(f" • '{a.alias}' -> {target_name} (ID: #{a.id})")
            else:
                print("  (tidak ada)")

            print("\n[ Alias Metode Pembayaran ]")
            if met_aliases:
                for a in met_aliases:
                    target_name = mets.get(a.method_id, f"ID #{a.method_id}")
                    print(f" • '{a.alias}' -> {target_name} (ID: #{a.id})")
            else:
                print("  (tidak ada)")

        elif pilihan == "2":
            with session_scope(factory) as session:
                active_cats = categories.list_categories(session, user_id)
            if not active_cats:
                print_error("Tidak ada kategori aktif yang tersedia.")
                continue

            chosen_cat = prompt_choice(
                "Pilih kategori: ", active_cats, lambda c: f"{c.name} ({c.type})"
            )
            raw_alias = prompt_str("Masukkan alias baru (mis. 'warteg', 'soto'): ").strip().lower()
            if not raw_alias:
                print_error("Alias tidak boleh kosong.")
                continue

            try:
                with session_scope(factory) as session:
                    res = aliases.add_alias(
                        session, user_id, raw_alias, TARGET_CATEGORY, chosen_cat.id
                    )
                    print_success(
                        f"Alias '{res.alias}' berhasil ditambahkan untuk "
                        f"kategori '{chosen_cat.name}'."
                    )
            except (ValidationError, NotFoundError) as err:
                print_error(str(err))

        elif pilihan == "3":
            with session_scope(factory) as session:
                active_methods = [
                    m for m in balances.get_method_balances(session, user_id) if not m.is_archived
                ]
            if not active_methods:
                print_error("Tidak ada metode pembayaran aktif yang tersedia.")
                continue

            chosen_method = prompt_choice(
                "Pilih metode pembayaran: ", active_methods, lambda m: m.name
            )
            raw_alias = prompt_str("Masukkan alias baru (mis. 'ovo', 'kantong'): ").strip().lower()
            if not raw_alias:
                print_error("Alias tidak boleh kosong.")
                continue

            try:
                with session_scope(factory) as session:
                    res = aliases.add_alias(
                        session, user_id, raw_alias, TARGET_PAYMENT_METHOD, chosen_method.method_id
                    )
                    print_success(
                        f"Alias '{res.alias}' berhasil ditambahkan untuk "
                        f"metode '{chosen_method.name}'."
                    )
            except (ValidationError, NotFoundError) as err:
                print_error(str(err))

        elif pilihan == "4":
            with session_scope(factory) as session:
                user_aliases = aliases.list_aliases(session, user_id)
            if not user_aliases:
                print_info("Belum ada alias yang terdaftar.")
                continue

            chosen_alias = prompt_choice(
                "Pilih alias yang ingin dihapus: ",
                user_aliases,
                lambda a: f"'{a.alias}' ({a.target_type})",
            )
            if not prompt_confirm(
                f"Yakin ingin menghapus alias '{chosen_alias.alias}'?", default=True
            ):
                print_info("Penghapusan dibatalkan.")
                continue

            try:
                with session_scope(factory) as session:
                    aliases.delete_alias(session, user_id, chosen_alias.id)
                    print_success(f"Alias '{chosen_alias.alias}' berhasil dihapus.")
            except (ValidationError, NotFoundError) as err:
                print_error(str(err))
        else:
            print_error("Pilihan tidak valid.")


def action_manage_reminders(factory: Any, user_id: int) -> None:
    """Menu pengelolaan pengingat harian, zona waktu, dan ringkasan otomatis (V2/V3)."""
    while True:
        print_header("Pengingat Harian & Ringkasan Otomatis")
        print(" 1. Lihat Pengaturan Saat Ini")
        print(" 2. Preview Pengingat Harian")
        print(" 3. Preview Ringkasan Mingguan")
        print(" 4. Preview Ringkasan Bulanan & Insight V2")
        print(" 5. Ubah Pengaturan Pengingat")
        print(" 6. Ubah Zona Waktu Pengguna (WIB/WITA/WIT)")
        print(" 0. Kembali ke Menu Utama")

        pilihan = prompt_str("Pilih menu [0-6]: ").strip()
        if pilihan == "0":
            break
        elif pilihan == "1":
            with session_scope(factory) as session:
                rs = reminders.get_reminder_settings(session, user_id)
                us = reminders.get_user_settings(session, user_id)
                d_stat = "Aktif" if rs.is_daily_active else "Nonaktif"
                r_stat = "Aktif" if us.is_recurring_active else "Nonaktif"
                w_stat = "Aktif" if rs.is_weekly_active else "Nonaktif"
                m_stat = "Aktif" if rs.is_monthly_active else "Nonaktif"
                tz_label = format_timezone_label(us.timezone)
                last_d = format_display(rs.last_daily_sent) if rs.last_daily_sent else "-"
                last_w = format_display(rs.last_weekly_sent) if rs.last_weekly_sent else "-"
                last_m = format_display(rs.last_monthly_sent) if rs.last_monthly_sent else "-"

            print("\n[ Pengaturan Pengingat & Ringkasan ]")
            print(f" • Zona Waktu           : {tz_label}")
            print(f" • Pengingat Harian     : {d_stat} (Jam: {rs.daily_time})")
            print(f" • Pengingat Rutin      : {r_stat}")
            print(f" • Ringkasan Mingguan   : {w_stat}")
            print(f" • Ringkasan Bulanan    : {m_stat}")
            print(
                f" • Terakhir Dikirim     : Harian={last_d} | Mingguan={last_w} | Bulanan={last_m}"
            )

        elif pilihan == "2":
            with session_scope(factory) as session:
                payload = reminders.build_daily_reminder(session, user_id)
            print("\n" + payload.message.replace("*", "").replace("`", ""))

        elif pilihan == "3":
            with session_scope(factory) as session:
                payload = reminders.build_weekly_summary(session, user_id)
            print("\n" + payload.message.replace("*", "").replace("`", ""))

        elif pilihan == "4":
            with session_scope(factory) as session:
                payload = reminders.build_monthly_summary(session, user_id)
            print("\n" + payload.message.replace("*", "").replace("`", ""))

        elif pilihan == "5":
            with session_scope(factory) as session:
                rs = reminders.get_reminder_settings(session, user_id)
                cur_daily = rs.is_daily_active
                cur_time = rs.daily_time
                cur_weekly = rs.is_weekly_active
                cur_monthly = rs.is_monthly_active

            new_daily = prompt_confirm("Aktifkan pengingat harian?", default=cur_daily)
            raw_time = prompt_str(f"Jam pengingat harian (format HH:MM) [{cur_time}]: ").strip()
            new_time = raw_time if raw_time else cur_time
            new_weekly = prompt_confirm("Aktifkan ringkasan mingguan otomatis?", default=cur_weekly)
            new_monthly = prompt_confirm(
                "Aktifkan ringkasan bulanan otomatis?", default=cur_monthly
            )

            try:
                with session_scope(factory) as session:
                    updated = reminders.update_reminder_settings(
                        session,
                        user_id,
                        is_daily_active=new_daily,
                        daily_time=new_time,
                        is_weekly_active=new_weekly,
                        is_monthly_active=new_monthly,
                    )
                    print_success(
                        f"Pengaturan pengingat berhasil diperbarui "
                        f"(Jam Harian: {updated.daily_time})."
                    )
            except ValidationError as err:
                print_error(str(err))

        elif pilihan == "6":
            raw_tz = prompt_str("Masukkan zona waktu (WIB / WITA / WIT): ").strip()
            try:
                with session_scope(factory) as session:
                    updated_us = reminders.set_user_timezone(session, user_id, raw_tz)
                    tz_label = format_timezone_label(updated_us.timezone)
                print_success(f"Zona waktu pengguna berhasil diatur ke {tz_label}.")
            except ValidationError as err:
                print_error(str(err))
        else:
            print_error("Pilihan tidak valid.")


# 22. Export Data (CSV, Excel, PDF)
def action_export_data(factory: Any, user_id: int) -> None:
    """Menu ekspor data dan laporan keuangan ke format CSV, Excel (.xlsx), dan PDF (.pdf)."""
    from datetime import date

    print_header("Export Data & Laporan (CSV, Excel, PDF)")
    format_options = [
        "CSV (.csv)",
        "Excel (.xlsx)",
        "PDF (.pdf)",
        "Semua Format Sekaligus (CSV, Excel, PDF)",
    ]
    fmt_choice = prompt_choice("Pilih format ekspor: ", format_options, allow_cancel=True)
    if fmt_choice is None:
        return

    period_options = [
        "Semua Transaksi (Semua Periode)",
        "Bulan Ini",
        "Minggu Ini (Senin s.d. Minggu)",
        "Hari Ini",
        "Rentang Tanggal Kustom",
    ]
    per_choice = prompt_choice("Pilih cakupan periode: ", period_options, allow_cancel=True)
    if per_choice is None:
        return

    start_val: Any = None
    end_val: Any = None
    today_date = _user_today(factory, user_id)

    if per_choice == "Bulan Ini":
        start_val = date(today_date.year, today_date.month, 1)
        end_val = today_date
    elif per_choice == "Minggu Ini (Senin s.d. Minggu)":
        start_val, end_val = week_bounds(today_date)
    elif per_choice == "Hari Ini":
        start_val = today_date
        end_val = today_date
    elif per_choice == "Rentang Tanggal Kustom":
        start_val = prompt_str("Tanggal awal (DD-MM-YYYY): ").strip()
        end_val = prompt_str("Tanggal akhir (DD-MM-YYYY): ").strip()

    saved_paths: list[tuple[str, Any]] = []
    try:
        with session_scope(factory) as session:
            if fmt_choice in ("CSV (.csv)", "Semua Format Sekaligus (CSV, Excel, PDF)"):
                p_csv = export.export_csv(session, user_id, start=start_val, end=end_val)
                saved_paths.append(("CSV", p_csv))
            if fmt_choice in ("Excel (.xlsx)", "Semua Format Sekaligus (CSV, Excel, PDF)"):
                p_xlsx = export.export_excel(session, user_id, start=start_val, end=end_val)
                saved_paths.append(("Excel", p_xlsx))
            if fmt_choice in ("PDF (.pdf)", "Semua Format Sekaligus (CSV, Excel, PDF)"):
                p_pdf = export.export_pdf(session, user_id, start=start_val, end=end_val)
                saved_paths.append(("PDF", p_pdf))
    except (ValidationError, NotFoundError) as err:
        print_error(str(err))
        return

    print_success("Ekspor data keuangan berhasil disimpan:")
    for label, path in saved_paths:
        print(f"  * {label:<6}: {path}")


# 23. Deteksi Pengeluaran Tidak Wajar (Fitur V4)
def action_detect_anomalies(factory: Any, user_id: int) -> None:
    """Tampilkan daftar transaksi pengeluaran yang terdeteksi anomali / tidak wajar."""
    print_header("Deteksi Pengeluaran Tidak Wajar (Anomali)")
    with session_scope(factory) as session:
        from services.anomalies import list_anomalous_transactions

        items = list_anomalous_transactions(session, user_id)

    if not items:
        print_info(
            "Tidak ada transaksi pengeluaran yang terdeteksi tidak wajar. "
            "Semua pengeluaran berada dalam rentang statistik wajar."
        )
        return

    print_warning(f"Ditemukan {len(items)} transaksi pengeluaran tidak wajar / anomali:")
    for it in items:
        tx = it.transaction
        cat_name = tx.category.name if tx.category else "-"
        print(f"\n• Transaksi #{tx.id} ({format_display(tx.date)}):")
        print(f"  Nominal    : {format_rupiah(tx.amount)}")
        print(f"  Kategori   : {cat_name}")
        print(f"  Keterangan : {tx.note or '-'}")
        print(f"  Tingkat    : [{it.severity.upper()}]")
        print(f"  Analisis   : {it.reason}")
