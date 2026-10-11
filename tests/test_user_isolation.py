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
