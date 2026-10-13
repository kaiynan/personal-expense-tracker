"""Tes isolasi data antar pengguna: data A tidak boleh memengaruhi B."""

from sqlalchemy import select

from database.balances import method_balances, total_balance
from database.seed import seed_defaults
from models import Budget, Category, PaymentMethod


def test_balances_are_independent_per_user(session, make_user, make_method, add_tx):
    alice = make_user("Alice")
    bob = make_user("Bob")
    alice_cash = make_method(alice, "Cash", opening=1_000_000)
    bob_cash = make_method(bob, "Cash", opening=200_000)
    add_tx(alice, "expense", 300_000, alice_cash)
    add_tx(bob, "income", 50_000, bob_cash)

    assert method_balances(session, alice.id) == {alice_cash.id: 700_000}
    assert method_balances(session, bob.id) == {bob_cash.id: 250_000}
    assert total_balance(session, alice.id) == 700_000
    assert total_balance(session, bob.id) == 250_000


def test_unknown_user_has_zero_balance(session, make_user, make_method, add_tx):
    alice = make_user("Alice")
    cash = make_method(alice, "Cash", opening=1_000_000)
    add_tx(alice, "income", 5_000, cash)

    assert method_balances(session, user_id=9999) == {}
    assert total_balance(session, user_id=9999) == 0


def test_foreign_transaction_does_not_leak_into_other_users_balance(
    session, make_user, make_method, add_tx
):
    """Transaksi yang (salah) merujuk metode milik pengguna lain tetap tidak
    ikut terhitung di saldo pengguna pemilik metode."""
    alice = make_user("Alice")
    bob = make_user("Bob")
    alice_cash = make_method(alice, "Cash", opening=1_000_000)
    add_tx(bob, "expense", 400_000, alice_cash)  # milik Bob, tetapi memakai metode Alice

    assert total_balance(session, alice.id) == 1_000_000


def test_seed_is_separate_per_user(session, make_user):
    alice = make_user("Alice")
    bob = make_user("Bob")
    seed_defaults(session, alice.id)
    seed_defaults(session, bob.id)

    for user in (alice, bob):
        methods = session.scalars(
            select(PaymentMethod).where(PaymentMethod.user_id == user.id)
        ).all()
        categories = session.scalars(select(Category).where(Category.user_id == user.id)).all()
        assert len(methods) == 3
        assert len(categories) == 18  # 10 pengeluaran + 7 pemasukan + Biaya Admin


def test_same_total_budget_month_allowed_for_different_users(session, make_user):
    alice = make_user("Alice")
    bob = make_user("Bob")
    session.add(Budget(user_id=alice.id, category_id=None, month="2026-02", amount=1_000_000))
    session.add(Budget(user_id=bob.id, category_id=None, month="2026-02", amount=2_000_000))
    session.flush()


def test_reports_join_does_not_leak_foreign_category(session, make_user, make_method):
    """Jika transaksi merujuk category_id pengguna lain, nama kategori itu tidak bocor."""
    from datetime import date

    from database.reports import (
        get_category_breakdown,
        get_extreme_transactions,
        get_top_expenses,
    )
    from models import Transaction

    alice = make_user("Alice")
    bob = make_user("Bob")
    alice_cat = Category(user_id=alice.id, name="KategoriRahasiaAlice", type="expense")
    session.add(alice_cat)
    session.flush()
    bob_cash = make_method(bob, "Cash", opening=500_000)

    # Baris transaksi Bob yang secara paksa merujuk category_id milik Alice
    session.add(
        Transaction(
            user_id=bob.id,
            type="expense",
            amount=100_000,
            date=date(2026, 10, 10),
            category_id=alice_cat.id,
            method_id=bob_cash.id,
            note="Tes join",
        )
    )
    session.flush()

    start, end = date(2026, 10, 1), date(2026, 10, 31)
    assert get_category_breakdown(session, bob.id, "expense", start, end) == []
    top = get_top_expenses(session, bob.id, start, end)
    assert len(top) == 1
    assert top[0][2] == "-"  # nama kategori Alice TIDAK bocor
    ext = get_extreme_transactions(session, bob.id, start, end)
    assert ext["max_expense"] is not None
    assert ext["max_expense"][2] == "-"


def test_two_telegram_accounts_registration_and_complete_data_isolation(tmp_path, monkeypatch):
    """Dua akun Telegram uji mendaftar lewat V3 dan tidak bisa saling melihat data."""
    import asyncio

    import pytest

    import config
    from bot.auth import BLOCKED_MESSAGE, PENDING_MESSAGE
    from bot.handlers.admin import (
        cmd_blokir,
        cmd_buat_undangan,
        cmd_kode,
        cmd_pengguna,
        cmd_setujui,
    )
    from bot.handlers.aliases import quick_confirm_callback
    from bot.handlers.backup import cmd_backup
    from bot.handlers.common import cmd_start, handle_text_menu
    from bot.handlers.export import cmd_export_csv
    from bot.handlers.quick import cmd_hari_ini, cmd_riwayat, cmd_saldo
    from bot.handlers.reports import cmd_harian
    from database.engine import create_db_engine, create_session_factory, session_scope
    from models import Base
    from services import transactions as tx_svc
    from tests.test_bot import MockContext, MockUpdate
    from utils.errors import NotFoundError

    db_path = tmp_path / "v3_multiuser.db"
    engine = create_db_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)

    tg_admin = 900001
    tg_user_a = 100111
    tg_user_b = 200222

    monkeypatch.setattr(config, "ALLOWED_TELEGRAM_IDS", frozenset({tg_admin}))
    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path / "reports_output")

    async def _scenario() -> None:
        ctx_admin = MockContext(factory)
        ctx_a = MockContext(factory)
        ctx_b = MockContext(factory)

        # 1. Admin menjalankan /start -> otomatis aktif sebagai admin
        u_adm = MockUpdate(tg_admin, text="/start")
        await cmd_start(u_adm, ctx_admin)
        assert "Selamat datang" in u_adm.effective_message.replies[-1]
        assert "Perintah Admin" in u_adm.effective_message.replies[-1]

        # 2. Akun Telegram A menjalankan /start tanpa kode -> berstatus pending
        u_a1 = MockUpdate(tg_user_a, text="/start")
        await cmd_start(u_a1, ctx_a)
        assert "pending" in u_a1.effective_message.replies[-1].lower()
        assert "db_user_id" not in ctx_a.user_data

        # Saat masih pending, Akun A tidak bisa menjalankan /saldo
        u_a_denied = MockUpdate(tg_user_a, text="/saldo")
        await cmd_saldo(u_a_denied, ctx_a)
        assert u_a_denied.effective_message.replies[-1] == PENDING_MESSAGE

        # 3. Admin membuat kode undangan KODE-A1, lalu Akun A mengaktifkan lewat /start KODE-A1
        u_inv = MockUpdate(tg_admin, text="/buat_undangan KODE-A1")
        await cmd_buat_undangan(u_inv, ctx_admin)
        assert "KODE-A1" in u_inv.effective_message.replies[-1]

        u_a_act = MockUpdate(tg_user_a, text="/start KODE-A1")
        await cmd_start(u_a_act, ctx_a)
        assert "Selamat datang" in u_a_act.effective_message.replies[-1]
        user_a_db_id = ctx_a.user_data["db_user_id"]

        # 4. Akun Telegram B menjalankan /start -> pending
        u_b1 = MockUpdate(tg_user_b, text="/start")
        await cmd_start(u_b1, ctx_b)
        assert "pending" in u_b1.effective_message.replies[-1].lower()

        # Akun B mencoba memakai kode KODE-A1 yang sudah dipakai Akun A -> ditolak!
        u_b_reuse = MockUpdate(tg_user_b, text="/kode KODE-A1")
        await cmd_kode(u_b_reuse, ctx_b)
        assert "sudah pernah digunakan" in u_b_reuse.effective_message.replies[-1]

        # 5. Admin menyetujui Akun B lewat /setujui <tg_user_b>
        u_app = MockUpdate(tg_admin, text=f"/setujui {tg_user_b}")
        await cmd_setujui(u_app, ctx_admin)
        assert "disetujui" in u_app.effective_message.replies[-1]

        # Akun B kini aktif saat memanggil /start
        u_b_act = MockUpdate(tg_user_b, text="/start")
        await cmd_start(u_b_act, ctx_b)
        assert "Selamat datang" in u_b_act.effective_message.replies[-1]
        user_b_db_id = ctx_b.user_data["db_user_id"]
        assert user_a_db_id != user_b_db_id

        # Non-admin (Akun A) mencoba menjalankan perintah admin -> ditolak
        u_a_admin_try = MockUpdate(tg_user_a, text="/pengguna")
        await cmd_pengguna(u_a_admin_try, ctx_a)
        assert "admin" in u_a_admin_try.effective_message.replies[-1].lower()

        # 6. Akun A mencatat transaksi "makan 75000 cash"
        u_a_q = MockUpdate(tg_user_a, text="makan 75000 cash")
        await handle_text_menu(u_a_q, ctx_a)
        u_a_ok = MockUpdate(tg_user_a, callback_data="quick_confirm")
        await quick_confirm_callback(u_a_ok, ctx_a)
        assert "Rp75.000" in u_a_ok.effective_message.replies[-1]

        # Akun B mencatat transaksi "bensin 42000 bank"
        u_b_q = MockUpdate(tg_user_b, text="bensin 42000 bank")
        await handle_text_menu(u_b_q, ctx_b)
        u_b_ok = MockUpdate(tg_user_b, callback_data="quick_confirm")
        await quick_confirm_callback(u_b_ok, ctx_b)
        assert "Rp42.000" in u_b_ok.effective_message.replies[-1]

        # 7. Verifikasi dua akun Telegram tidak bisa saling melihat data di semua handler
        for cmd_fn in (cmd_saldo, cmd_hari_ini, cmd_riwayat, cmd_harian):
            ua = MockUpdate(tg_user_a)
            ub = MockUpdate(tg_user_b)
            await cmd_fn(ua, ctx_a)
            await cmd_fn(ub, ctx_b)
            reply_a = ua.effective_message.replies[-1]
            reply_b = ub.effective_message.replies[-1]
            assert "75.000" in reply_a
            assert "42.000" not in reply_a
            assert "42.000" in reply_b
            assert "75.000" not in reply_b

        # Verifikasi ekspor CSV dan backup JSON terisolasi penuh
        ua_csv = MockUpdate(tg_user_a, text="/export_csv")
        ub_csv = MockUpdate(tg_user_b, text="/export_csv")
        await cmd_export_csv(ua_csv, ctx_a)
        await cmd_export_csv(ub_csv, ctx_b)
        csv_a_bytes = ua_csv.effective_message.documents[-1][1].decode("utf-8")
        csv_b_bytes = ub_csv.effective_message.documents[-1][1].decode("utf-8")
        assert "75000" in csv_a_bytes and "42000" not in csv_a_bytes
        assert "42000" in csv_b_bytes and "75000" not in csv_b_bytes

        ua_bkp = MockUpdate(tg_user_a, text="/backup")
        ub_bkp = MockUpdate(tg_user_b, text="/backup")
        await cmd_backup(ua_bkp, ctx_a)
        await cmd_backup(ub_bkp, ctx_b)
        bkp_a_json = ua_bkp.effective_message.documents[-1][1].decode("utf-8")
        bkp_b_json = ub_bkp.effective_message.documents[-1][1].decode("utf-8")
        assert "75000" in bkp_a_json and "42000" not in bkp_a_json
        assert "42000" in bkp_b_json and "75000" not in bkp_b_json

        # Verifikasi Akun B tidak bisa membaca/mengubah/menghapus transaksi Akun A lewat ID tebakan
        with session_scope(factory) as s:
            tx_a = tx_svc.list_transactions(s, user_a_db_id)[0]
            with pytest.raises(NotFoundError):
                tx_svc.get_transaction(s, user_b_db_id, tx_a.id)
            with pytest.raises(NotFoundError):
                tx_svc.edit_transaction(s, user_b_db_id, tx_a.id, amount=1)
            with pytest.raises(NotFoundError):
                tx_svc.delete_transaction(s, user_b_db_id, tx_a.id)

        # 8. Admin memblokir Akun B lewat /blokir -> Akun B langsung kehilangan akses
        u_blk = MockUpdate(tg_admin, text=f"/blokir {tg_user_b}")
        await cmd_blokir(u_blk, ctx_admin)
        assert "diblokir" in u_blk.effective_message.replies[-1]

        u_b_after_blk = MockUpdate(tg_user_b, text="/saldo")
        await cmd_saldo(u_b_after_blk, ctx_b)
        assert u_b_after_blk.effective_message.replies[-1] == BLOCKED_MESSAGE

    try:
        asyncio.run(_scenario())
    finally:
        engine.dispose()
