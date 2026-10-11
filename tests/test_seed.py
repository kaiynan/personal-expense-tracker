"""Tes seed data awal."""

from sqlalchemy import func, select

from database.seed import (
    ADMIN_FEE_CATEGORY,
    DEFAULT_EXPENSE_CATEGORIES,
    DEFAULT_INCOME_CATEGORIES,
    DEFAULT_PAYMENT_METHODS,
    ensure_default_user,
    seed_defaults,
)
from models import Category, PaymentMethod, User


def _count(session, model, **filters) -> int:
    stmt = select(func.count()).select_from(model)
    for column, value in filters.items():
        stmt = stmt.where(getattr(model, column) == value)
    return session.scalar(stmt)


def test_ensure_default_user_creates_once(session):
    first = ensure_default_user(session)
    second = ensure_default_user(session)
    assert first.id == second.id
    assert _count(session, User) == 1


def test_default_user_has_jakarta_timezone(session):
    user = ensure_default_user(session)
    session.commit()
    assert user.timezone == "Asia/Jakarta"


def test_seed_creates_payment_methods(session):
    user = ensure_default_user(session)
    seed_defaults(session, user.id)
    names = set(session.scalars(select(PaymentMethod.name).where(PaymentMethod.user_id == user.id)))
    assert names == set(DEFAULT_PAYMENT_METHODS)


def test_seed_creates_default_categories(session):
    user = ensure_default_user(session)
    seed_defaults(session, user.id)
    rows = session.execute(
        select(Category.type, Category.name).where(Category.user_id == user.id)
    ).all()
    expense = {row.name for row in rows if row.type == "expense"}
    income = {row.name for row in rows if row.type == "income"}
    assert expense == set(DEFAULT_EXPENSE_CATEGORIES) | {ADMIN_FEE_CATEGORY}
    assert income == set(DEFAULT_INCOME_CATEGORIES)


def test_admin_fee_is_the_only_system_category(session):
    user = ensure_default_user(session)
    seed_defaults(session, user.id)
    system = session.scalars(
        select(Category).where(Category.user_id == user.id, Category.is_system.is_(True))
    ).all()
    assert [(c.name, c.type) for c in system] == [(ADMIN_FEE_CATEGORY, "expense")]


def test_seed_is_idempotent(session):
    user = ensure_default_user(session)
    seed_defaults(session, user.id)
    methods = _count(session, PaymentMethod)
    categories = _count(session, Category)

    seed_defaults(session, user.id)
    seed_defaults(session, user.id)

    assert _count(session, PaymentMethod) == methods == len(DEFAULT_PAYMENT_METHODS)
    assert _count(session, Category) == categories


def test_seed_fills_only_missing_items(session):
    user = ensure_default_user(session)
    session.add(PaymentMethod(user_id=user.id, name="Cash"))
    session.flush()
    seed_defaults(session, user.id)
    assert _count(session, PaymentMethod, user_id=user.id) == len(DEFAULT_PAYMENT_METHODS)
