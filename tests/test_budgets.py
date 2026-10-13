"""Tes service budget: budget bulanan, per kategori, ambang 80% dan 100%, serta isolasi pengguna."""

from __future__ import annotations

import pytest

from database.engine import create_session_factory, session_scope
from database.seed import ensure_default_user, seed_defaults
from services import adjustments, budgets, transactions, transfers
from utils.errors import ValidationError


@pytest.fixture
def budget_ctx(engine):
    """Factory session dan user id untuk pengujian budget."""
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        u = ensure_default_user(session)
        seed_defaults(session, u.id)
        user_id = u.id

    from models import User

    with session_scope(factory) as session:
        u2 = User(name="User Lain")
        session.add(u2)
        session.flush()
        seed_defaults(session, u2.id)
        u2_id = u2.id

    return factory, user_id, u2_id


def test_set_and_get_monthly_total_budget(budget_ctx):
    factory, user_id, _ = budget_ctx
    with session_scope(factory) as session:
        b = budgets.set_monthly_budget(session, user_id, 3_000_000, "2026-10")
        assert b.amount == 3_000_000
        assert b.category_id is None
        assert b.month == "2026-10"

        fetched = budgets.get_monthly_budget(session, user_id, "2026-10")
        assert fetched is not None
        assert fetched.amount == 3_000_000

        # Update nominal bulan yang sama
        b_upd = budgets.set_monthly_budget(session, user_id, 3_500_000, "2026-10")
        assert b_upd.amount == 3_500_000


def test_set_and_get_category_budget(budget_ctx):
    factory, user_id, _ = budget_ctx
    with session_scope(factory) as session:
        from models import Category

        food = session.query(Category).filter_by(user_id=user_id, name="Makanan").one()
        salary = session.query(Category).filter_by(user_id=user_id, name="Gaji").one()

        # Set budget untuk kategori pengeluaran Makanan
        b = budgets.set_category_budget(session, user_id, food.id, 800_000, "2026-10")
        assert b.amount == 800_000
        assert b.category_id == food.id

        fetched = budgets.get_category_budget(session, user_id, food.id, "2026-10")
        assert fetched is not None
        assert fetched.amount == 800_000

        # Kategori pemasukan (Gaji) harus ditolak
        with pytest.raises(ValidationError):
            budgets.set_category_budget(session, user_id, salary.id, 500_000, "2026-10")


def test_budget_threshold_warnings_79_80_100(budget_ctx):
    """Uji ambang batas budget:
    - 79% -> safe (tidak ada peringatan)
    - 80% -> warning (mendekati batas)
    - 100% -> exceeded (melewati batas)
    """
    factory, user_id, _ = budget_ctx

    with session_scope(factory) as session:
        from models import Category, PaymentMethod

        food = session.query(Category).filter_by(user_id=user_id, name="Makanan").one()
        cash = session.query(PaymentMethod).filter_by(user_id=user_id, name="Cash").one()

        # Atur budget total: Rp1.000.000
        budgets.set_monthly_budget(session, user_id, 1_000_000, "2026-10")
        # Atur budget kategori Makanan: Rp500.000
        budgets.set_category_budget(session, user_id, food.id, 500_000, "2026-10")

        # Kasus 1: Pengeluaran 395.000 (79% dari 500k) -> Aman (<80%)
        transactions.add_expense(session, user_id, 395_000, food.id, cash.id, on="05-10-2026")

        eval1 = budgets.evaluate_budgets(session, user_id, "2026-10")
        cat_b1 = next(cb for cb in eval1.category_budgets if cb.category_id == food.id)
        assert cat_b1.percentage == 79.0
        assert cat_b1.warning_level == "safe"
        assert not eval1.has_warnings

        # Kasus 2: Tambah 5.000 sehingga total 400.000 (80% dari 500k) -> Warning (>=80%)
        transactions.add_expense(session, user_id, 5_000, food.id, cash.id, on="06-10-2026")

        eval2 = budgets.evaluate_budgets(session, user_id, "2026-10")
        cat_b2 = next(cb for cb in eval2.category_budgets if cb.category_id == food.id)
        assert cat_b2.percentage == 80.0
        assert cat_b2.warning_level == "warning"
        assert eval2.has_warnings
        assert any("MENDEKATI BATAS" in m for m in eval2.warning_messages)

        # Kasus 3: Tambah 100.000 sehingga total 500.000 (100% dari 500k) -> Exceeded (>=100%)
        transactions.add_expense(session, user_id, 100_000, food.id, cash.id, on="07-10-2026")

        eval3 = budgets.evaluate_budgets(session, user_id, "2026-10")
        cat_b3 = next(cb for cb in eval3.category_budgets if cb.category_id == food.id)
        assert cat_b3.percentage == 100.0
        assert cat_b3.warning_level == "exceeded"
        assert any("MELEWATI BATAS" in m for m in eval3.warning_messages)


def test_budget_ignores_transfers_and_adjustments(budget_ctx):
    """Transfer dan koreksi saldo tidak dihitung dalam pengeluaran budget."""
    factory, user_id, _ = budget_ctx

    with session_scope(factory) as session:
        from models import PaymentMethod

        cash = session.query(PaymentMethod).filter_by(user_id=user_id, name="Cash").one()
        bank = session.query(PaymentMethod).filter_by(user_id=user_id, name="Bank").one()

        budgets.set_monthly_budget(session, user_id, 1_000_000, "2026-10")

        # Transfer Bank -> Cash 900.000 (tanpa biaya admin)
        transfers.add_transfer(session, user_id, 900_000, bank.id, cash.id, on="10-10-2026")
        # Koreksi saldo 800.000
        adjustments.adjust_balance(session, user_id, cash.id, 800_000, on="10-10-2026")

        eval_res = budgets.evaluate_budgets(session, user_id, "2026-10")
        assert eval_res.total_budget.spent == 0
        assert eval_res.total_budget.warning_level == "safe"


def test_weekly_budget_monday_to_sunday_and_thresholds(budget_ctx):
    """Budget mingguan dievaluasi dari Senin s.d. Minggu dengan ambang 79%, 80%, 100%."""
    from datetime import date

    factory, user_id, _ = budget_ctx
    with session_scope(factory) as session:
        from models import Category, PaymentMethod

        food = session.query(Category).filter_by(user_id=user_id, name="Makanan").one()
        salary = session.query(Category).filter_by(user_id=user_id, name="Gaji").one()
        cash = session.query(PaymentMethod).filter_by(user_id=user_id, name="Cash").one()

        # 07-10-2026 adalah hari Rabu -> Senin minggu tersebut adalah 05-10-2026 (s.d. 11-10-2026)
        wb_total = budgets.set_weekly_budget(session, user_id, 500_000, on="07-10-2026")
        assert wb_total.week_start == date(2026, 10, 5)
        assert wb_total.amount == 500_000
        assert wb_total.category_id is None

        # Set budget mingguan kategori Makanan pada hari Minggu 11-10-2026 (masih minggu yang sama)
        wb_food = budgets.set_weekly_budget(
            session, user_id, 200_000, on="11-10-2026", category_id=food.id
        )
        assert wb_food.week_start == date(2026, 10, 5)
        assert wb_food.category_id == food.id

        # Kategori pemasukan ditolak
        with pytest.raises(ValidationError):
            budgets.set_weekly_budget(
                session, user_id, 100_000, on="07-10-2026", category_id=salary.id
            )

        # Transaksi di luar minggu (Minggu 04-10-2026 dan Senin 12-10-2026) tidak dihitung
        transactions.add_expense(session, user_id, 999_000, food.id, cash.id, on="04-10-2026")
        transactions.add_expense(session, user_id, 999_000, food.id, cash.id, on="12-10-2026")

        # 1. Pengeluaran 158.000 pada Senin 05-10-2026 (79% dari 200k) -> safe
        transactions.add_expense(session, user_id, 158_000, food.id, cash.id, on="05-10-2026")
        weval1 = budgets.evaluate_weekly_budgets(session, user_id, on="08-10-2026")
        assert weval1.week_start == date(2026, 10, 5)
        assert weval1.week_end == date(2026, 10, 11)
        cat_status1 = next(c for c in weval1.category_budgets if c.category_id == food.id)
        assert cat_status1.spent == 158_000
        assert cat_status1.percentage == 79.0
        assert cat_status1.warning_level == "safe"
        assert not weval1.has_warnings

        # 2. Tambah 2.000 pada Jumat 09-10-2026 -> 160.000 (80% dari 200k) -> warning
        transactions.add_expense(session, user_id, 2_000, food.id, cash.id, on="09-10-2026")
        weval2 = budgets.evaluate_weekly_budgets(session, user_id, on="09-10-2026")
        cat_status2 = next(c for c in weval2.category_budgets if c.category_id == food.id)
        assert cat_status2.percentage == 80.0
        assert cat_status2.warning_level == "warning"
        assert weval2.has_warnings
        assert any("MENDEKATI BATAS" in m for m in weval2.warning_messages)

        # 3. Tambah 40.000 pada Minggu 11-10-2026 -> 200.000 (100% dari 200k) -> exceeded
        transactions.add_expense(session, user_id, 40_000, food.id, cash.id, on="11-10-2026")
        weval3 = budgets.evaluate_weekly_budgets(session, user_id, on="11-10-2026")
        cat_status3 = next(c for c in weval3.category_budgets if c.category_id == food.id)
        assert cat_status3.percentage == 100.0
        assert cat_status3.warning_level == "exceeded"
        assert any("MELEWATI BATAS" in m for m in weval3.warning_messages)


def test_monthly_budget_rollover_surplus_and_deficit(budget_ctx):
    """Rollover membawa sisa positif ke bulan berikutnya dan tidak membawa defisit negatif."""
    factory, user_id, _ = budget_ctx

    with session_scope(factory) as session:
        from models import Category, PaymentMethod

        food = session.query(Category).filter_by(user_id=user_id, name="Makanan").one()
        transport = session.query(Category).filter_by(user_id=user_id, name="Transportasi").one()
        cash = session.query(PaymentMethod).filter_by(user_id=user_id, name="Cash").one()

        # Bulan 2026-09:
        # - Total Bulanan: Rp2.000.000, terpakai Rp1.500.000 -> sisa Rp500.000
        # - Makanan: Rp600.000, terpakai Rp400.000 -> sisa Rp200.000
        # - Transportasi: Rp300.000, terpakai Rp1.100.000 -> defisit -Rp800.000 (surplus 0)
        budgets.set_monthly_budget(session, user_id, 2_000_000, "2026-09")
        budgets.set_category_budget(session, user_id, food.id, 600_000, "2026-09")
        budgets.set_category_budget(session, user_id, transport.id, 300_000, "2026-09")

        transactions.add_expense(session, user_id, 400_000, food.id, cash.id, on="10-09-2026")
        transactions.add_expense(
            session, user_id, 1_100_000, transport.id, cash.id, on="15-09-2026"
        )

        # Jalankan rollover dari 2026-09 ke 2026-10
        rolled = budgets.rollover_budgets(
            session, user_id, from_month="2026-09", to_month="2026-10"
        )
        assert len(rolled) == 3

        by_cat = {r.category_id: r for r in rolled}
        # Total Bulanan: dasar 2.000.000 + rollover 500.000 = 2.500.000
        assert by_cat[None].surplus_carried_over == 500_000
        assert by_cat[None].new_base_amount == 2_000_000
        assert by_cat[None].new_effective_budget == 2_500_000

        # Makanan: dasar 600.000 + rollover 200.000 = 800.000
        assert by_cat[food.id].surplus_carried_over == 200_000
        assert by_cat[food.id].new_effective_budget == 800_000

        # Transportasi: defisit -> surplus_carried_over = 0, budget efektif tetap 300.000
        assert by_cat[transport.id].surplus_carried_over == 0
        assert by_cat[transport.id].new_effective_budget == 300_000

        # Evaluasi bulan 2026-10 harus memakai effective budget (base + carryover)
        transactions.add_expense(session, user_id, 640_000, food.id, cash.id, on="05-10-2026")
        eval_oct = budgets.evaluate_budgets(session, user_id, "2026-10")
        assert eval_oct.total_budget.amount == 2_500_000
        assert eval_oct.total_budget.base_amount == 2_000_000
        assert eval_oct.total_budget.carryover_amount == 500_000

        food_oct = next(c for c in eval_oct.category_budgets if c.category_id == food.id)
        assert food_oct.amount == 800_000
        assert food_oct.spent == 640_000
        assert food_oct.percentage == 80.0
        assert food_oct.warning_level == "warning"

        # Validasi error jika bulan asal >= bulan tujuan
        with pytest.raises(ValidationError):
            budgets.rollover_budgets(session, user_id, from_month="2026-10", to_month="2026-09")
