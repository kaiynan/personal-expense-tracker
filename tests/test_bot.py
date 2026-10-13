"""Pengujian unit dan integrasi antarmuka Telegram Bot (Fase 4)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from bot.auth import REJECTION_MESSAGE, resolve_db_user_id
from bot.handlers.aliases import (
    cmd_alias,
    cmd_hapus_alias,
    cmd_tambah_alias,
    quick_cancel_callback,
    quick_confirm_callback,
)
from bot.handlers.backup import (
    backup_callback,
    cmd_backup,
    cmd_restore,
    handle_backup_document,
)
from bot.handlers.budgets import (
    budget_choice_callback,
    cmd_budget,
    cmd_budget_mingguan,
    cmd_rollover_budget,
    enter_budget_amount,
)
from bot.handlers.common import cmd_start, handle_text_menu
from bot.handlers.export import (
    cmd_export,
    cmd_export_csv,
    cmd_export_excel,
    cmd_export_pdf,
    export_callback,
)
from bot.handlers.quick import (
    cmd_hari_ini,
    cmd_riwayat,
    cmd_riwayat_perubahan,
    cmd_saldo,
    cmd_undo,
)
from bot.handlers.reminders import (
    cmd_pengingat,
    cmd_ringkasan,
    cmd_waktu_pengingat,
    cmd_zona_waktu,
    reload_scheduled_jobs,
    reminder_callback,
    scheduled_reminder_job,
    sync_user_jobs_in_queue,
)
from bot.handlers.reports import (
    cmd_harian,
    cmd_laporan,
    cmd_perbandingan,
    cmd_statistik,
    report_callback,
)
from database.engine import create_db_engine, create_session_factory, session_scope
from database.seed import ensure_default_user, seed_defaults
from models import TYPE_EXPENSE, TYPE_INCOME, Base
from services import (
    backup,
    balances,
    categories,
    dashboard,
    opening_balances,
    transactions,
    transfers,
)
from utils.dates import today


class MockUser:
    """Mock pengguna Telegram."""

    def __init__(self, user_id: int, first_name: str = "Budi") -> None:
        self.id = user_id
        self.first_name = first_name


class MockFile:
    """Mock objek file Telegram untuk unduhan dokumen."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    async def download_as_bytearray(self) -> bytearray:
        return bytearray(self.data)


class MockDocument:
    """Mock lampiran dokumen Telegram."""

    def __init__(self, file_name: str, content: bytes) -> None:
        self.file_name = file_name
        self.content = content

    async def get_file(self) -> MockFile:
        return MockFile(self.content)


class MockMessage:
    """Mock pesan Telegram untuk menangkap balasan bot."""

    def __init__(self) -> None:
        self.replies: list[str] = []
        self.documents: list[tuple[str | None, bytes]] = []
        self.text: str = ""
        self.document: MockDocument | None = None

    async def reply_text(self, text: str, **kwargs: Any) -> MockMessage:
        self.replies.append(text)
        return self

    async def reply_photo(self, photo: Any, caption: str = "", **kwargs: Any) -> MockMessage:
        self.replies.append(caption)
        return self

    async def reply_document(
        self, document: Any, filename: str | None = None, caption: str = "", **kwargs: Any
    ) -> MockMessage:
        raw = document.read() if hasattr(document, "read") else b""
        self.documents.append((filename, raw))
        self.replies.append(caption)
        return self

    async def edit_message_text(self, text: str, **kwargs: Any) -> MockMessage:
        self.replies.append(text)
        return self

    async def edit_text(self, text: str, **kwargs: Any) -> MockMessage:
        self.replies.append(text)
        return self

    async def delete(self) -> None:
        pass


class MockCallbackQuery:
    """Mock callback query interaksi inline button."""

    def __init__(self, data: str, message: MockMessage) -> None:
        self.data = data
        self.message = message

    async def answer(self) -> None:
        pass

    async def edit_message_text(self, text: str, **kwargs: Any) -> MockMessage:
        self.message.replies.append(text)
        return self.message


class MockJob:
    """Mock job terjadwal pada JobQueue."""

    def __init__(
        self,
        name: str,
        callback: Any,
        data: dict[str, Any] | None,
        queue: MockJobQueue,
        schedule_kind: str,
        schedule_kwargs: dict[str, Any],
    ) -> None:
        self.name = name
        self.callback = callback
        self.data = data
        self._queue = queue
        self.schedule_kind = schedule_kind
        self.schedule_kwargs = schedule_kwargs
        self.removed = False

    def schedule_removal(self) -> None:
        self.removed = True
        if self in self._queue.jobs_list:
            self._queue.jobs_list.remove(self)


class MockJobQueue:
    """Mock JobQueue PTB untuk pengujian penjadwalan dan reload saat restart."""

    def __init__(self) -> None:
        self.jobs_list: list[MockJob] = []

    def jobs(self) -> tuple[MockJob, ...]:
        return tuple(self.jobs_list)

    def get_jobs_by_name(self, name: str) -> tuple[MockJob, ...]:
        return tuple(j for j in self.jobs_list if j.name == name and not j.removed)

    def run_daily(
        self, callback: Any, time: Any, name: str = "", data: Any = None, **kw: Any
    ) -> MockJob:
        job = MockJob(name, callback, data, self, "daily", {"time": time, **kw})
        self.jobs_list.append(job)
        return job

    def run_monthly(
        self, callback: Any, when: Any, day: int, name: str = "", data: Any = None, **kw: Any
    ) -> MockJob:
        job = MockJob(name, callback, data, self, "monthly", {"when": when, "day": day, **kw})
        self.jobs_list.append(job)
        return job

    def run_repeating(
        self,
        callback: Any,
        interval: Any,
        first: Any = None,
        name: str = "",
        data: Any = None,
        **kw: Any,
    ) -> MockJob:
        job = MockJob(
            name, callback, data, self, "repeating", {"interval": interval, "first": first, **kw}
        )
        self.jobs_list.append(job)
        return job


class MockApplication:
    """Mock application bot data."""

    def __init__(self, session_factory: Any) -> None:
        self.bot_data = {"session_factory": session_factory}
        self.job_queue = MockJobQueue()


class MockBot:
    """Mock bot instance untuk pengiriman pesan terjadwal."""

    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        self.sent_messages.append((chat_id, text))


class MockContext:
    """Mock context PTB."""

    def __init__(
        self,
        session_factory: Any,
        args: list[str] | None = None,
        job: MockJob | None = None,
        application: MockApplication | None = None,
        bot: MockBot | None = None,
    ) -> None:
        self.application = application or MockApplication(session_factory)
        self.bot = bot or MockBot()
        self.user_data: dict[str, Any] = {}
        self.args: list[str] = args or []
        self.job = job


class MockUpdate:
    """Mock update Telegram lengkap."""

    def __init__(
        self,
        user_id: int,
        text: str = "",
        callback_data: str | None = None,
        document: MockDocument | None = None,
    ) -> None:
        self.effective_user = MockUser(user_id)
        self.effective_message = MockMessage()
        self.effective_message.text = text
        self.effective_message.document = document
        self.callback_query = (
            MockCallbackQuery(callback_data, self.effective_message) if callback_data else None
        )


@pytest.fixture
def bot_ctx(tmp_path, monkeypatch):
    """Factory session dan user id dengan database SQLite file di tmp_path untuk isolasi thread."""
    import config

    db_path = tmp_path / "bot_test.db"
    engine = create_db_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)

    allowed_tg_id = 998877
    monkeypatch.setattr(config, "ALLOWED_TELEGRAM_IDS", frozenset([allowed_tg_id]))
    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path / "reports_output")

    with session_scope(factory) as session:
        u = ensure_default_user(session)
        seed_defaults(session, u.id)

    yield factory, allowed_tg_id
    engine.dispose()


def test_bot_auth_whitelist_rejected(bot_ctx):
    """Pengguna di luar whitelist ditolak dengan pesan singkat tanpa mengeksekusi aksi."""
    factory, _allowed_id = bot_ctx

    unauthorized_id = 112233
    update = MockUpdate(user_id=unauthorized_id, text="/saldo")
    context = MockContext(factory)

    async def _run():
        await cmd_saldo(update, context)

    asyncio.run(_run())

    assert len(update.effective_message.replies) == 1
    assert update.effective_message.replies[0] == REJECTION_MESSAGE
    assert "db_user_id" not in context.user_data


def test_bot_auth_whitelist_allowed_and_mapped(bot_ctx):
    """Pengguna dalam whitelist diterima dan ID Telegram berhasil dipetakan ke tabel users."""
    factory, allowed_id = bot_ctx

    update = MockUpdate(user_id=allowed_id, text="/start")
    context = MockContext(factory)

    async def _run():
        await cmd_start(update, context)

    asyncio.run(_run())

    assert len(update.effective_message.replies) == 1
    assert "Selamat datang di" in update.effective_message.replies[0]
    assert "db_user_id" in context.user_data

    # Verifikasi users.telegram_id terisi di database
    with session_scope(factory) as session:
        from database import queries

        user = queries.get_user_by_telegram_id(session, allowed_id)
        assert user is not None
        assert user.telegram_id == allowed_id


def test_bot_cmd_saldo_and_hari_ini(bot_ctx):
    """Perintah /saldo dan /hari_ini menampilkan rincian saldo dan transaksi hari ini."""
    factory, allowed_id = bot_ctx

    # Tambahkan saldo awal dan transaksi
    with session_scope(factory) as session:
        u_id = asyncio.run(resolve_db_user_id(factory, allowed_id))
        all_m = balances.get_method_balances(session, u_id)
        cash_id = next(m.method_id for m in all_m if m.name == "Cash")
        bank_id = next(m.method_id for m in all_m if m.name == "Bank")

        opening_balances.set_opening_balance(
            session, u_id, cash_id, 500_000, effective_date="01-10-2026"
        )
        opening_balances.set_opening_balance(
            session, u_id, bank_id, 1_000_000, effective_date="01-10-2026"
        )

        cat_food = categories.list_categories(session, u_id, type_=TYPE_EXPENSE)[0].id
        today_str = today("Asia/Jakarta").strftime("%d-%m-%Y")
        transactions.add_expense(
            session, u_id, 50_000, cat_food, cash_id, on=today_str, note="Bakso"
        )

    update = MockUpdate(user_id=allowed_id, text="/saldo")
    context = MockContext(factory)

    async def _run():
        await cmd_saldo(update, context)

    asyncio.run(_run())

    saldo_reply = update.effective_message.replies[0]
    assert "SALDO SAAT INI" in saldo_reply
    assert "Cash" in saldo_reply
    assert "Rp450.000" in saldo_reply  # 500k - 50k
    assert "Rp1.000.000" in saldo_reply
    assert "Rp1.450.000" in saldo_reply  # total

    # Tes /hari_ini
    update_today = MockUpdate(user_id=allowed_id, text="/hari_ini")
    context_today = MockContext(factory)

    async def _run_today():
        await cmd_hari_ini(update_today, context_today)

    asyncio.run(_run_today())

    today_reply = update_today.effective_message.replies[0]
    assert "TRANSAKSI HARI INI" in today_reply
    assert "Rp50.000" in today_reply
    assert "Bakso" in today_reply


def test_bot_cmd_riwayat_and_laporan(bot_ctx):
    """Perintah /riwayat dan laporan mingguan/bulanan bekerja dengan benar."""
    factory, allowed_id = bot_ctx

    with session_scope(factory) as session:
        u_id = asyncio.run(resolve_db_user_id(factory, allowed_id))
        all_m = balances.get_method_balances(session, u_id)
        cash_id = all_m[0].method_id
        gaji_cat = categories.list_categories(session, u_id, type_=TYPE_INCOME)[0].id
        food_cat = categories.list_categories(session, u_id, type_=TYPE_EXPENSE)[0].id

        today_str = today("Asia/Jakarta").strftime("%d-%m-%Y")
        transactions.add_income(
            session, u_id, 3_000_000, gaji_cat, cash_id, on=today_str, note="Gaji"
        )
        transactions.add_expense(
            session, u_id, 100_000, food_cat, cash_id, on=today_str, note="Makan"
        )

    # Tes /riwayat
    update = MockUpdate(user_id=allowed_id, text="/riwayat")
    context = MockContext(factory)
    asyncio.run(cmd_riwayat(update, context))
    assert "10 TRANSAKSI TERAKHIR" in update.effective_message.replies[0]
    assert "Rp3.000.000" in update.effective_message.replies[0]
    assert "Rp100.000" in update.effective_message.replies[0]

    # Tes /laporan menu
    update_rep = MockUpdate(user_id=allowed_id, text="/laporan")
    context_rep = MockContext(factory)
    asyncio.run(cmd_laporan(update_rep, context_rep))
    assert "LAPORAN KEUANGAN" in update_rep.effective_message.replies[0]

    # Tes callback laporan mingguan
    cb_weekly = MockUpdate(user_id=allowed_id, callback_data="rep_weekly")
    cb_context = MockContext(factory)
    asyncio.run(report_callback(cb_weekly, cb_context))
    assert "LAPORAN KEUANGAN MINGGUAN" in cb_weekly.effective_message.replies[0]
    assert "Rp3.000.000" in cb_weekly.effective_message.replies[0]

    # Tes callback laporan bulanan
    cb_monthly = MockUpdate(user_id=allowed_id, callback_data="rep_monthly")
    cb_monthly_context = MockContext(factory)
    asyncio.run(report_callback(cb_monthly, cb_monthly_context))
    assert "LAPORAN KEUANGAN BULANAN" in cb_monthly.effective_message.replies[0]

    # Tes callback laporan harian
    cb_daily = MockUpdate(user_id=allowed_id, callback_data="rep_daily")
    cb_daily_context = MockContext(factory)
    asyncio.run(report_callback(cb_daily, cb_daily_context))
    assert "LAPORAN KEUANGAN HARIAN" in cb_daily.effective_message.replies[0]

    # Tes callback perbandingan periode
    cb_comp = MockUpdate(user_id=allowed_id, callback_data="rep_compare")
    cb_comp_context = MockContext(factory)
    asyncio.run(report_callback(cb_comp, cb_comp_context))
    assert "PERBANDINGAN PERIODE KEUANGAN" in cb_comp.effective_message.replies[0]

    # Tes callback statistik
    cb_stat = MockUpdate(user_id=allowed_id, callback_data="rep_stats")
    cb_stat_context = MockContext(factory)
    asyncio.run(report_callback(cb_stat, cb_stat_context))
    assert "STATISTIK KEUANGAN & INSIGHT" in cb_stat.effective_message.replies[0]

    # Tes command langsung: /harian, /perbandingan, /statistik
    up_harian = MockUpdate(user_id=allowed_id, text="/harian")
    asyncio.run(cmd_harian(up_harian, MockContext(factory)))
    assert "LAPORAN KEUANGAN HARIAN" in up_harian.effective_message.replies[0]

    up_comp = MockUpdate(user_id=allowed_id, text="/perbandingan")
    asyncio.run(cmd_perbandingan(up_comp, MockContext(factory)))
    assert "PERBANDINGAN PERIODE KEUANGAN" in up_comp.effective_message.replies[0]

    up_stat = MockUpdate(user_id=allowed_id, text="/statistik")
    asyncio.run(cmd_statistik(up_stat, MockContext(factory)))
    assert "STATISTIK KEUANGAN & INSIGHT" in up_stat.effective_message.replies[0]


def test_bot_record_transaction_and_appears_in_cli(bot_ctx):
    """Transaksi yang dicatat via alur bot Telegram langsung muncul di layanan & ringkasan CLI."""
    factory, allowed_id = bot_ctx

    # 1. Pastikan mapping pengguna Telegram berjalan
    u_id = asyncio.run(resolve_db_user_id(factory, allowed_id))

    with session_scope(factory) as session:
        cash_id = balances.get_method_balances(session, u_id)[0].method_id
        food_cat = categories.list_categories(session, u_id, type_=TYPE_EXPENSE)[0].id

    # 2. Simulasikan eksekusi transaksi dari bot (seperti yang dilakukan confirm_save_callback)
    today_str = today("Asia/Jakarta").strftime("%d-%m-%Y")
    with session_scope(factory) as session:
        res = transactions.add_expense(
            session,
            u_id,
            amount=75_000,
            category_id=food_cat,
            method_id=cash_id,
            on=today_str,
            note="Kopi dan Roti Telegram",
        )
        tx_id = res.transaction.id

    # 3. Verifikasi data langsung muncul di layer Dashboard dan Riwayat CLI
    with session_scope(factory) as session:
        summary = dashboard.get_dashboard_summary(session, u_id)
        assert summary.monthly_expense == 75_000
        assert summary.transaction_count == 1

        tx_list = transactions.list_transactions(session, u_id, limit=5)
        assert len(tx_list) == 1
        assert tx_list[0].id == tx_id
        assert tx_list[0].amount == 75_000
        assert tx_list[0].note == "Kopi dan Roti Telegram"


def test_bot_transfer_appears_in_cli_balances(bot_ctx):
    """Transfer yang diproses via bot langsung memperbarui saldo per metode yang terbaca di CLI."""
    factory, allowed_id = bot_ctx
    u_id = asyncio.run(resolve_db_user_id(factory, allowed_id))

    with session_scope(factory) as session:
        all_m = balances.get_method_balances(session, u_id)
        cash_id = next(m.method_id for m in all_m if m.name == "Cash")
        bank_id = next(m.method_id for m in all_m if m.name == "Bank")
        opening_balances.set_opening_balance(
            session, u_id, bank_id, 1_000_000, effective_date="01-10-2026"
        )
        opening_balances.set_opening_balance(
            session, u_id, cash_id, 100_000, effective_date="01-10-2026"
        )

        # Eksekusi transfer via service seperti alur bot
        res = transfers.add_transfer(
            session,
            u_id,
            amount=200_000,
            from_method_id=bank_id,
            to_method_id=cash_id,
            on="05-10-2026",
            admin_fee=2_500,
            note="Tarik Tunai via Bot",
        )
        assert res.transfer.id > 0

    # Verifikasi saldo terbaca di CLI
    with session_scope(factory) as session:
        m_map = {m.name: m.balance for m in balances.get_method_balances(session, u_id)}
        assert m_map["Cash"] == 300_000  # 100k + 200k
        assert m_map["Bank"] == 797_500  # 1.000k - 200k - 2.5k (admin fee)
        assert balances.get_total_balance(session, u_id) == 1_097_500


def test_bot_undo_and_riwayat_perubahan(bot_ctx):
    """Perintah /undo dan /riwayat_perubahan bekerja dengan baik di bot."""
    factory, allowed_id = bot_ctx
    u_id = asyncio.run(resolve_db_user_id(factory, allowed_id))

    # 1. Tambah transaksi
    with session_scope(factory) as session:
        all_m = balances.get_method_balances(session, u_id)
        cash_id = next(m.method_id for m in all_m if m.name == "Cash")
        all_c = categories.list_categories(session, u_id, type_=TYPE_EXPENSE)
        food_id = next(c.id for c in all_c if c.name == "Makanan")
        transactions.add_expense(
            session, u_id, 35_000, food_id, cash_id, on="05-10-2026", note="Nasi Padang"
        )

    # 2. Cek /riwayat_perubahan
    upd = MockUpdate(allowed_id, text="/riwayat_perubahan")
    ctx = MockContext(factory)
    asyncio.run(cmd_riwayat_perubahan(upd, ctx))
    assert len(upd.effective_message.replies) == 1
    assert "RIWAYAT PERUBAHAN" in upd.effective_message.replies[0]
    assert "CREATE" in upd.effective_message.replies[0]

    # 3. Cek /undo
    upd_undo = MockUpdate(allowed_id, text="/undo")
    ctx_undo = MockContext(factory)
    asyncio.run(cmd_undo(upd_undo, ctx_undo))
    assert len(upd_undo.effective_message.replies) == 1
    assert "Berhasil membatalkan aksi" in upd_undo.effective_message.replies[0]

    # 4. Cek /undo kedua saat sudah tidak ada aksi lagi
    upd_undo2 = MockUpdate(allowed_id, text="/undo")
    ctx_undo2 = MockContext(factory)
    asyncio.run(cmd_undo(upd_undo2, ctx_undo2))
    assert "Tidak ada riwayat perubahan" in upd_undo2.effective_message.replies[0]


def test_bot_alias_commands(bot_ctx):
    """Perintah /alias, /tambah_alias, dan /hapus_alias bekerja di bot."""
    factory, allowed_id = bot_ctx

    # 1. /alias menampilkan daftar alias yang di-seed
    upd = MockUpdate(allowed_id, text="/alias")
    ctx = MockContext(factory)
    asyncio.run(cmd_alias(upd, ctx))
    assert len(upd.effective_message.replies) == 1
    assert "DAFTAR ALIAS INPUT CEPAT" in upd.effective_message.replies[0]
    assert "gopay" in upd.effective_message.replies[0]

    # 2. /tambah_alias berhasil menambah alias kustom
    upd_add = MockUpdate(allowed_id, text="/tambah_alias warteg Makanan")
    ctx_add = MockContext(factory, args=["warteg", "Makanan"])
    asyncio.run(cmd_tambah_alias(upd_add, ctx_add))
    assert "berhasil didaftarkan" in upd_add.effective_message.replies[0]

    # 3. /hapus_alias berhasil menghapus alias
    upd_del = MockUpdate(allowed_id, text="/hapus_alias warteg")
    ctx_del = MockContext(factory, args=["warteg"])
    asyncio.run(cmd_hapus_alias(upd_del, ctx_del))
    assert "berhasil dihapus" in upd_del.effective_message.replies[0]


def test_bot_quick_input_flow(bot_ctx):
    """Alur input cepat Telegram: teks -> konfirmasi -> simpan -> sukses."""
    factory, allowed_id = bot_ctx
    u_id = asyncio.run(resolve_db_user_id(factory, allowed_id))

    # Beri saldo awal cash 100k
    with session_scope(factory) as session:
        all_m = balances.get_method_balances(session, u_id)
        cash_id = next(m.method_id for m in all_m if m.name == "Cash")
        opening_balances.set_opening_balance(session, u_id, cash_id, 100_000, "01-01-2026")

    # 1. Kirim teks input cepat: "makan 25000 cash soto ayam"
    upd = MockUpdate(allowed_id, text="makan 25000 cash soto ayam")
    ctx = MockContext(factory)
    asyncio.run(handle_text_menu(upd, ctx))

    assert len(upd.effective_message.replies) == 1
    reply = upd.effective_message.replies[0]
    assert "KONFIRMASI INPUT CEPAT" in reply
    assert "Rp25.000" in reply
    assert "Makanan" in reply
    assert "Cash" in reply
    assert "soto ayam" in reply
    assert "pending_quick_tx" in ctx.user_data

    # 2. Simpan transaksi lewat callback
    cb_upd = MockUpdate(allowed_id, callback_data="quick_confirm")
    cb_ctx = ctx  # gunakan user_data yang sama
    asyncio.run(quick_confirm_callback(cb_upd, cb_ctx))

    assert "pending_quick_tx" not in cb_ctx.user_data
    assert len(cb_upd.effective_message.replies) == 1
    cb_reply = cb_upd.effective_message.replies[0]
    assert "TRANSAKSI BERHASIL DISIMPAN" in cb_reply
    assert "Rp25.000" in cb_reply

    # 3. Verifikasi saldo berkurang di database
    with session_scope(factory) as session:
        assert balances.get_total_balance(session, u_id) == 75_000


def test_bot_quick_input_cancel(bot_ctx):
    """Pembatalan input cepat Telegram mengosongkan pending data."""
    factory, allowed_id = bot_ctx

    upd = MockUpdate(allowed_id, text="bensin 15k cash")
    ctx = MockContext(factory)
    asyncio.run(handle_text_menu(upd, ctx))
    assert "pending_quick_tx" in ctx.user_data

    cb_upd = MockUpdate(allowed_id, callback_data="quick_cancel")
    asyncio.run(quick_cancel_callback(cb_upd, ctx))
    assert "pending_quick_tx" not in ctx.user_data
    assert "Input cepat dibatalkan" in cb_upd.effective_message.replies[0]


def test_bot_quick_input_invalid_format(bot_ctx):
    """Pesan non-menu dengan format salah menghasilkan panduan."""
    factory, allowed_id = bot_ctx

    upd = MockUpdate(allowed_id, text="makan 0 cash")
    ctx = MockContext(factory)
    asyncio.run(handle_text_menu(upd, ctx))

    assert len(upd.effective_message.replies) == 1
    assert "Format Input Cepat Tidak Dikenali" in upd.effective_message.replies[0]


def test_bot_cmd_pengingat_and_waktu_pengingat(bot_ctx):
    """Pengujian perintah /pengingat dan /waktu_pengingat."""
    factory, allowed_id = bot_ctx

    # 1. /pengingat
    upd = MockUpdate(allowed_id)
    ctx = MockContext(factory)
    asyncio.run(cmd_pengingat(upd, ctx))
    assert len(upd.effective_message.replies) == 1
    assert "PENGATURAN PENGINGAT & RINGKASAN" in upd.effective_message.replies[0]
    assert "20:00" in upd.effective_message.replies[0]

    # 2. /waktu_pengingat tanpa argumen
    upd_time = MockUpdate(allowed_id)
    ctx_empty = MockContext(factory, args=[])
    asyncio.run(cmd_waktu_pengingat(upd_time, ctx_empty))
    assert "Masukkan jam pengingat" in upd_time.effective_message.replies[0]

    # 3. /waktu_pengingat jam valid
    upd_valid = MockUpdate(allowed_id)
    ctx_valid = MockContext(factory, args=["21:45"])
    asyncio.run(cmd_waktu_pengingat(upd_valid, ctx_valid))
    assert "berhasil diubah ke *21:45*" in upd_valid.effective_message.replies[0]

    # 4. /waktu_pengingat jam tidak valid
    upd_invalid = MockUpdate(allowed_id)
    ctx_invalid = MockContext(factory, args=["25:00"])
    asyncio.run(cmd_waktu_pengingat(upd_invalid, ctx_invalid))
    assert "Format waktu tidak valid" in upd_invalid.effective_message.replies[0]


def test_bot_cmd_ringkasan(bot_ctx):
    """Pengujian perintah /ringkasan (mingguan, bulanan, default)."""
    factory, allowed_id = bot_ctx

    # Default: kirim mingguan dan bulanan
    upd = MockUpdate(allowed_id)
    ctx = MockContext(factory)
    asyncio.run(cmd_ringkasan(upd, ctx))
    assert len(upd.effective_message.replies) == 2
    assert "RINGKASAN KEUANGAN MINGGUAN" in upd.effective_message.replies[0]
    assert "RINGKASAN & INSIGHT KEUANGAN BULANAN" in upd.effective_message.replies[1]

    # Filter mingguan
    upd_w = MockUpdate(allowed_id)
    ctx_w = MockContext(factory, args=["mingguan"])
    asyncio.run(cmd_ringkasan(upd_w, ctx_w))
    assert len(upd_w.effective_message.replies) == 1
    assert "RINGKASAN KEUANGAN MINGGUAN" in upd_w.effective_message.replies[0]

    # Filter bulanan
    upd_m = MockUpdate(allowed_id)
    ctx_m = MockContext(factory, args=["bulanan"])
    asyncio.run(cmd_ringkasan(upd_m, ctx_m))
    assert len(upd_m.effective_message.replies) == 1
    assert "RINGKASAN & INSIGHT KEUANGAN BULANAN" in upd_m.effective_message.replies[0]


def test_bot_reminder_callbacks(bot_ctx):
    """Pengujian callback toggle dan preview pengingat."""
    factory, allowed_id = bot_ctx
    ctx = MockContext(factory)

    # Inisialisasi db_user_id
    upd_init = MockUpdate(allowed_id)
    asyncio.run(cmd_start(upd_init, ctx))

    # 1. Toggle daily
    cb_daily = MockUpdate(allowed_id, callback_data="rem_toggle_daily")
    asyncio.run(reminder_callback(cb_daily, ctx))
    assert len(cb_daily.effective_message.replies) >= 1
    assert "❌ Nonaktif" in cb_daily.effective_message.replies[-1]

    # 2. Preview daily
    cb_prev_d = MockUpdate(allowed_id, callback_data="rem_preview_daily")
    asyncio.run(reminder_callback(cb_prev_d, ctx))
    assert any("PENGINGAT KEUANGAN HARIAN" in r for r in cb_prev_d.effective_message.replies)

    # 3. Preview weekly
    cb_prev_w = MockUpdate(allowed_id, callback_data="rem_preview_weekly")
    asyncio.run(reminder_callback(cb_prev_w, ctx))
    assert any("RINGKASAN KEUANGAN MINGGUAN" in r for r in cb_prev_w.effective_message.replies)

    # 4. Preview monthly
    cb_prev_m = MockUpdate(allowed_id, callback_data="rem_preview_monthly")
    asyncio.run(reminder_callback(cb_prev_m, ctx))
    assert any(
        "RINGKASAN & INSIGHT KEUANGAN BULANAN" in r for r in cb_prev_m.effective_message.replies
    )


def test_bot_scheduled_reminder_job(bot_ctx):
    """Pengujian scheduled_reminder_job mengirimkan notifikasi terjadwal."""
    factory, allowed_id = bot_ctx
    ctx = MockContext(factory)

    # Pastikan user terdaftar di database dan memiliki telegram_id
    upd_init = MockUpdate(allowed_id)
    asyncio.run(cmd_start(upd_init, ctx))

    # Jalankan background job
    asyncio.run(scheduled_reminder_job(ctx))
    assert len(ctx.bot.sent_messages) >= 1
    chat_id, text = ctx.bot.sent_messages[0]
    assert chat_id == allowed_id
    assert "PENGINGAT KEUANGAN HARIAN" in text


def test_bot_cmd_backup_and_restore_list(bot_ctx):
    """Pengujian /backup mengirim file JSON dan /restore memilih dari server serta memulihkan."""
    factory, allowed_id = bot_ctx
    ctx = MockContext(factory)

    # 1. /restore saat belum ada file backup di server
    upd_empty = MockUpdate(allowed_id, text="/restore")
    asyncio.run(cmd_restore(upd_empty, ctx))
    assert (
        "Belum ada file cadangan yang tersimpan di server" in upd_empty.effective_message.replies[0]
    )

    # 2. Tambahkan transaksi di DB
    user_id = ctx.user_data["db_user_id"]
    today_str = today("Asia/Jakarta").strftime("%d-%m-%Y")
    with session_scope(factory) as session:
        cat = categories.list_categories(session, user_id, type_=TYPE_EXPENSE)[0]
        pm = balances.get_method_balances(session, user_id)[0]
        tx = transactions.add_expense(
            session,
            user_id,
            85_000,
            cat.id,
            pm.method_id,
            on=today_str,
            note="Beli buku Python",
        )
        tx_id = tx.transaction.id

    # 3. Jalankan /backup
    upd_bkp = MockUpdate(allowed_id, text="/backup")
    asyncio.run(cmd_backup(upd_bkp, ctx))
    assert len(upd_bkp.effective_message.documents) == 1
    filename, raw_bytes = upd_bkp.effective_message.documents[0]
    assert filename is not None and filename.endswith(".json")
    assert b"Beli buku Python" in raw_bytes
    assert "PENCADANGAN DATA SELESAI" in upd_bkp.effective_message.replies[0]

    # 4. Hapus transaksi dari DB untuk simulasi kehilangan data
    with session_scope(factory) as session:
        transactions.delete_transaction(session, user_id, tx_id)
        assert len(transactions.list_transactions(session, user_id)) == 0

    # 5. Jalankan /restore -> menampilkan daftar cadangan
    upd_res = MockUpdate(allowed_id, text="/restore")
    asyncio.run(cmd_restore(upd_res, ctx))
    assert "Ditemukan 1 file cadangan di server" in upd_res.effective_message.replies[0]

    # 6. Pilih cadangan pertama (bkp_sel_0)
    cb_sel = MockUpdate(allowed_id, callback_data="bkp_sel_0")
    asyncio.run(backup_callback(cb_sel, ctx))
    assert "KONFIRMASI PEMULIHAN DATA" in cb_sel.effective_message.replies[0]
    assert "pending_restore" in ctx.user_data

    # 7. Konfirmasi restore (bkp_confirm)
    cb_conf = MockUpdate(allowed_id, callback_data="bkp_confirm")
    asyncio.run(backup_callback(cb_conf, ctx))
    assert "PEMULIHAN DATA BERHASIL" in cb_conf.effective_message.replies[0]
    assert "pending_restore" not in ctx.user_data

    # 8. Pastikan transaksi kembali ada di DB
    with session_scope(factory) as session:
        restored_txs = transactions.list_transactions(session, user_id)
        assert len(restored_txs) == 1
        assert restored_txs[0].amount == 85_000
        assert restored_txs[0].note == "Beli buku Python"


def test_bot_handle_backup_document_and_cancel(bot_ctx):
    """Pengujian unggah dokumen .json ke bot, pembatalan, konfirmasi, dan validasi file rusak."""
    import json

    factory, allowed_id = bot_ctx
    ctx = MockContext(factory)

    # Inisialisasi user
    upd_init = MockUpdate(allowed_id)
    asyncio.run(cmd_start(upd_init, ctx))
    user_id = ctx.user_data["db_user_id"]
    today_str = today("Asia/Jakarta").strftime("%d-%m-%Y")

    # Buat data backup dari DB dengan 1 transaksi pemasukan
    with session_scope(factory) as session:
        cat = categories.list_categories(session, user_id, type_=TYPE_INCOME)[0]
        pm = balances.get_method_balances(session, user_id)[0]
        tx = transactions.add_income(
            session,
            user_id,
            1_500_000,
            cat.id,
            pm.method_id,
            on=today_str,
            note="Bonus proyek",
        )
        bkp_dict = backup.create_backup_data(session, user_id)
        transactions.delete_transaction(session, user_id, tx.transaction.id)

    raw_json_bytes = json.dumps(bkp_dict).encode("utf-8")

    # 1. Uji dokumen rusak (bukan JSON valid)
    bad_doc = MockDocument("rusak.json", b"{bukan json valid")
    upd_bad = MockUpdate(allowed_id, document=bad_doc)
    asyncio.run(handle_backup_document(upd_bad, ctx))
    assert "File cadangan rusak atau bukan JSON yang valid" in upd_bad.effective_message.replies[0]

    # 2. Unggah dokumen JSON valid -> lalu batalkan (bkp_cancel)
    valid_doc = MockDocument("cadangan_saya.json", raw_json_bytes)
    upd_doc = MockUpdate(allowed_id, document=valid_doc)
    asyncio.run(handle_backup_document(upd_doc, ctx))
    assert "KONFIRMASI PEMULIHAN DATA" in upd_doc.effective_message.replies[0]
    assert "pending_restore" in ctx.user_data

    cb_cancel = MockUpdate(allowed_id, callback_data="bkp_cancel")
    asyncio.run(backup_callback(cb_cancel, ctx))
    assert "dibatalkan" in cb_cancel.effective_message.replies[0]
    assert "pending_restore" not in ctx.user_data

    # Pastikan transaksi belum dipulihkan karena dibatalkan
    with session_scope(factory) as session:
        assert len(transactions.list_transactions(session, user_id)) == 0

    # 3. Unggah ulang dan konfirmasi (bkp_confirm)
    upd_doc2 = MockUpdate(allowed_id, document=valid_doc)
    asyncio.run(handle_backup_document(upd_doc2, ctx))
    cb_confirm = MockUpdate(allowed_id, callback_data="bkp_confirm")
    asyncio.run(backup_callback(cb_confirm, ctx))
    assert "PEMULIHAN DATA BERHASIL" in cb_confirm.effective_message.replies[0]

    with session_scope(factory) as session:
        txs = transactions.list_transactions(session, user_id)
        assert len(txs) == 1
        assert txs[0].amount == 1_500_000
        assert txs[0].note == "Bonus proyek"


def test_bot_export_commands_and_callbacks(bot_ctx):
    """Pengujian /export, /export_csv, /export_excel, /export_pdf, dan callback inline."""
    factory, allowed_id = bot_ctx
    ctx = MockContext(factory)

    # 1. /export tanpa argumen menampilkan menu tombol
    upd_menu = MockUpdate(allowed_id, text="/export")
    asyncio.run(cmd_export(upd_menu, ctx))
    assert "EKSPOR DATA & LAPORAN KEUANGAN" in upd_menu.effective_message.replies[0]

    # 2. /export_csv mengirim file .csv
    upd_csv = MockUpdate(allowed_id, text="/export_csv")
    asyncio.run(cmd_export_csv(upd_csv, ctx))
    assert len(upd_csv.effective_message.documents) == 1
    fname_csv, raw_csv = upd_csv.effective_message.documents[0]
    assert fname_csv is not None and fname_csv.endswith(".csv")
    assert b"Nominal (Rp)" in raw_csv

    # 3. /export_excel mengirim file .xlsx
    upd_xlsx = MockUpdate(allowed_id, text="/export_excel")
    asyncio.run(cmd_export_excel(upd_xlsx, ctx))
    assert len(upd_xlsx.effective_message.documents) == 1
    fname_xlsx, _ = upd_xlsx.effective_message.documents[0]
    assert fname_xlsx is not None and fname_xlsx.endswith(".xlsx")

    # 4. /export_pdf mengirim file .pdf
    upd_pdf = MockUpdate(allowed_id, text="/export_pdf")
    asyncio.run(cmd_export_pdf(upd_pdf, ctx))
    assert len(upd_pdf.effective_message.documents) == 1
    fname_pdf, raw_pdf = upd_pdf.effective_message.documents[0]
    assert fname_pdf is not None and fname_pdf.endswith(".pdf")
    assert raw_pdf.startswith(b"%PDF-")

    # 5. Callback tombol inline exp_pdf_all
    cb_exp = MockUpdate(allowed_id, callback_data="exp_pdf_all")
    asyncio.run(export_callback(cb_exp, ctx))
    assert len(cb_exp.effective_message.documents) == 1
    assert "EKSPOR DATA BERHASIL" in cb_exp.effective_message.replies[0]
    assert "Semua Periode" in cb_exp.effective_message.replies[0]


def test_bot_weekly_budget_and_rollover(bot_ctx):
    """Pengujian /budget, /budget_mingguan, /rollover_budget, dan pengaturan via tombol inline."""
    from services import budgets

    factory, allowed_id = bot_ctx
    ctx = MockContext(factory)
    user_id = asyncio.run(resolve_db_user_id(factory, allowed_id))

    # 1. Set budget total mingguan via tombol inline bgt_set_weekly_total
    cb_wt = MockUpdate(allowed_id, callback_data="bgt_set_weekly_total")
    asyncio.run(budget_choice_callback(cb_wt, ctx))
    assert "budget total mingguan" in cb_wt.effective_message.replies[0]

    msg_wt = MockUpdate(allowed_id, text="650000")
    asyncio.run(enter_budget_amount(msg_wt, ctx))
    assert "Budget Total Mingguan" in msg_wt.effective_message.replies[0]
    assert "Rp650.000" in msg_wt.effective_message.replies[0]

    # 2. Cek /budget_mingguan
    upd_wm = MockUpdate(allowed_id, text="/budget_mingguan")
    asyncio.run(cmd_budget_mingguan(upd_wm, ctx))
    assert "STATUS BUDGET MINGGUAN" in upd_wm.effective_message.replies[0]
    assert "Rp650.000" in upd_wm.effective_message.replies[0]

    # 3. Siapkan budget 2026-09 lalu jalankan /rollover_budget 2026-09 2026-10
    with session_scope(factory) as session:
        budgets.set_monthly_budget(session, user_id, 1_800_000, "2026-09")

    ctx_ro = MockContext(factory, args=["2026-09", "2026-10"])
    upd_ro = MockUpdate(allowed_id, text="/rollover_budget 2026-09 2026-10")
    asyncio.run(cmd_rollover_budget(upd_ro, ctx_ro))
    assert "ROLLOVER BUDGET BERHASIL" in upd_ro.effective_message.replies[0]
    assert "2026-09" in upd_ro.effective_message.replies[0]
    assert "2026-10" in upd_ro.effective_message.replies[0]
    assert "Rp3.600.000" in upd_ro.effective_message.replies[0]

    # 4. Cek /budget menampilkan status anggaran
    upd_bgt = MockUpdate(allowed_id, text="/budget")
    asyncio.run(cmd_budget(upd_bgt, ctx))
    assert "STATUS ANGGARAN (BUDGET)" in upd_bgt.effective_message.replies[0]
    assert "Budget Mingguan" in upd_bgt.effective_message.replies[0]


def test_bot_zona_waktu_command_and_callbacks(bot_ctx):
    """Pengujian perintah /zona_waktu dan tombol inline WIB/WITA/WIT serta toggle recurring."""
    from services import reminders

    factory, allowed_id = bot_ctx
    ctx = MockContext(factory)
    user_id = asyncio.run(resolve_db_user_id(factory, allowed_id))

    # 1. /zona_waktu tanpa argumen menampilkan pilihan
    upd_menu = MockUpdate(allowed_id, text="/zona_waktu")
    asyncio.run(cmd_zona_waktu(upd_menu, ctx))
    assert "PENGATURAN ZONA WAKTU" in upd_menu.effective_message.replies[0]
    assert "WIB" in upd_menu.effective_message.replies[0]

    # 2. /zona_waktu WITA
    ctx_wita = MockContext(factory, args=["WITA"], application=ctx.application)
    upd_wita = MockUpdate(allowed_id, text="/zona_waktu WITA")
    asyncio.run(cmd_zona_waktu(upd_wita, ctx_wita))
    assert "WITA (Asia/Makassar)" in upd_wita.effective_message.replies[0]
    with session_scope(factory) as session:
        assert reminders.get_user_settings(session, user_id).timezone == "Asia/Makassar"

    # 3. Tombol inline rem_tz_WIT
    cb_wit = MockUpdate(allowed_id, callback_data="rem_tz_WIT")
    asyncio.run(reminder_callback(cb_wit, ctx))
    assert any("WIT (Asia/Jayapura)" in r for r in cb_wit.effective_message.replies)
    with session_scope(factory) as session:
        assert reminders.get_user_settings(session, user_id).timezone == "Asia/Jayapura"

    # 4. Tombol inline rem_toggle_recurring
    cb_rec = MockUpdate(allowed_id, callback_data="rem_toggle_recurring")
    asyncio.run(reminder_callback(cb_rec, ctx))
    with session_scope(factory) as session:
        assert reminders.get_user_settings(session, user_id).is_recurring_active is False

    # 5. Zona waktu tidak valid ditolak
    ctx_bad = MockContext(factory, args=["INVALID_TZ"], application=ctx.application)
    upd_bad = MockUpdate(allowed_id, text="/zona_waktu INVALID_TZ")
    asyncio.run(cmd_zona_waktu(upd_bad, ctx_bad))
    assert "Zona waktu tidak dikenali" in upd_bad.effective_message.replies[0]


def test_bot_day_boundary_date_rollover_two_timezones(bot_ctx, monkeypatch):
    """Dua pengguna di zona waktu berbeda (WIB vs WIT) melihat batas 'hari ini' masing-masing."""
    from datetime import UTC, date, datetime

    import utils.dates as dates_mod
    from database import queries
    from models import USER_STATUS_ACTIVE
    from services import reminders

    factory, wib_tg_id = bot_ctx
    wit_tg_id = 888999000

    # Buat pengguna kedua (WIT) dan aktifkan
    with session_scope(factory) as session:
        u_wib_id = asyncio.run(resolve_db_user_id(factory, wib_tg_id))
        u_wit, _ = queries.register_telegram_user(session, wit_tg_id, name="PapuaUser")
        u_wit.status = USER_STATUS_ACTIVE
        session.flush()
        seed_defaults(session, u_wit.id)
        u_wit_id = u_wit.id
        reminders.set_user_timezone(session, u_wib_id, "WIB")
        reminders.set_user_timezone(session, u_wit_id, "WIT")

    # Bekukan waktu UTC pada 2026-02-28 16:30:00 UTC:
    # - WIB (UTC+7): 28-02-2026 pukul 23:30
    # - WIT (UTC+9): 01-03-2026 pukul 01:30
    fixed_utc = datetime(2026, 2, 28, 16, 30, 0, tzinfo=UTC)
    monkeypatch.setattr(dates_mod, "_utcnow", lambda: fixed_utc)

    # Pengguna WIB mencatat input cepat "makan 25k cash" -> harus tercatat pada 28-02-2026
    ctx_wib = MockContext(factory)
    upd_q_wib = MockUpdate(wib_tg_id, text="makan 25k cash")
    asyncio.run(handle_text_menu(upd_q_wib, ctx_wib))
    cb_q_wib = MockUpdate(wib_tg_id, callback_data="quick_confirm")
    asyncio.run(quick_confirm_callback(cb_q_wib, ctx_wib))
    assert "TRANSAKSI BERHASIL DISIMPAN" in cb_q_wib.effective_message.replies[-1]

    # Pengguna WIT mencatat input cepat "makan 40k cash" -> harus tercatat pada 01-03-2026
    ctx_wit = MockContext(factory)
    upd_q_wit = MockUpdate(wit_tg_id, text="makan 40k cash")
    asyncio.run(handle_text_menu(upd_q_wit, ctx_wit))
    cb_q_wit = MockUpdate(wit_tg_id, callback_data="quick_confirm")
    asyncio.run(quick_confirm_callback(cb_q_wit, ctx_wit))
    assert "TRANSAKSI BERHASIL DISIMPAN" in cb_q_wit.effective_message.replies[-1]

    # Cek /hari_ini untuk keduanya
    upd_hi_wib = MockUpdate(wib_tg_id, text="/hari_ini")
    asyncio.run(cmd_hari_ini(upd_hi_wib, ctx_wib))
    assert "28-02-2026" in upd_hi_wib.effective_message.replies[0]
    assert "Rp25.000" in upd_hi_wib.effective_message.replies[0]
    assert "Rp40.000" not in upd_hi_wib.effective_message.replies[0]

    upd_hi_wit = MockUpdate(wit_tg_id, text="/hari_ini")
    asyncio.run(cmd_hari_ini(upd_hi_wit, ctx_wit))
    assert "01-03-2026" in upd_hi_wit.effective_message.replies[0]
    assert "Rp40.000" in upd_hi_wit.effective_message.replies[0]
    assert "Rp25.000" not in upd_hi_wit.effective_message.replies[0]

    with session_scope(factory) as session:
        wib_txs = transactions.list_transactions(session, u_wib_id)
        wit_txs = transactions.list_transactions(session, u_wit_id)
        assert len(wib_txs) == 1 and wib_txs[0].date == date(2026, 2, 28)
        assert len(wit_txs) == 1 and wit_txs[0].date == date(2026, 3, 1)


def test_bot_job_queue_reload_and_no_duplicate_send_after_restart(bot_ctx, monkeypatch):
    """Job dimuat ulang dari DB saat bot restart, bisa diaktifkan/dimatikan, dan idempoten."""
    from datetime import UTC, date, datetime

    import utils.dates as dates_mod
    from database import queries
    from models import FREQUENCY_DAILY
    from services import recurring, reminders

    factory, allowed_id = bot_ctx

    # Bekukan waktu UTC pada Senin, 1 Juni 2026 pukul 13:05 UTC (20:05 WIB)
    # Hari Senin + tanggal 1 -> daily, recurring, weekly, dan monthly semuanya jatuh tempo!
    fixed_utc = datetime(2026, 6, 1, 13, 5, 0, tzinfo=UTC)
    monkeypatch.setattr(dates_mod, "_utcnow", lambda: fixed_utc)

    # 1. Pastikan user terdaftar dan tambahkan 1 transaksi rutin yang jatuh tempo
    app1 = MockApplication(factory)
    ctx1 = MockContext(factory, application=app1)
    upd_init = MockUpdate(allowed_id)
    asyncio.run(cmd_start(upd_init, ctx1))
    user_id = ctx1.user_data["db_user_id"]

    with session_scope(factory) as session:
        cat = categories.list_categories(session, user_id, type_=TYPE_EXPENSE)[0]
        pm = balances.get_method_balances(session, user_id)[0]
        recurring.create_recurring(
            session,
            user_id,
            name="Langganan Musik",
            type_=TYPE_EXPENSE,
            amount=55_000,
            category_id=cat.id,
            method_id=pm.method_id,
            frequency=FREQUENCY_DAILY,
            start_date=date(2026, 6, 1),
        )

    # 2. Muat jadwal dari DB saat startup bot pertama (app1)
    scheduled_count = reload_scheduled_jobs(app1, factory)
    assert scheduled_count == 4  # daily, recurring, weekly, monthly
    job_names_1 = {j.name for j in app1.job_queue.jobs()}
    assert job_names_1 == {
        f"user_{user_id}_daily",
        f"user_{user_id}_recurring",
        f"user_{user_id}_weekly",
        f"user_{user_id}_monthly",
        "global_reminder_sweep",
    }

    # 3. Jalankan setiap job yang terdaftar pada app1 -> masing-masing terkirim tepat 1x
    bot1 = MockBot()
    for job in list(app1.job_queue.jobs()):
        job_ctx = MockContext(factory, job=job, application=app1, bot=bot1)
        asyncio.run(scheduled_reminder_job(job_ctx))

    assert len(bot1.sent_messages) == 4
    texts_sent = [msg for _, msg in bot1.sent_messages]
    assert any("PENGINGAT KEUANGAN HARIAN" in t for t in texts_sent)
    assert any("TRANSAKSI RUTIN JATUH TEMPO" in t for t in texts_sent)
    assert any("RINGKASAN KEUANGAN MINGGUAN" in t for t in texts_sent)
    assert any("RINGKASAN & INSIGHT KEUANGAN BULANAN" in t for t in texts_sent)

    # 4. Simulasi BOT RESTART: buat instance Application baru (app2) dan muat ulang dari DB
    app2 = MockApplication(factory)
    reloaded_count = reload_scheduled_jobs(app2, factory)
    assert reloaded_count == 4

    # Jalankan kembali semua job yang dimuat ulang setelah restart pada periode yang sama
    bot2 = MockBot()
    for job in list(app2.job_queue.jobs()):
        job_ctx2 = MockContext(factory, job=job, application=app2, bot=bot2)
        asyncio.run(scheduled_reminder_job(job_ctx2))

    # TIDAK BOLEH ada pengiriman ganda setelah restart!
    assert bot2.sent_messages == []

    # 5. Pengguna menonaktifkan pengingat harian & mingguan -> JobQueue disinkronkan
    with session_scope(factory) as session:
        us_updated = reminders.update_user_settings(
            session, user_id, is_daily_active=False, is_weekly_active=False
        )
        user_obj = queries.get_user(session, user_id)
        tg_id = user_obj.telegram_id if user_obj else None
        session.expunge(us_updated)

    active_jobs = sync_user_jobs_in_queue(app2.job_queue, user_id, tg_id, us_updated)
    assert active_jobs == 2
    remaining_names = {j.name for j in app2.job_queue.jobs() if j.name != "global_reminder_sweep"}
    assert remaining_names == {
        f"user_{user_id}_recurring",
        f"user_{user_id}_monthly",
    }


def test_cmd_runway(bot_ctx):
    """Perintah /runway menampilkan metrik runway dan burn rate di Telegram."""
    from bot.handlers.reports import cmd_runway

    factory, allowed_id = bot_ctx
    app = MockApplication(factory)
    ctx = MockContext(factory, application=app)
    upd_start = MockUpdate(allowed_id)
    asyncio.run(cmd_start(upd_start, ctx))

    upd_runway = MockUpdate(allowed_id)
    asyncio.run(cmd_runway(upd_runway, ctx))
    assert len(upd_runway.effective_message.replies) > 0
    reply = upd_runway.effective_message.replies[0]
    assert "RUNWAY & PREDIKSI KEUANGAN" in reply
    assert "Laju Pengeluaran" in reply
