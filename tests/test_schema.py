"""Tes constraint skema: aturan domain harus ditegakkan oleh database sendiri."""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from models import (
    TARGET_CATEGORY,
    TARGET_PAYMENT_METHOD,
    AuditLog,
    Budget,
    Category,
    PaymentMethod,
    QuickAlias,
    RecurringTransaction,
    ReminderSetting,
    Transaction,
    WeeklyBudget,
)

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


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"amount": 0}, id="recurring_amount_nol"),
        pytest.param({"amount": -100}, id="recurring_amount_negatif"),
        pytest.param({"name": "   "}, id="recurring_name_blank"),
        pytest.param({"type": "transfer"}, id="recurring_type_invalid"),
        pytest.param({"frequency": "hourly"}, id="recurring_frequency_invalid"),
        pytest.param({"day_of_month": 0}, id="recurring_day_too_small"),
        pytest.param({"day_of_month": 32}, id="recurring_day_too_big"),
    ],
)
def test_recurring_check_constraints_reject(session, user, cash, overrides):
    cat = Category(user_id=user.id, name="TestCat", type="expense")
    session.add(cat)
    session.flush()

    data = {
        "user_id": user.id,
        "name": "Kos",
        "type": "expense",
        "amount": 1000,
        "category_id": cat.id,
        "method_id": cash.id,
        "frequency": "monthly",
        "day_of_month": 1,
        "start_date": date(2026, 1, 1),
    }
    data.update(overrides)
    _expect_rejected(session, RecurringTransaction(**data))


# --- audit_log: CHECK & FK ----------------------------------------------------
@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"action": "invalid_action"}, id="audit_action_invalid"),
        pytest.param({"action": "   "}, id="audit_action_blank"),
        pytest.param({"entity_type": "   "}, id="audit_entity_type_blank"),
    ],
)
def test_audit_log_check_constraints_reject(session, user, overrides):
    data = {
        "user_id": user.id,
        "action": "create",
        "entity_type": "transaction",
        "entity_id": 1,
    }
    data.update(overrides)
    _expect_rejected(session, AuditLog(**data))


def test_audit_log_foreign_key_user_enforced(session):
    _expect_rejected(
        session,
        AuditLog(
            user_id=9999,
            action="create",
            entity_type="transaction",
            entity_id=1,
        ),
    )


def test_audit_log_valid_insert(session, user):
    log = AuditLog(
        user_id=user.id,
        action="create",
        entity_type="transaction",
        entity_id=10,
        description="Tambah pengeluaran",
    )
    session.add(log)
    session.commit()
    assert log.id is not None
    assert log.is_undone is False


# --- quick_aliases: CHECK & FK & UNIQUE ----------------------------------------
@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param(
            {"alias": "  ", "target_type": TARGET_CATEGORY, "category_id": 1, "method_id": None},
            id="alias_blank",
        ),
        pytest.param(
            {"alias": "makan", "target_type": "invalid", "category_id": 1, "method_id": None},
            id="target_type_invalid",
        ),
        pytest.param(
            {
                "alias": "makan",
                "target_type": TARGET_CATEGORY,
                "category_id": None,
                "method_id": None,
            },
            id="category_target_without_cat_id",
        ),
        pytest.param(
            {"alias": "makan", "target_type": TARGET_CATEGORY, "category_id": 1, "method_id": 1},
            id="category_target_with_method_id",
        ),
        pytest.param(
            {
                "alias": "cash",
                "target_type": TARGET_PAYMENT_METHOD,
                "category_id": None,
                "method_id": None,
            },
            id="method_target_without_method_id",
        ),
        pytest.param(
            {
                "alias": "cash",
                "target_type": TARGET_PAYMENT_METHOD,
                "category_id": 1,
                "method_id": 1,
            },
            id="method_target_with_category_id",
        ),
    ],
)
def test_quick_alias_check_constraints_reject(session, user, kwargs):
    _expect_rejected(session, QuickAlias(user_id=user.id, **kwargs))


def test_quick_alias_unique_user_alias_reject(session, user, cash):
    qa1 = QuickAlias(
        user_id=user.id,
        alias="tunai",
        target_type=TARGET_PAYMENT_METHOD,
        method_id=cash.id,
    )
    session.add(qa1)
    session.commit()

    qa2 = QuickAlias(
        user_id=user.id,
        alias="tunai",
        target_type=TARGET_PAYMENT_METHOD,
        method_id=cash.id,
    )
    _expect_rejected(session, qa2)


def test_quick_alias_valid_insert(session, user, cash):
    cat = Category(user_id=user.id, name="Makan Siang", type="expense")
    session.add(cat)
    session.flush()

    qa_cat = QuickAlias(
        user_id=user.id,
        alias="makansiang",
        target_type=TARGET_CATEGORY,
        category_id=cat.id,
    )
    qa_met = QuickAlias(
        user_id=user.id,
        alias="cash",
        target_type=TARGET_PAYMENT_METHOD,
        method_id=cash.id,
    )
    session.add_all([qa_cat, qa_met])
    session.commit()
    assert qa_cat.id is not None
    assert qa_met.id is not None


# --- reminder_settings ----------------------------------------------------------
@pytest.mark.parametrize(
    "daily_time",
    [
        pytest.param("9:00", id="jam_terlalu_pendek"),
        pytest.param("20:000", id="jam_terlalu_panjang"),
        pytest.param("     ", id="jam_spasi_saja"),
    ],
)
def test_reminder_setting_check_constraints_reject(session, user, daily_time):
    rs = ReminderSetting(user_id=user.id, daily_time=daily_time)
    _expect_rejected(session, rs)


def test_reminder_setting_unique_user_id_reject(session, user):
    rs1 = ReminderSetting(user_id=user.id, daily_time="20:00")
    session.add(rs1)
    session.commit()

    rs2 = ReminderSetting(user_id=user.id, daily_time="08:00")
    _expect_rejected(session, rs2)


def test_reminder_setting_valid_insert(session, user):
    rs = ReminderSetting(
        user_id=user.id,
        is_daily_active=True,
        daily_time="19:30",
        is_weekly_active=True,
        is_monthly_active=True,
    )
    session.add(rs)
    session.commit()
    assert rs.id is not None
    assert rs.daily_time == "19:30"


# --- weekly_budgets & budget rollover -------------------------------------------
def test_budget_negative_carryover_rejected(session, user):
    b = Budget(
        user_id=user.id,
        category_id=None,
        month="2026-10",
        amount=1_000_000,
        carryover_amount=-100_000,
        is_rollover_enabled=True,
    )
    _expect_rejected(session, b)


def test_unique_total_weekly_budget_per_week(session, user):
    w_start = date(2026, 10, 5)
    session.add(WeeklyBudget(user_id=user.id, category_id=None, week_start=w_start, amount=500_000))
    session.commit()
    _expect_rejected(
        session,
        WeeklyBudget(user_id=user.id, category_id=None, week_start=w_start, amount=750_000),
    )


def test_unique_category_weekly_budget_per_week(session, user):
    cat = Category(user_id=user.id, name="Makanan", type="expense")
    session.add(cat)
    session.commit()
    w_start = date(2026, 10, 5)
    session.add(
        WeeklyBudget(user_id=user.id, category_id=cat.id, week_start=w_start, amount=200_000)
    )
    session.commit()
    _expect_rejected(
        session,
        WeeklyBudget(user_id=user.id, category_id=cat.id, week_start=w_start, amount=300_000),
    )


@pytest.mark.parametrize("amount", [0, -50_000])
def test_weekly_budget_non_positive_amount_rejected(session, user, amount):
    _expect_rejected(
        session,
        WeeklyBudget(
            user_id=user.id,
            category_id=None,
            week_start=date(2026, 10, 5),
            amount=amount,
        ),
    )


# --- V3 users (role, status) & invites -----------------------------------------
def test_user_invalid_role_or_status_rejected(session):
    from models import User

    _expect_rejected(session, User(name="InvalidRole", role="owner", status="active"))
    _expect_rejected(session, User(name="InvalidStatus", role="user", status="banned"))


def test_invite_constraints(session, user, make_user):
    from models import Invite

    other = make_user("CalonUser")

    # Kode kosong ditolak
    _expect_rejected(session, Invite(user_id=user.id, code="   "))

    # is_used=True tanpa used_by_user_id ditolak
    _expect_rejected(
        session, Invite(user_id=user.id, code="INV1", is_used=True, used_by_user_id=None)
    )

    # is_used=False dengan used_by_user_id terisi ditolak
    _expect_rejected(
        session,
        Invite(user_id=user.id, code="INV2", is_used=False, used_by_user_id=other.id),
    )

    # Duplikat kode ditolak
    inv = Invite(user_id=user.id, code="UNIK123", is_used=False)
    session.add(inv)
    session.commit()
    _expect_rejected(session, Invite(user_id=user.id, code="UNIK123", is_used=False))


# --- V3 user_settings & sent_notifications -------------------------------------
def test_user_setting_constraints(session, user):
    from models import UserSetting

    # Timezone kosong ditolak
    _expect_rejected(session, UserSetting(user_id=user.id, timezone="   ", daily_time="20:00"))

    # daily_time format salah ditolak
    _expect_rejected(
        session,
        UserSetting(user_id=user.id, timezone="Asia/Jakarta", daily_time="9:00"),
    )

    # Valid insert + unique user_id
    us1 = UserSetting(user_id=user.id, timezone="Asia/Makassar", daily_time="20:00")
    session.add(us1)
    session.commit()
    assert us1.id is not None

    _expect_rejected(
        session,
        UserSetting(user_id=user.id, timezone="Asia/Jayapura", daily_time="21:00"),
    )


def test_sent_notification_constraints(session, user):
    from models import SentNotification

    # notification_type tidak valid ditolak
    _expect_rejected(
        session,
        SentNotification(
            user_id=user.id, notification_type="invalid_type", period_key="2026-10-13"
        ),
    )

    # period_key kosong ditolak
    _expect_rejected(
        session,
        SentNotification(user_id=user.id, notification_type="daily", period_key="   "),
    )

    # Valid insert + unique (user_id, notification_type, period_key)
    sn1 = SentNotification(user_id=user.id, notification_type="daily", period_key="2026-10-13")
    session.add(sn1)
    session.commit()
    assert sn1.id is not None

    _expect_rejected(
        session,
        SentNotification(user_id=user.id, notification_type="daily", period_key="2026-10-13"),
    )
