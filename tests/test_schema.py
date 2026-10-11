"""Tes constraint skema: aturan domain harus ditegakkan oleh database sendiri."""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from models import Budget, Category, PaymentMethod, Transaction

BASE = {"type": "income", "amount": 1000, "date": date(2026, 2, 1)}


@pytest.fixture
def user(make_user):
    return make_user()


@pytest.fixture
def cash(make_method, user):
    return make_method(user, "Cash")


@pytest.fixture
def bank(make_method, user):
    return make_method(user, "Bank")


def _expect_rejected(session, obj):
    session.add(obj)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


# --- transactions: CHECK --------------------------------------------------------
@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"type": "income", "amount": 0}, id="income_nol"),
        pytest.param({"type": "expense", "amount": -5}, id="expense_negatif"),
        pytest.param({"type": "transfer", "amount": 0, "to": "bank"}, id="transfer_nol"),
        pytest.param({"type": "adjustment", "amount": 0}, id="adjustment_nol"),
        pytest.param({"type": "transfer", "amount": 100}, id="transfer_tanpa_tujuan"),
        pytest.param(
            {"type": "transfer", "amount": 100, "to": "cash"}, id="transfer_asal_sama_tujuan"
        ),
        pytest.param({"type": "income", "amount": 100, "to": "bank"}, id="income_punya_tujuan"),
        pytest.param({"type": "bonus", "amount": 100}, id="jenis_tidak_valid"),
        pytest.param({"status": "archived"}, id="status_tidak_valid"),
    ],
)
def test_transaction_check_constraints_reject(session, user, cash, bank, overrides):
    overrides = dict(overrides)
    target = {None: None, "cash": cash.id, "bank": bank.id}[overrides.pop("to", None)]
    tx = Transaction(
        user_id=user.id, method_id=cash.id, to_method_id=target, **{**BASE, **overrides}
    )
    _expect_rejected(session, tx)


def test_negative_adjustment_is_allowed(session, user, cash):
    session.add(
        Transaction(
            user_id=user.id,
            method_id=cash.id,
            type="adjustment",
            amount=-50_000,
            date=date(2026, 2, 1),
        )
    )
    session.flush()


def test_valid_transfer_is_allowed(session, user, cash, bank):
    session.add(
        Transaction(
            user_id=user.id,
            method_id=bank.id,
            to_method_id=cash.id,
            type="transfer",
            amount=100_000,
            date=date(2026, 2, 1),
        )
    )
    session.flush()


def test_default_status_is_active(session, user, cash):
    tx = Transaction(user_id=user.id, method_id=cash.id, **BASE)
    session.add(tx)
    session.commit()
    assert tx.status == "active"


# --- foreign key ---------------------------------------------------------------
def test_foreign_key_is_enforced(session, user):
    _expect_rejected(session, Transaction(user_id=user.id, method_id=9999, **BASE))


def test_foreign_key_related_transaction_id_enforced(session, user, cash):
    _expect_rejected(
        session,
        Transaction(user_id=user.id, method_id=cash.id, related_transaction_id=9999, **BASE),
    )


def test_foreign_key_related_transaction_id_valid(session, user, cash):
    parent = Transaction(user_id=user.id, method_id=cash.id, **BASE)
    session.add(parent)
    session.flush()
    child = Transaction(
        user_id=user.id,
        method_id=cash.id,
        related_transaction_id=parent.id,
        **BASE,
    )
    session.add(child)
    session.flush()
    assert child.related_transaction_id == parent.id


# --- unique --------------------------------------------------------------------
def test_duplicate_method_name_same_user_rejected(session, user, cash):
    _expect_rejected(session, PaymentMethod(user_id=user.id, name="Cash"))


def test_same_method_name_different_user_allowed(session, make_user, cash):
    other = make_user("Lain")
    session.add(PaymentMethod(user_id=other.id, name="Cash"))
    session.flush()


def test_blank_method_name_rejected(session, user):
    _expect_rejected(session, PaymentMethod(user_id=user.id, name="   "))


def test_category_same_name_same_type_rejected(session, user):
    session.add(Category(user_id=user.id, name="Lainnya", type="expense"))
    session.commit()
    _expect_rejected(session, Category(user_id=user.id, name="Lainnya", type="expense"))


def test_category_same_name_different_type_allowed(session, user):
    session.add(Category(user_id=user.id, name="Lainnya", type="expense"))
    session.add(Category(user_id=user.id, name="Lainnya", type="income"))
    session.flush()


def test_category_invalid_type_rejected(session, user):
    _expect_rejected(session, Category(user_id=user.id, name="X", type="transfer"))


def test_unique_total_budget_per_month(session, user):
    """NULL dianggap berbeda oleh UNIQUE, jadi dijaga index parsial khusus."""
    session.add(Budget(user_id=user.id, category_id=None, month="2026-02", amount=1_000_000))
    session.commit()
    _expect_rejected(
        session, Budget(user_id=user.id, category_id=None, month="2026-02", amount=2_000_000)
    )


def test_total_budget_different_month_allowed(session, user):
    session.add(Budget(user_id=user.id, category_id=None, month="2026-02", amount=1_000_000))
    session.add(Budget(user_id=user.id, category_id=None, month="2026-03", amount=1_000_000))
    session.flush()


def test_unique_category_budget_per_month(session, user):
    category = Category(user_id=user.id, name="Makanan", type="expense")
    session.add(category)
    session.commit()
    session.add(Budget(user_id=user.id, category_id=category.id, month="2026-02", amount=500_000))
    session.commit()
    _expect_rejected(
        session,
        Budget(user_id=user.id, category_id=category.id, month="2026-02", amount=700_000),
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"amount": 0, "month": "2026-02"}, id="budget_nol"),
        pytest.param({"amount": 100, "month": "2026-2"}, id="format_bulan_salah"),
    ],
)
def test_budget_check_constraints_reject(session, user, kwargs):
    _expect_rejected(session, Budget(user_id=user.id, category_id=None, **kwargs))
