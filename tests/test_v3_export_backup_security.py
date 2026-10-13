"""Pengujian fitur keamanan ekspor, backup/restore ZIP per pengguna, penghapusan akun,
rate limit, batas harian, dan netralisasi formula injection (V3).
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from unittest.mock import AsyncMock

import openpyxl
import pytest
from sqlalchemy import select

import config
from bot.auth import RATE_LIMIT_MESSAGE, resolve_db_user_id
from bot.handlers.backup import cmd_ekspor_data
from bot.handlers.common import cmd_hapus_akun, cmd_start
from database import queries
from database.engine import create_db_engine, create_session_factory, session_scope
from database.seed import ensure_default_user, seed_defaults
from models import (
    ROLE_ADMIN,
    ROLE_USER,
    TYPE_EXPENSE,
    TYPE_INCOME,
    Base,
    Category,
    PaymentMethod,
    User,
)
from services import (
    backup as backup_service,
)
from services import (
    balances,
    transfers,
)
from services import (
    export as export_service,
)
from services import (
    transactions as tx_service,
)
from services import (
    users as users_service,
)
from tests.services_layer.conftest import Ctx
from utils.errors import ValidationError
from utils.rate_limiter import UserRateLimiter, global_rate_limiter


@pytest.fixture
def alice(session):
    user = User(name="Alice", telegram_id=111111)
    session.add(user)
    session.commit()
    seed_defaults(session, user.id)
    methods = {
        m.name: m
        for m in session.scalars(select(PaymentMethod).where(PaymentMethod.user_id == user.id))
    }
    expense = {
        c.name: c
        for c in session.scalars(
            select(Category).where(Category.user_id == user.id, Category.type == TYPE_EXPENSE)
        )
    }
    income = {
        c.name: c
        for c in session.scalars(
            select(Category).where(Category.user_id == user.id, Category.type == TYPE_INCOME)
        )
    }
    return Ctx(session, user, methods, expense, income)


@pytest.fixture
def bob(session):
    user = User(name="Bob", telegram_id=222222)
    session.add(user)
    session.commit()
    seed_defaults(session, user.id)
    methods = {
        m.name: m
        for m in session.scalars(select(PaymentMethod).where(PaymentMethod.user_id == user.id))
    }
    expense = {
        c.name: c
        for c in session.scalars(
            select(Category).where(Category.user_id == user.id, Category.type == TYPE_EXPENSE)
        )
    }
    income = {
        c.name: c
        for c in session.scalars(
            select(Category).where(Category.user_id == user.id, Category.type == TYPE_INCOME)
        )
    }
    return Ctx(session, user, methods, expense, income)


@pytest.fixture
def bot_ctx(tmp_path, monkeypatch):
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


class MockMessage:
    def __init__(self, text: str = "", document=None) -> None:
        self.text = text
        self.document = document
        self.replies: list[str] = []
        self.documents: list[tuple[str, bytes]] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append(text)

    async def reply_document(self, document, filename: str, caption: str = "", **kwargs) -> None:
        data = document.read() if hasattr(document, "read") else bytes(document)
        self.documents.append((filename, data))
        if caption:
            self.replies.append(caption)


class MockDocument:
    def __init__(self, filename: str, data: bytes) -> None:
        self.file_name = filename
        self._data = data

    async def get_file(self):
        m = AsyncMock()
        m.download_as_bytearray = AsyncMock(return_value=bytearray(self._data))
        return m


class MockUpdate:
    def __init__(self, user_id: int, text: str = "", document=None) -> None:
        self.effective_user = AsyncMock()
        self.effective_user.id = user_id
        self.effective_user.first_name = f"User_{user_id}"
        self.effective_message = MockMessage(text, document)


class MockContext:
    def __init__(self, session_factory, args: list[str] | None = None) -> None:
        self.application = AsyncMock()
        self.application.bot_data = {"session_factory": session_factory}
        self.user_data: dict = {}
        self.args = args or []


# -----------------------------------------------------------------------------
# 1. Formula Injection Neutralization
# -----------------------------------------------------------------------------
def test_formula_injection_neutralization_in_exports(session, alice):
    """Nilai sel teks diawali =, +, -, @ dinetralkan dengan awalan tanda kutip tunggal (')"""
    alice.open(alice.cash, 1_000_000)

    # Catat transaksi dengan keterangan berpotensi formula injection
    tx_service.add_expense(
        session,
        alice.id,
        25_000,
        alice.food,
        alice.cash,
        on="10-02-2026",
        note="=SUM(A1:A10)",
    )
    tx_service.add_expense(
        session,
        alice.id,
        30_000,
        alice.food,
        alice.cash,
        on="11-02-2026",
        note="+cmd|' /C calc'!A0",
    )
    tx_service.add_expense(
        session,
        alice.id,
        15_000,
        alice.food,
        alice.cash,
        on="12-02-2026",
        note="-2+3*4",
    )
    tx_service.add_expense(
        session,
        alice.id,
        50_000,
        alice.food,
        alice.cash,
        on="13-02-2026",
        note="@SUM(B1:B5)",
    )
    # Transaksi normal
    tx_service.add_expense(
        session,
        alice.id,
        10_000,
        alice.food,
        alice.cash,
        on="14-02-2026",
        note="Makan siang biasa",
    )
    session.commit()

    # 1.1 Ekspor CSV
    csv_path = export_service.export_csv(session, alice.id)
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "'=SUM(A1:A10)" in csv_text
    assert "'+cmd|' /C calc'!A0" in csv_text
    assert "'-2+3*4" in csv_text
    assert "'@SUM(B1:B5)" in csv_text
    assert "Makan siang biasa" in csv_text

    # 1.2 Ekspor Excel (.xlsx)
    xlsx_path = export_service.export_excel(session, alice.id)
    wb = openpyxl.load_workbook(xlsx_path)
    ws_tx = wb["Daftar Transaksi"]

    notes_in_excel = []
    for row in ws_tx.iter_rows(min_row=2, values_only=True):
        note_val = row[8]  # Kolom Keterangan
        if note_val:
            notes_in_excel.append(str(note_val))

    assert "'=SUM(A1:A10)" in notes_in_excel
    assert "'+cmd|' /C calc'!A0" in notes_in_excel
    assert "'-2+3*4" in notes_in_excel
    assert "'@SUM(B1:B5)" in notes_in_excel
    assert "Makan siang biasa" in notes_in_excel


def test_formula_injection_neutralization_in_backup_zip(session, alice, tmp_path):
    """transactions.csv di dalam ZIP cadangan juga dinetralkan dari formula injection."""
    alice.open(alice.cash, 100_000)
    tx_service.add_expense(
        session,
        alice.id,
        20_000,
        alice.food,
        alice.cash,
        on="15-02-2026",
        note="=HYPERLINK('http://malicious.com','Click Me')",
    )
    session.commit()

    zip_file = tmp_path / "alice_backup.zip"
    backup_service.export_user_backup_zip(session, alice.id, file_path=zip_file)

    with zipfile.ZipFile(zip_file, "r") as zf:
        tx_csv = zf.read("transactions.csv").decode("utf-8")
        assert "'=HYPERLINK('http://malicious.com','Click Me')" in tx_csv


# -----------------------------------------------------------------------------
# 2. Corrupt and Foreign Backup File Rejection
# -----------------------------------------------------------------------------
def test_reject_corrupted_backup_files(session, alice, tmp_path):
    """File cadangan rusak (JSON tidak valid atau ZIP corrupt) harus ditolak."""
    # 2.1 File JSON rusak
    bad_json = tmp_path / "corrupt.json"
    bad_json.write_text("{tidak lengkap", encoding="utf-8")
    with pytest.raises(ValidationError, match="Gagal membaca file cadangan"):
        backup_service.restore_backup(session, alice.id, bad_json)

    # 2.2 File ZIP rusak
    bad_zip = tmp_path / "corrupt.zip"
    bad_zip.write_bytes(b"PK\x03\x04bukan zip yang valid atau rusak")
    with pytest.raises(ValidationError, match="File arsip ZIP rusak"):
        backup_service.restore_backup(session, alice.id, bad_zip)

    # 2.3 Bytes ZIP rusak langsung
    with pytest.raises(ValidationError, match="File arsip ZIP rusak"):
        backup_service.validate_backup_data(
            session, alice.id, b"PK\x03\x04corrupted_zip_bytes_data"
        )

    # 2.4 ZIP valid tetapi tidak memiliki backup.json
    empty_zip = tmp_path / "no_json.zip"
    with zipfile.ZipFile(empty_zip, "w") as zf:
        zf.writestr("test.txt", "hello")
    with pytest.raises(ValidationError, match=r"tidak memiliki file backup\.json"):
        backup_service.restore_backup(session, alice.id, empty_zip)

    # 2.5 Data JSON tidak memiliki kunci yang dibutuhkan
    invalid_data = {
        "version": "1.0",
        "user_id": alice.id,
        "data": {
            "payment_methods": [],
            # categories hilang
        },
    }
    with pytest.raises(ValidationError, match="kunci 'categories' hilang"):
        backup_service.validate_backup_data(session, alice.id, invalid_data)


def test_reject_foreign_user_backup(session, alice, bob, tmp_path):
    """Cadangan milik pengguna A yang dicoba dipulihkan oleh pengguna B harus ditolak."""
    alice.open(alice.cash, 100_000)
    bob.open(bob.cash, 50_000)
    session.commit()

    # Alice buat file cadangan
    alice_zip = tmp_path / "alice.zip"
    backup_service.export_user_backup_zip(session, alice.id, file_path=alice_zip)

    # Bob mencoba memulihkan cadangan Alice
    with pytest.raises(ValidationError, match="bukan pengguna saat ini"):
        backup_service.restore_backup(session, bob.id, alice_zip)

    # Pastikan data Bob tidak tersentuh dan saldo Bob tetap 50_000
    assert balances.get_total_balance(session, bob.id) == 50_000
    assert balances.get_total_balance(session, alice.id) == 100_000


# -----------------------------------------------------------------------------
# 3. User Backup ZIP Export & Restore Round-trip
# -----------------------------------------------------------------------------
def test_user_backup_zip_roundtrip_isolation(session, alice, bob, tmp_path):
    """Ekspor ZIP lengkap dan pemulihan data berhasil memulihkan seluruh data dan saldo."""
    alice.open(alice.cash, 500_000)
    alice.open(alice.bank, 1_500_000)
    bob.open(bob.cash, 200_000)

    # Alice buat transaksi transfer dengan biaya admin
    transfers.add_transfer(
        session,
        alice.id,
        200_000,
        alice.bank,
        alice.cash,
        on="01-02-2026",
        note="Tarik tunai",
        admin_fee=2_500,
    )
    session.commit()

    total_alice_before = balances.get_total_balance(session, alice.id)
    total_bob_before = balances.get_total_balance(session, bob.id)

    # Alice ekspor ke ZIP
    alice_zip = tmp_path / "alice_full.zip"
    backup_service.export_user_backup_zip(session, alice.id, file_path=alice_zip)
    assert alice_zip.is_file()

    # Cek isi zip
    with zipfile.ZipFile(alice_zip, "r") as zf:
        names = zf.namelist()
        assert "manifest.json" in names
        assert "backup.json" in names
        assert "transactions.csv" in names
        assert "categories.csv" in names
        assert "payment_methods.csv" in names
        assert "budgets.csv" in names

    # Alice catat pengeluaran baru (sehingga saldo berubah)
    tx_service.add_expense(
        session,
        alice.id,
        100_000,
        alice.food,
        alice.cash,
        on="02-02-2026",
        note="Makan malam",
    )
    session.commit()
    assert balances.get_total_balance(session, alice.id) != total_alice_before

    # Alice memulihkan dari ZIP
    res = backup_service.restore_backup(session, alice.id, alice_zip)
    session.commit()

    assert res["transactions"] == 2  # transfer + admin fee
    assert balances.get_total_balance(session, alice.id) == total_alice_before
    # Bob sama sekali tidak tersentuh
    assert balances.get_total_balance(session, bob.id) == total_bob_before


# -----------------------------------------------------------------------------
# 4. Permanent Account Deletion (`/hapus_akun` & `delete_user_account`)
# -----------------------------------------------------------------------------
def test_delete_user_account_complete_cleanup_and_isolation(session, alice, bob):
    """Penghapusan akun menghapus seluruh data pemiliknya tanpa memengaruhi pengguna lain."""
    # Setup data lengkap untuk Alice:
    alice.open(alice.cash, 1_000_000)
    alice.open(alice.bank, 2_000_000)

    # Bob juga punya data
    bob.open(bob.cash, 750_000)
    tx_service.add_expense(
        session, bob.id, 50_000, bob.food, bob.cash, on="01-02-2026", note="Makan Bob"
    )

    # Transfer Alice dengan biaya admin (ada related_transaction_id yang merujuk transaksi lain)
    transfers.add_transfer(
        session,
        alice.id,
        300_000,
        alice.bank,
        alice.cash,
        on="02-02-2026",
        note="Transfer Alice",
        admin_fee=5_000,
    )
    tx_service.add_expense(
        session,
        alice.id,
        40_000,
        alice.food,
        alice.cash,
        on="03-02-2026",
        note="Belanja Alice",
    )
    session.commit()

    # Pastikan data Alice dan Bob ada
    assert queries.get_user(session, alice.id) is not None
    assert queries.get_user(session, bob.id) is not None
    assert len(tx_service.list_transactions(session, alice.id)) == 3
    assert len(tx_service.list_transactions(session, bob.id)) == 1

    # Hapus akun Alice
    users_service.delete_user_account(session, alice.id)
    session.commit()

    # 1. Alice dan seluruh datanya terhapus secara permanen
    assert queries.get_user(session, alice.id) is None
    assert queries.list_transactions(session, alice.id, include_deleted=True) == []
    assert queries.list_categories(session, alice.id, include_system=True) == []
    assert queries.list_methods(session, alice.id) == []

    # 2. Data Bob TETAP 100% utuh
    bob_user = queries.get_user(session, bob.id)
    assert bob_user is not None
    bob_txs = tx_service.list_transactions(session, bob.id)
    assert len(bob_txs) == 1
    assert bob_txs[0].note == "Makan Bob"
    assert balances.get_total_balance(session, bob.id) == 700_000


# -----------------------------------------------------------------------------
# 5. Full Admin Backup
# -----------------------------------------------------------------------------
def test_full_admin_backup_permissions(session, alice, bob, tmp_path):
    """Full backup sistem hanya dapat dijalankan oleh admin."""
    # Alice user biasa, Bob admin
    u_alice = queries.get_user(session, alice.id)
    u_bob = queries.get_user(session, bob.id)
    u_alice.role = ROLE_USER
    u_bob.role = ROLE_ADMIN
    session.commit()

    # Alice (bukan admin) mencoba full backup -> ditolak
    with pytest.raises(ValidationError, match="Hanya admin"):
        backup_service.export_full_admin_backup(session, alice.id)

    # Bob (admin) menjalankan full backup -> berhasil
    admin_zip = tmp_path / "system_backup.zip"
    backup_service.export_full_admin_backup(session, bob.id, file_path=admin_zip)
    assert admin_zip.is_file()

    with zipfile.ZipFile(admin_zip, "r") as zf:
        names = zf.namelist()
        assert "system_manifest.json" in names
        assert "invites.json" in names
        # Terdapat file json untuk masing-masing user
        assert any(n.startswith("users/user_") for n in names)


# -----------------------------------------------------------------------------
# 6. Daily Transaction Limit (`MAX_DAILY_TRANSACTIONS_PER_USER`)
# -----------------------------------------------------------------------------
def test_daily_transaction_limit_per_user(session, alice, bob, monkeypatch):
    """Pengguna tidak boleh melebihi batas jumlah transaksi per hari."""
    alice.open(alice.cash, 10_000_000)
    bob.open(bob.cash, 10_000_000)
    session.commit()

    # Set batas menjadi 3 transaksi per hari untuk pengujian
    monkeypatch.setattr(config, "MAX_DAILY_TRANSACTIONS_PER_USER", 3)

    target_date = "20-02-2026"
    other_date = "21-02-2026"

    # Alice catat 3 transaksi di target_date
    tx_service.add_expense(
        session, alice.id, 10_000, alice.food, alice.cash, on=target_date, note="Tx 1"
    )
    tx_service.add_expense(
        session, alice.id, 10_000, alice.food, alice.cash, on=target_date, note="Tx 2"
    )
    tx_service.add_expense(
        session, alice.id, 10_000, alice.food, alice.cash, on=target_date, note="Tx 3"
    )
    session.commit()

    # Transaksi ke-4 di target_date harus ditolak
    with pytest.raises(ValidationError, match="Batas transaksi harian"):
        tx_service.add_expense(
            session, alice.id, 10_000, alice.food, alice.cash, on=target_date, note="Tx 4"
        )

    # Namun Alice masih bisa mencatat transaksi di tanggal yang berbeda
    tx_other = tx_service.add_expense(
        session, alice.id, 10_000, alice.food, alice.cash, on=other_date, note="Tx Other"
    )
    assert tx_other.transaction.note == "Tx Other"

    # Dan Bob (pengguna lain) tidak terpengaruh batas harian Alice
    tx_bob = tx_service.add_expense(
        session, bob.id, 10_000, bob.food, bob.cash, on=target_date, note="Bob Tx 1"
    )
    assert tx_bob.transaction.note == "Bob Tx 1"


def test_transfer_with_admin_fee_checks_daily_limit(session, alice, monkeypatch):
    """Transfer dengan biaya admin dihitung sebagai 2 transaksi untuk batas harian."""
    alice.open(alice.cash, 5_000_000)
    alice.open(alice.bank, 5_000_000)
    session.commit()

    monkeypatch.setattr(config, "MAX_DAILY_TRANSACTIONS_PER_USER", 2)
    tx_date = "22-02-2026"

    # Catat 1 transaksi pengeluaran
    tx_service.add_expense(
        session, alice.id, 10_000, alice.food, alice.cash, on=tx_date, note="Tx 1"
    )

    # Transfer dengan biaya admin membutuhkan 2 kuota (transfer + admin fee) -> harus ditolak
    with pytest.raises(ValidationError, match="Batas transaksi harian"):
        transfers.add_transfer(
            session,
            alice.id,
            100_000,
            alice.cash,
            alice.bank,
            on=tx_date,
            admin_fee=2_500,
        )

    # Transfer tanpa biaya admin (hanya butuh 1 kuota) -> diizinkan
    trf_res = transfers.add_transfer(
        session,
        alice.id,
        100_000,
        alice.cash,
        alice.bank,
        on=tx_date,
        admin_fee=0,
    )
    assert trf_res.transfer.amount == 100_000


# -----------------------------------------------------------------------------
# 7. Rate Limiter (`UserRateLimiter`)
# -----------------------------------------------------------------------------
def test_user_rate_limiter_isolated():
    """Rate limiter membatasi jumlah permintaan per jendela waktu dan terisolasi per pengguna."""
    limiter = UserRateLimiter(default_limit=3, window_seconds=60.0)

    # User 1 mengirim 3 request -> diizinkan
    assert limiter.is_allowed(1) is True
    assert limiter.is_allowed(1) is True
    assert limiter.is_allowed(1) is True

    # Request ke-4 dari User 1 ditolak
    assert limiter.is_allowed(1) is False
    assert limiter.remaining(1) == 0

    # User 2 tidak terpengaruh kuota User 1
    assert limiter.is_allowed(2) is True
    assert limiter.remaining(2) == 2

    # Reset kuota User 1
    limiter.reset(1)
    assert limiter.is_allowed(1) is True


# -----------------------------------------------------------------------------
# 8. Bot Handlers (`/start`, `/ekspor_data`, `/hapus_akun`)
# -----------------------------------------------------------------------------
def test_bot_start_contains_privacy_message(bot_ctx):
    """Perintah /start menampilkan pesan privasi, isolasi data, dan kendali data pengguna."""
    factory, tg_id = bot_ctx
    ctx = MockContext(factory)
    upd = MockUpdate(tg_id, text="/start")

    asyncio.run(cmd_start(upd, ctx))
    reply = upd.effective_message.replies[0]

    assert "Privasi & Kendali Data" in reply
    assert "diisolasi secara penuh" in reply
    assert "/ekspor_data" in reply
    assert "/hapus_akun" in reply


def test_bot_ekspor_data_sends_zip_document(bot_ctx):
    """Perintah /ekspor_data mengirim file arsip .zip berisi data keuangan pengguna."""
    factory, tg_id = bot_ctx
    ctx = MockContext(factory)
    user_id = asyncio.run(resolve_db_user_id(factory, tg_id))
    ctx.user_data["db_user_id"] = user_id

    upd = MockUpdate(tg_id, text="/ekspor_data")
    asyncio.run(cmd_ekspor_data(upd, ctx))

    assert len(upd.effective_message.documents) == 1
    filename, zip_bytes = upd.effective_message.documents[0]
    assert filename.endswith(".zip")
    assert "backup_user_" in filename

    # Verifikasi isi zip
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "backup.json" in names
        assert "transactions.csv" in names


def test_bot_hapus_akun_confirmation_flow(bot_ctx):
    """Perintah /hapus_akun meminta konfirmasi sebelum menghapus data."""
    factory, tg_id = bot_ctx
    ctx = MockContext(factory)
    user_id = asyncio.run(resolve_db_user_id(factory, tg_id))
    ctx.user_data["db_user_id"] = user_id

    # 1. /hapus_akun tanpa konfirmasi -> tampilkan peringatan
    upd_warn = MockUpdate(tg_id, text="/hapus_akun")
    asyncio.run(cmd_hapus_akun(upd_warn, ctx))
    assert "PERINGATAN HAPUS AKUN" in upd_warn.effective_message.replies[0]
    assert "/hapus_akun KONFIRMASI" in upd_warn.effective_message.replies[0]

    # Pastikan pengguna masih ada
    with session_scope(factory) as session:
        assert queries.get_user(session, user_id) is not None

    # 2. /hapus_akun KONFIRMASI -> akun dan data terhapus
    ctx.args = ["KONFIRMASI"]
    upd_del = MockUpdate(tg_id, text="/hapus_akun KONFIRMASI")
    asyncio.run(cmd_hapus_akun(upd_del, ctx))
    assert "AKUN BERHASIL DIHAPUS" in upd_del.effective_message.replies[0]

    # Pastikan pengguna sudah terhapus permanen dari database
    with session_scope(factory) as session:
        assert queries.get_user(session, user_id) is None


def test_bot_rate_limiting_enforcement(bot_ctx, monkeypatch):
    """Panggilan bot yang melebihi batas rate limit dibalas dengan pesan peringatan."""
    factory, tg_id = bot_ctx
    ctx = MockContext(factory)
    user_id = asyncio.run(resolve_db_user_id(factory, tg_id))
    ctx.user_data["db_user_id"] = user_id

    # Reset limiter dan atur limit rendah untuk pengujian
    global_rate_limiter.reset()
    monkeypatch.setattr(global_rate_limiter, "default_limit", 2)

    upd1 = MockUpdate(tg_id, text="/saldo")
    upd2 = MockUpdate(tg_id, text="/saldo")
    upd3 = MockUpdate(tg_id, text="/saldo")

    from bot.handlers.quick import cmd_saldo

    asyncio.run(cmd_saldo(upd1, ctx))
    asyncio.run(cmd_saldo(upd2, ctx))
    asyncio.run(cmd_saldo(upd3, ctx))

    # Permintaan ke-3 harus diblokir oleh rate limiter
    assert RATE_LIMIT_MESSAGE in upd3.effective_message.replies[0]
