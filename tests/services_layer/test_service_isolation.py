"""Tes isolasi pengguna untuk SETIAP fungsi service.

Pola: Alice punya data; Bob mencoba membaca/mengubah/memakai data Alice.
Bob harus ditolak (atau tidak melihat apa-apa), dan data serta saldo Alice
tidak berubah. Sebaliknya, operasi Alice tidak memengaruhi saldo Bob.
"""

import pytest

from database import queries
from models import STATUS_ACTIVE, STATUS_DELETED, User
from services import balances as bal
from services import transactions as svc
from services.adjustments import adjust_balance
from services.aliases import (
    add_alias,
    delete_alias,
    get_alias,
    list_aliases,
    parse_quick_input,
    seed_default_aliases,
)
from services.audit import (
    get_audit_log,
    list_audit_logs,
    record_audit_log,
    undo_action,
    undo_last,
)
from services.backup import (
    create_backup_data,
    export_backup,
    export_full_admin_backup,
    export_user_backup_zip,
    get_backup_summary,
    list_backups,
    restore_backup,
    validate_backup_data,
)
from services.budgets import (
    check_budget_warnings,
    evaluate_budgets,
    evaluate_weekly_budgets,
    get_category_budget,
    get_monthly_budget,
    get_weekly_budget,
    list_budgets,
    list_weekly_budgets,
    rollover_budgets,
    set_category_budget,
    set_monthly_budget,
    set_weekly_budget,
)
from services.categories import (
    add_category,
    add_payment_method,
    archive_category,
    archive_payment_method,
    get_category,
    list_categories,
    list_payment_methods,
    rename_category,
    rename_payment_method,
    unarchive_category,
    unarchive_payment_method,
)
from services.dashboard import (
    get_balance_trend_chart_data,
    get_cash_flow_chart_data,
    get_category_chart_data,
    get_dashboard_summary,
    get_runway_metrics,
)
from services.export import (
    export_csv,
    export_excel,
    export_pdf,
    get_export_data,
)
from services.opening_balances import get_opening_balance, set_opening_balance
from services.recurring import (
    activate_recurring,
    apply_recurring,
    create_recurring,
    deactivate_recurring,
    delete_recurring,
    get_next_due_date,
    get_recurring,
    list_recurring,
    process_due_recurring,
    update_recurring,
)
from services.reminders import (
    build_daily_reminder,
    build_monthly_summary,
    build_recurring_due_notification,
    build_weekly_summary,
    collect_due_notifications,
    collect_predictive_notifications,
    get_reminder_settings,
    get_user_settings,
    has_sent_notification,
    mark_reminder_sent,
    record_sent_notification,
    set_user_timezone,
    update_reminder_settings,
    update_user_settings,
)
from services.transfers import add_transfer
from services.users import (
    approve_user,
    block_user,
    create_invite,
    delete_user_account,
    list_invites,
    list_users,
    redeem_invite,
)
from tests.services_layer.conftest import D
from utils.errors import NotFoundError, ValidationError


@pytest.fixture
def setup(session, alice, bob):
    """Alice dan Bob masing-masing punya saldo dan satu transaksi."""
    alice.open(alice.cash, 1_000_000)
    bob.open(bob.cash, 500_000)
    a_tx = svc.add_expense(
        session, alice.id, 100_000, alice.food, alice.cash, on=D, note="rahasia alice"
    ).transaction
    b_tx = svc.add_expense(session, bob.id, 50_000, bob.food, bob.cash, on=D).transaction
    session.commit()
    return a_tx, b_tx


def _alice_unchanged(session, alice):
    assert bal.get_total_balance(session, alice.id) == 900_000


# --- transactions --------------------------------------------------------------
def test_add_income_rejects_other_users_category_and_method(session, alice, bob, setup):
    with pytest.raises(ValidationError):
        svc.add_income(session, bob.id, 1_000, alice.salary, bob.cash, on=D)
    with pytest.raises(ValidationError):
        svc.add_income(session, bob.id, 1_000, bob.salary, alice.cash, on=D)
    _alice_unchanged(session, alice)


def test_add_expense_rejects_other_users_category_and_method(session, alice, bob, setup):
    with pytest.raises(ValidationError):
        svc.add_expense(session, bob.id, 1_000, alice.food, bob.cash, on=D)
    with pytest.raises(ValidationError):
        svc.add_expense(session, bob.id, 1_000, bob.food, alice.cash, on=D)
    _alice_unchanged(session, alice)


def test_get_transaction_of_other_user_not_found(session, alice, bob, setup):
    a_tx, _ = setup
    with pytest.raises(NotFoundError):
        svc.get_transaction(session, bob.id, a_tx.id)
    with pytest.raises(NotFoundError):
        svc.get_transaction(session, bob.id, a_tx.id, include_deleted=True)


def test_list_transactions_only_own(session, alice, bob, setup):
    _a_tx, b_tx = setup
    assert [t.id for t in svc.list_transactions(session, bob.id, include_deleted=True)] == [b_tx.id]
    # filter dengan id milik Alice / kata kunci Alice tidak membocorkan apa pun
    assert svc.list_transactions(session, bob.id, keyword="rahasia") == []
    assert svc.list_transactions(session, bob.id, category_id=alice.food) == []
    assert svc.list_transactions(session, bob.id, method_id=alice.cash) == []
    assert svc.list_transactions(session, 9999) == []


def test_edit_transaction_of_other_user_not_found(session, alice, bob, setup):
    a_tx, _ = setup
    with pytest.raises(NotFoundError):
        svc.edit_transaction(session, bob.id, a_tx.id, amount=1)
    session.refresh(a_tx)
    assert a_tx.amount == 100_000
    _alice_unchanged(session, alice)


def test_edit_own_transaction_cannot_point_to_other_users_data(session, alice, bob, setup):
    _, b_tx = setup
    with pytest.raises(ValidationError):
        svc.edit_transaction(session, bob.id, b_tx.id, category_id=alice.food)
    with pytest.raises(ValidationError):
        svc.edit_transaction(session, bob.id, b_tx.id, method_id=alice.cash)
    bt = add_transfer(session, bob.id, 1_000, bob.cash, bob.bank, on=D).transfer
    with pytest.raises(ValidationError):
        svc.edit_transaction(session, bob.id, bt.id, to_method_id=alice.bank)
    _alice_unchanged(session, alice)


def test_delete_transaction_of_other_user_not_found(session, alice, bob, setup):
    a_tx, _ = setup
    with pytest.raises(NotFoundError):
        svc.delete_transaction(session, bob.id, a_tx.id)
    session.refresh(a_tx)
    assert a_tx.status == STATUS_ACTIVE
    _alice_unchanged(session, alice)


def test_restore_transaction_of_other_user_not_found(session, alice, bob, setup):
    a_tx, _ = setup
    svc.delete_transaction(session, alice.id, a_tx.id)
    with pytest.raises(NotFoundError):
        svc.restore_transaction(session, bob.id, a_tx.id)
    session.refresh(a_tx)
    assert a_tx.status == STATUS_DELETED


# --- transfers -----------------------------------------------------------------
def test_transfer_rejects_other_users_methods(session, alice, bob, setup):
    with pytest.raises(ValidationError):
        add_transfer(session, bob.id, 1_000, alice.cash, bob.bank, on=D)
    with pytest.raises(ValidationError):
        add_transfer(session, bob.id, 1_000, bob.cash, alice.bank, on=D)
    _alice_unchanged(session, alice)


def test_transfer_with_fee_only_affects_own_balances(session, alice, bob, setup):
    add_transfer(session, alice.id, 300_000, alice.cash, alice.bank, on=D, admin_fee=5_000)
    assert bal.get_total_balance(session, bob.id) == 450_000
    assert svc.list_transactions(session, bob.id, type_="transfer") == []


def test_delete_and_restore_transfer_with_fee_isolated_per_user(session, alice, bob, setup):
    """Operasi hapus dan pulihkan transfer berbiaya admin tidak bocor antar pengguna."""
    res = add_transfer(session, alice.id, 200_000, alice.cash, alice.bank, on=D, admin_fee=2_500)
    a_transfer = res.transfer
    a_fee = res.admin_fee

    # 1. Bob tidak bisa menghapus transfer atau biaya admin milik Alice
    with pytest.raises(NotFoundError):
        svc.delete_transaction(session, bob.id, a_transfer.id)
    with pytest.raises(NotFoundError):
        svc.delete_transaction(session, bob.id, a_fee.id)

    assert a_transfer.status == STATUS_ACTIVE
    assert a_fee.status == STATUS_ACTIVE
    assert bal.get_total_balance(session, bob.id) == 450_000

    # 2. Alice menghapus transfernya: hanya transaksi Alice yang terhapus
    svc.delete_transaction(session, alice.id, a_transfer.id)
    session.refresh(a_transfer)
    session.refresh(a_fee)
    assert a_transfer.status == STATUS_DELETED
    assert a_fee.status == STATUS_DELETED
    assert bal.get_total_balance(session, bob.id) == 450_000

    # 3. Bob tidak bisa memulihkan transfer atau biaya admin milik Alice
    with pytest.raises(NotFoundError):
        svc.restore_transaction(session, bob.id, a_transfer.id)
    with pytest.raises(NotFoundError):
        svc.restore_transaction(session, bob.id, a_fee.id)

    # 4. Alice memulihkan transfernya: hanya transaksi Alice yang dipulihkan
    svc.restore_transaction(session, alice.id, a_transfer.id)
    session.refresh(a_transfer)
    session.refresh(a_fee)
    assert a_transfer.status == STATUS_ACTIVE
    assert a_fee.status == STATUS_ACTIVE
    assert bal.get_total_balance(session, bob.id) == 450_000


# --- opening balances ----------------------------------------------------------
def test_set_opening_balance_on_other_users_method_rejected(session, alice, bob, setup):
    with pytest.raises(ValidationError):
        set_opening_balance(session, bob.id, alice.cash, 1, D)
    assert get_opening_balance(session, alice.id, alice.cash).amount == 1_000_000
    _alice_unchanged(session, alice)


def test_get_opening_balance_of_other_user_is_none(session, alice, bob, setup):
    assert get_opening_balance(session, bob.id, alice.cash) is None


def test_set_opening_balance_does_not_affect_other_user(session, alice, bob, setup):
    set_opening_balance(session, alice.id, alice.bank, 7_000_000, D)
    assert bal.get_total_balance(session, bob.id) == 450_000


# --- adjustments ---------------------------------------------------------------
def test_adjust_balance_on_other_users_method_rejected(session, alice, bob, setup):
    with pytest.raises(ValidationError):
        adjust_balance(session, bob.id, alice.cash, 0, on=D)
    _alice_unchanged(session, alice)


def test_adjust_balance_uses_only_own_transactions(session, alice, bob, setup):
    result = adjust_balance(session, bob.id, bob.cash, 450_000, on=D)
    assert result.previous_balance == 450_000
    assert result.transaction is None
    _alice_unchanged(session, alice)


# --- balances ------------------------------------------------------------------
def test_balance_services_are_per_user(session, alice, bob, setup):
    assert {b.method_id for b in bal.get_method_balances(session, bob.id)} == {
        bob.cash,
        bob.bank,
        bob.ewallet,
    }
    assert bal.get_total_balance(session, bob.id) == 450_000
    assert bal.get_total_balance(session, alice.id) == 900_000
    assert bal.get_method_balances(session, 9999) == []
    assert bal.get_total_balance(session, 9999) == 0


def test_get_method_balance_of_other_users_method_not_found(session, alice, bob, setup):
    with pytest.raises(NotFoundError):
        bal.get_method_balance(session, bob.id, alice.cash)


def test_check_balance_consistency_isolation(session, alice, bob, setup):
    alice_res = bal.check_balance_consistency(session, alice.id)
    bob_res = bal.check_balance_consistency(session, bob.id)
    assert all(r.is_consistent for r in alice_res)
    assert all(r.is_consistent for r in bob_res)
    assert {r.method_id for r in alice_res} == {alice.cash, alice.bank, alice.ewallet}
    assert {r.method_id for r in bob_res} == {bob.cash, bob.bank, bob.ewallet}


# --- pengguna tidak dikenal ----------------------------------------------------
@pytest.mark.parametrize(
    "call",
    [
        lambda s, a: svc.add_income(s, 9999, 1, a.salary, a.cash, on=D),
        lambda s, a: svc.add_expense(s, 9999, 1, a.food, a.cash, on=D),
        lambda s, a: add_transfer(s, 9999, 1, a.cash, a.bank, on=D),
        lambda s, a: set_opening_balance(s, 9999, a.cash, 1, D),
        lambda s, a: adjust_balance(s, 9999, a.cash, 1, on=D),
    ],
)
def test_writes_for_unknown_user_rejected(session, alice, setup, call):
    with pytest.raises(NotFoundError):
        call(session, alice)
    _alice_unchanged(session, alice)


def test_get_related_transactions_isolation(session, alice, bob):
    res = add_transfer(session, alice.id, 100_000, alice.cash, alice.bank, on=D, admin_fee=1_000)
    assert len(svc.get_related_transactions(session, alice.id, res.transfer.id)) == 1
    assert svc.get_related_transactions(session, bob.id, res.transfer.id) == []
    assert svc.get_related_transactions(session, 9999, res.transfer.id) == []


# --- categories ---------------------------------------------------------------
def test_categories_isolation(session, alice, bob):
    alice_cats = list_categories(session, alice.id)
    bob_cats = list_categories(session, bob.id)
    assert alice_cats
    assert bob_cats
    assert {c.id for c in alice_cats}.isdisjoint({c.id for c in bob_cats})
    assert get_category(session, bob.id, alice_cats[0].id) is None

    # add_category
    a_cat = add_category(session, alice.id, "Kripto", "income")
    assert a_cat.user_id == alice.id
    b_cat = add_category(session, bob.id, "Kripto", "income")
    assert b_cat.user_id == bob.id
    assert a_cat.id != b_cat.id

    # rename_category
    with pytest.raises(NotFoundError):
        rename_category(session, bob.id, a_cat.id, "Kripto Baru")
    renamed = rename_category(session, alice.id, a_cat.id, "Kripto Baru")
    assert renamed.name == "Kripto Baru"
    assert b_cat.name == "Kripto"

    # archive_category
    with pytest.raises(NotFoundError):
        archive_category(session, bob.id, a_cat.id)
    archived = archive_category(session, alice.id, a_cat.id)
    assert archived.is_archived is True

    # unarchive_category
    with pytest.raises(NotFoundError):
        unarchive_category(session, bob.id, a_cat.id)
    unarchived = unarchive_category(session, alice.id, a_cat.id)
    assert unarchived.is_archived is False

    # --- payment_methods ---
    alice_pms = list_payment_methods(session, alice.id)
    bob_pms = list_payment_methods(session, bob.id)
    assert alice_pms
    assert bob_pms
    assert {m.id for m in alice_pms}.isdisjoint({m.id for m in bob_pms})

    # add_payment_method
    a_pm = add_payment_method(session, alice.id, "Crypto Wallet")
    assert a_pm.user_id == alice.id
    b_pm = add_payment_method(session, bob.id, "Crypto Wallet")
    assert b_pm.user_id == bob.id
    assert a_pm.id != b_pm.id

    # rename_payment_method
    with pytest.raises(NotFoundError):
        rename_payment_method(session, bob.id, a_pm.id, "Crypto Baru")
    renamed_pm = rename_payment_method(session, alice.id, a_pm.id, "Crypto Baru")
    assert renamed_pm.name == "Crypto Baru"
    assert b_pm.name == "Crypto Wallet"

    # archive_payment_method
    with pytest.raises(NotFoundError):
        archive_payment_method(session, bob.id, a_pm.id)
    archived_pm = archive_payment_method(session, alice.id, a_pm.id)
    assert archived_pm.is_archived is True

    # unarchive_payment_method
    with pytest.raises(NotFoundError):
        unarchive_payment_method(session, bob.id, a_pm.id)
    unarchived_pm = unarchive_payment_method(session, alice.id, a_pm.id)
    assert unarchived_pm.is_archived is False


# --- budgets ------------------------------------------------------------------
def test_budgets_isolation(session, alice, bob):
    set_monthly_budget(session, alice.id, 2_000_000, "2026-10")
    assert get_monthly_budget(session, bob.id, "2026-10") is None
    bob_b = set_monthly_budget(session, bob.id, 1_500_000, "2026-10")
    alice_b = get_monthly_budget(session, alice.id, "2026-10")
    assert alice_b.amount == 2_000_000
    assert bob_b.amount == 1_500_000

    set_category_budget(session, alice.id, alice.food, 500_000, "2026-10")
    assert get_category_budget(session, bob.id, alice.food, "2026-10") is None
    assert len(list_budgets(session, alice.id, "2026-10")) == 2
    assert len(list_budgets(session, bob.id, "2026-10")) == 1

    a_eval = evaluate_budgets(session, alice.id, "2026-10")
    b_eval = evaluate_budgets(session, bob.id, "2026-10")
    assert a_eval.total_budget.amount == 2_000_000
    assert b_eval.total_budget.amount == 1_500_000
    assert check_budget_warnings(session, bob.id, "2026-10") == []

    # Weekly budget isolasi
    set_weekly_budget(session, alice.id, 600_000, on="07-10-2026")
    assert get_weekly_budget(session, bob.id, on="07-10-2026") is None
    set_weekly_budget(session, alice.id, 250_000, on="07-10-2026", category_id=alice.food)
    with pytest.raises(ValidationError):
        set_weekly_budget(session, bob.id, 200_000, on="07-10-2026", category_id=alice.food)
    assert len(list_weekly_budgets(session, alice.id, on="07-10-2026")) == 2
    assert len(list_weekly_budgets(session, bob.id, on="07-10-2026")) == 0
    a_weval = evaluate_weekly_budgets(session, alice.id, on="07-10-2026")
    b_weval = evaluate_weekly_budgets(session, bob.id, on="07-10-2026")
    assert a_weval.total_budget.amount == 600_000
    assert b_weval.total_budget is None

    # Rollover budget isolasi: rollover Alice dari 2026-10 ke 2026-11 tidak menyentuh Bob
    rolled_a = rollover_budgets(session, alice.id, from_month="2026-10", to_month="2026-11")
    assert len(rolled_a) == 2
    assert get_monthly_budget(session, bob.id, "2026-11") is None
    alice_nov = get_monthly_budget(session, alice.id, "2026-11")
    assert alice_nov is not None
    assert alice_nov.carryover_amount == 2_000_000


# --- dashboard ----------------------------------------------------------------
def test_dashboard_summary_isolation(session, alice, bob, setup):
    a_dash = get_dashboard_summary(session, alice.id, "2026-02")
    b_dash = get_dashboard_summary(session, bob.id, "2026-02")
    assert a_dash.monthly_expense == 100_000
    assert b_dash.monthly_expense == 50_000
    assert a_dash.total_balance == 900_000
    assert b_dash.total_balance == 450_000


def test_get_runway_metrics_isolation(session, alice, bob, setup):
    a_runway = get_runway_metrics(session, alice.id, as_of=D)
    b_runway = get_runway_metrics(session, bob.id, as_of=D)
    assert a_runway.total_expense == 100_000
    assert b_runway.total_expense == 50_000
    assert a_runway.total_balance == 900_000
    assert b_runway.total_balance == 450_000
    assert a_runway.daily_burn_rate == 100_000
    assert b_runway.daily_burn_rate == 50_000


def test_chart_data_isolation(session, alice, bob, setup):
    # 1. get_category_chart_data
    a_exp_chart = get_category_chart_data(
        session, alice.id, "expense", period="monthly", ref_date=D
    )
    b_exp_chart = get_category_chart_data(session, bob.id, "expense", period="monthly", ref_date=D)
    assert a_exp_chart["total"] == 100_000
    assert b_exp_chart["total"] == 50_000

    # 2. get_cash_flow_chart_data
    a_flow = get_cash_flow_chart_data(session, alice.id, period="monthly", ref_date=D)
    b_flow = get_cash_flow_chart_data(session, bob.id, period="monthly", ref_date=D)
    assert a_flow["total_expense"] == 100_000
    assert b_flow["total_expense"] == 50_000

    # 3. get_balance_trend_chart_data
    a_trend = get_balance_trend_chart_data(session, alice.id, period="monthly", ref_date=D)
    b_trend = get_balance_trend_chart_data(session, bob.id, period="monthly", ref_date=D)
    assert a_trend["current_balance"] == 900_000
    assert b_trend["current_balance"] == 450_000


# --- recurring ----------------------------------------------------------------
def test_recurring_isolation(session, alice, bob):
    # Bob tidak bisa membuat recurring dengan category / method milik Alice
    with pytest.raises(ValidationError):
        create_recurring(session, bob.id, "Kos", "expense", 1_000_000, alice.food, bob.cash)
    with pytest.raises(ValidationError):
        create_recurring(session, bob.id, "Kos", "expense", 1_000_000, bob.food, alice.cash)

    # Alice buat recurring
    a_rec = create_recurring(
        session,
        alice.id,
        "Kos Alice",
        "expense",
        1_500_000,
        alice.food,
        alice.cash,
        frequency="monthly",
        day_of_month=1,
        start_date="01-02-2026",
    )
    b_rec = create_recurring(
        session,
        bob.id,
        "Internet Bob",
        "expense",
        300_000,
        bob.food,
        bob.cash,
        frequency="monthly",
        day_of_month=5,
        start_date="01-02-2026",
    )

    # get_recurring isolasi
    assert get_recurring(session, bob.id, a_rec.id) is None
    assert get_recurring(session, alice.id, b_rec.id) is None
    assert get_recurring(session, alice.id, a_rec.id) is not None

    # list_recurring isolasi
    a_list = list_recurring(session, alice.id)
    b_list = list_recurring(session, bob.id)
    assert len(a_list) == 1 and a_list[0].id == a_rec.id
    assert len(b_list) == 1 and b_list[0].id == b_rec.id

    # get_next_due_date isolasi
    assert get_next_due_date(session, alice.id, a_rec.id) is not None
    with pytest.raises(NotFoundError):
        get_next_due_date(session, bob.id, a_rec.id)

    # update_recurring isolasi
    with pytest.raises(NotFoundError):
        update_recurring(session, bob.id, a_rec.id, name="Hack Kos")
    with pytest.raises(ValidationError):
        update_recurring(session, bob.id, b_rec.id, category_id=alice.food)

    # deactivate_recurring & activate_recurring isolasi
    with pytest.raises(NotFoundError):
        deactivate_recurring(session, bob.id, a_rec.id)
    deactivate_recurring(session, alice.id, a_rec.id)
    assert not get_recurring(session, alice.id, a_rec.id).is_active

    with pytest.raises(NotFoundError):
        activate_recurring(session, bob.id, a_rec.id)
    activate_recurring(session, alice.id, a_rec.id)
    assert get_recurring(session, alice.id, a_rec.id).is_active

    # apply_recurring isolasi
    alice.open(alice.cash, 2_000_000)
    bob.open(bob.cash, 1_000_000)
    session.commit()

    with pytest.raises(NotFoundError):
        apply_recurring(session, bob.id, a_rec.id, on="01-02-2026")

    res = apply_recurring(session, alice.id, a_rec.id, on="01-02-2026")
    assert res.transaction.amount == 1_500_000
    assert bal.get_total_balance(session, alice.id) == 500_000
    assert bal.get_total_balance(session, bob.id) == 1_000_000

    # process_due_recurring isolasi
    b_res = process_due_recurring(session, bob.id, as_of="10-02-2026")
    assert len(b_res) == 1
    assert bal.get_total_balance(session, bob.id) == 700_000
    assert bal.get_total_balance(session, alice.id) == 500_000

    # delete_recurring isolasi
    with pytest.raises(NotFoundError):
        delete_recurring(session, bob.id, a_rec.id)
    delete_recurring(session, alice.id, a_rec.id)
    assert get_recurring(session, alice.id, a_rec.id) is None


# --- backup & restore ---------------------------------------------------------
def test_backup_restore_isolation(session, alice, bob, setup, tmp_path):
    _a_tx, _b_tx = setup
    assert bal.get_total_balance(session, alice.id) == 900_000
    assert bal.get_total_balance(session, bob.id) == 450_000

    # 1. create_backup_data isolasi
    a_data = create_backup_data(session, alice.id)
    b_data = create_backup_data(session, bob.id)
    assert a_data["user_id"] == alice.id
    assert b_data["user_id"] == bob.id
    assert len(a_data["data"]["transactions"]) == 1
    assert a_data["data"]["transactions"][0]["note"] == "rahasia alice"
    # Bob's backup tidak mengandung transaksi Alice
    b_notes = [t.get("note") for t in b_data["data"]["transactions"]]
    assert "rahasia alice" not in b_notes

    # 2. export_backup isolasi
    a_file = tmp_path / "alice_backup.json"
    b_file = tmp_path / "bob_backup.json"
    export_backup(session, alice.id, file_path=a_file)
    export_backup(session, bob.id, file_path=b_file)
    assert a_file.exists() and b_file.exists()

    # 3. validate_backup_data isolasi
    # Alice dapat memvalidasi cadangannya
    assert validate_backup_data(session, alice.id, a_data)["version"] == "1.0"
    # Bob ditolak jika mencoba memvalidasi cadangan milik Alice
    with pytest.raises(ValidationError, match="bukan pengguna saat ini"):
        validate_backup_data(session, bob.id, a_data)

    # 4. get_backup_summary isolasi
    # Alice bisa membaca ringkasan cadangannya sendiri
    a_summary = get_backup_summary(session, alice.id, a_file)
    assert a_summary["user_id"] == alice.id
    # Bob ditolak saat mencoba membaca ringkasan cadangan Alice
    with pytest.raises(ValidationError, match="bukan pengguna saat ini"):
        get_backup_summary(session, bob.id, a_file)

    # 5. list_backups isolasi
    a_backups = list_backups(session, alice.id, directory=tmp_path)
    b_backups = list_backups(session, bob.id, directory=tmp_path)
    assert len(a_backups) == 1 and a_backups[0]["filename"] == "alice_backup.json"
    assert len(b_backups) == 1 and b_backups[0]["filename"] == "bob_backup.json"

    # 6. restore_backup isolasi
    # Bob ditolak saat mencoba memulihkan cadangan milik Alice
    with pytest.raises(ValidationError, match="bukan pengguna saat ini"):
        restore_backup(session, bob.id, a_file)
    # Saldo Bob tidak berubah
    assert bal.get_total_balance(session, bob.id) == 450_000

    # Tambah transaksi baru untuk Alice sebelum restore
    svc.add_expense(session, alice.id, 50_000, alice.food, alice.cash, on=D)
    session.commit()
    assert bal.get_total_balance(session, alice.id) == 850_000

    # Alice memulihkan cadangan lamanya (sebelum transaksi tambahan)
    restore_res = restore_backup(session, alice.id, a_file)
    session.commit()
    assert restore_res["transactions"] == 1
    # Saldo Alice kembali ke 900_000
    assert bal.get_total_balance(session, alice.id) == 900_000
    # Saldo Bob TETAP 450_000, transaksi Bob tetap utuh
    assert bal.get_total_balance(session, bob.id) == 450_000

    # 7. export_user_backup_zip isolasi
    a_zip = tmp_path / "alice_backup.zip"
    b_zip = tmp_path / "bob_backup.zip"
    export_user_backup_zip(session, alice.id, a_zip)
    export_user_backup_zip(session, bob.id, b_zip)
    assert a_zip.is_file() and b_zip.is_file()
    # Bob ditolak saat mencoba memulihkan ZIP milik Alice
    with pytest.raises(ValidationError, match="bukan pengguna saat ini"):
        restore_backup(session, bob.id, a_zip)

    # 8. export_full_admin_backup isolasi
    # Bob (bukan admin) ditolak
    with pytest.raises(ValidationError, match="Hanya admin"):
        export_full_admin_backup(session, bob.id)
    # Alice dijadikan admin, berhasil ekspor sistem
    alice_user = queries.get_user(session, alice.id)
    alice_user.role = "admin"
    session.flush()
    admin_zip = tmp_path / "admin_system.zip"
    export_full_admin_backup(session, alice.id, admin_zip)
    assert admin_zip.is_file()


# --- audit & undo --------------------------------------------------------------
def test_audit_isolation(session, alice, bob):
    # 1. record_audit_log
    a_log = record_audit_log(
        session,
        alice.id,
        "create",
        "transaction",
        1,
        description="Aksi rahasia Alice",
    )
    b_log = record_audit_log(
        session,
        bob.id,
        "create",
        "transaction",
        2,
        description="Aksi Bob",
    )
    session.commit()

    # 2. get_audit_log
    # Bob tidak bisa melihat log Alice
    with pytest.raises(NotFoundError):
        get_audit_log(session, bob.id, a_log.id)
    # Alice tidak bisa melihat log Bob
    with pytest.raises(NotFoundError):
        get_audit_log(session, alice.id, b_log.id)

    # 3. list_audit_logs
    a_logs = list_audit_logs(session, alice.id)
    b_logs = list_audit_logs(session, bob.id)
    assert all(log.user_id == alice.id for log in a_logs)
    assert all(log.user_id == bob.id for log in b_logs)

    # 4. undo_action isolasi
    # Alice buat transaksi asli
    alice.open(alice.cash, 100_000)
    svc.add_expense(session, alice.id, 20_000, alice.food, alice.cash, on=D)
    session.commit()
    # Dapatkan log transaksi Alice yang baru
    last_alice_log = list_audit_logs(session, alice.id, action="create", limit=1)[0]

    # Bob mencoba membatalkan (undo) aksi Alice -> NotFoundError
    with pytest.raises(NotFoundError):
        undo_action(session, bob.id, last_alice_log.id)

    # Saldo Alice tetap 80_000
    assert bal.get_total_balance(session, alice.id) == 80_000

    # 5. undo_last isolasi
    # Bob memanggil undo_last saat belum punya transaksi undoable
    # Hanya membatalkan log Bob sendiri, tidak menyentuh Alice
    undo_res = undo_action(session, alice.id, last_alice_log.id)
    assert undo_res.action_undone == "create"
    # Saldo Alice pulih ke 100_000
    assert bal.get_total_balance(session, alice.id) == 100_000

    # Tes undo_last pada Bob
    bob.open(bob.cash, 50_000)
    svc.add_expense(session, bob.id, 10_000, bob.food, bob.cash, on=D)
    session.commit()
    assert bal.get_total_balance(session, bob.id) == 40_000
    undo_last(session, bob.id)
    session.commit()
    # Saldo Bob pulih ke 50_000, saldo Alice tetap 100_000
    assert bal.get_total_balance(session, bob.id) == 50_000
    assert bal.get_total_balance(session, alice.id) == 100_000


def test_aliases_isolation(session, alice, bob):
    """Alice dan Bob memiliki alias masing-masing.

    Bob tidak bisa mengakses atau memodifikasi alias Alice.
    """
    # 1. seed_default_aliases isolasi: keduanya sudah memiliki alias masing-masing
    a_aliases = list_aliases(session, alice.id)
    b_aliases = list_aliases(session, bob.id)
    assert len(a_aliases) > 0
    assert len(b_aliases) > 0
    assert {a.id for a in a_aliases}.isdisjoint({b.id for b in b_aliases})

    # Pengguna baru tanpa seed alias
    carol = User(name="Carol")
    session.add(carol)
    session.flush()
    assert len(list_aliases(session, carol.id)) == 0
    seeded = seed_default_aliases(session, carol.id)
    assert seeded == 0  # Carol belum punya kategori/metode

    # 2. add_alias isolasi: Bob tidak bisa membuat alias yang mengarah ke kategori Alice
    with pytest.raises(NotFoundError):
        add_alias(session, bob.id, "makanan_alice", "category", alice.food)

    # Alice buat alias kustom
    qa_alice = add_alias(session, alice.id, "warteg", "category", alice.food)

    # 3. get_alias isolasi: Bob tidak bisa melihat alias Alice
    with pytest.raises(NotFoundError):
        get_alias(session, bob.id, qa_alice.id)

    # 4. delete_alias isolasi: Bob tidak bisa menghapus alias Alice
    with pytest.raises(NotFoundError):
        delete_alias(session, bob.id, qa_alice.id)

    # 5. parse_quick_input isolasi: Bob tidak bisa menggunakan alias Alice
    with pytest.raises(ValidationError):
        parse_quick_input(session, bob.id, "warteg 20000")

    # Tapi Alice bisa menggunakan alias miliknya sendiri
    parsed = parse_quick_input(session, alice.id, "warteg 20000")
    assert parsed.amount == 20000
    assert parsed.category_id == alice.food

    # Alice bisa menghapus alias miliknya
    assert delete_alias(session, alice.id, qa_alice.id) is True


def test_reminders_isolation(session, alice, bob):
    """Alice dan Bob memiliki pengaturan dan ringkasan pengingat yang terisolasi."""
    # 1. get_reminder_settings isolasi
    a_set = get_reminder_settings(session, alice.id)
    b_set = get_reminder_settings(session, bob.id)
    assert a_set.user_id == alice.id
    assert b_set.user_id == bob.id
    assert a_set.id != b_set.id

    # 2. update_reminder_settings isolasi: perubahan pada Alice tidak memengaruhi Bob
    update_reminder_settings(session, alice.id, daily_time="21:30", is_daily_active=False)
    session.refresh(b_set)
    assert b_set.daily_time == "20:00"
    assert b_set.is_daily_active is True

    # 3. mark_reminder_sent isolasi
    mark_reminder_sent(session, alice.id, "daily", D)
    session.refresh(b_set)
    assert b_set.last_daily_sent is None

    # 4. build_daily_reminder isolasi: Alice punya transaksi, Bob tidak
    svc.add_expense(session, alice.id, 50_000, alice.food, alice.cash, on=D)
    a_daily = build_daily_reminder(session, alice.id, as_of=D)
    b_daily = build_daily_reminder(session, bob.id, as_of=D)
    assert a_daily.user_id == alice.id
    assert b_daily.user_id == bob.id
    assert a_daily.has_transactions is True
    assert b_daily.has_transactions is False
    assert a_daily.total_expense == 50_000
    assert b_daily.total_expense == 0

    # 5. build_weekly_summary & build_monthly_summary isolasi
    a_week = build_weekly_summary(session, alice.id, as_of=D)
    b_week = build_weekly_summary(session, bob.id, as_of=D)
    assert a_week.user_id == alice.id
    assert b_week.user_id == bob.id
    assert a_week.total_expense == 50_000
    assert b_week.total_expense == 0

    a_month = build_monthly_summary(session, alice.id, as_of=D)
    b_month = build_monthly_summary(session, bob.id, as_of=D)
    assert a_month.user_id == alice.id
    assert b_month.user_id == bob.id
    assert a_month.total_expense == 50_000
    assert b_month.total_expense == 0

    # 6. get_user_settings, update_user_settings, set_user_timezone isolasi (V3)
    a_us = get_user_settings(session, alice.id)
    b_us = get_user_settings(session, bob.id)
    assert a_us.user_id == alice.id
    assert b_us.user_id == bob.id
    assert a_us.id != b_us.id

    set_user_timezone(session, alice.id, "WIT")
    session.refresh(b_us)
    assert get_user_settings(session, alice.id).timezone == "Asia/Jayapura"
    assert b_us.timezone == "Asia/Jakarta"

    update_user_settings(session, alice.id, is_recurring_active=False, daily_time="19:00")
    session.refresh(b_us)
    assert b_us.is_recurring_active is True
    assert b_us.daily_time == "20:00"

    # 7. has_sent_notification & record_sent_notification isolasi (V3)
    # Alice sudah ditandai terkirim untuk D (2026-02-01) pada langkah 3, Bob belum
    assert has_sent_notification(session, alice.id, "daily", "2026-02-01") is True
    assert has_sent_notification(session, bob.id, "daily", "2026-02-01") is False
    assert has_sent_notification(session, alice.id, "daily", "2026-02-05") is False
    record_sent_notification(session, alice.id, "daily", "2026-02-05")
    assert has_sent_notification(session, alice.id, "daily", "2026-02-05") is True
    assert has_sent_notification(session, bob.id, "daily", "2026-02-05") is False

    # 8. build_recurring_due_notification & collect_due_notifications isolasi (V3)
    create_recurring(
        session,
        alice.id,
        "Kos Bulanan Alice",
        "expense",
        1_200_000,
        alice.food,
        alice.cash,
        frequency="monthly",
        day_of_month=1,
        start_date=D,
    )
    a_rec_notif = build_recurring_due_notification(session, alice.id, as_of=D)
    b_rec_notif = build_recurring_due_notification(session, bob.id, as_of=D)
    assert a_rec_notif is not None
    assert "Kos Bulanan Alice" in a_rec_notif.message
    assert b_rec_notif.has_due is False

    update_user_settings(session, alice.id, is_daily_active=True, is_recurring_active=True)
    # Alice sudah terkirim daily "2026-02-01", jadi job_type="daily" untuk Alice kosong,
    # sedangkan Bob mengembalikan daily miliknya.
    a_daily_due = collect_due_notifications(session, alice.id, as_of=D, job_type="daily")
    b_daily_due = collect_due_notifications(session, bob.id, as_of=D, job_type="daily")
    assert a_daily_due == []
    assert [n.notification_type for n in b_daily_due] == ["daily"]

    # Untuk job_type="recurring", Alice mengembalikan recurring due sedangkan Bob kosong
    a_due = collect_due_notifications(session, alice.id, as_of=D, job_type="recurring")
    b_due = collect_due_notifications(session, bob.id, as_of=D, job_type="recurring")
    assert [n.notification_type for n in a_due] == ["recurring"]
    assert b_due == []
    assert all("Kos Bulanan Alice" not in n.message for n in b_due)

    # 9. collect_predictive_notifications isolasi (V4)
    a_pred = collect_predictive_notifications(session, alice.id, as_of=D)
    b_pred = collect_predictive_notifications(session, bob.id, as_of=D)
    assert all(p.user_id == alice.id for p in a_pred)
    assert all(p.user_id == bob.id for p in b_pred)


def test_export_isolation(session, alice, bob, setup, tmp_path):
    """Ekspor CSV, Excel, dan PDF hanya menyertakan data milik pengguna masing-masing."""
    # 1. get_export_data isolasi
    a_data = get_export_data(session, alice.id)
    b_data = get_export_data(session, bob.id)
    assert a_data.user_id == alice.id
    assert b_data.user_id == bob.id
    assert a_data.total_expense == 100_000
    assert b_data.total_expense == 50_000
    assert any("rahasia alice" in r.note for r in a_data.transactions)
    assert all("rahasia alice" not in r.note for r in b_data.transactions)

    # Filter Bob dengan kategori/metode Alice menghasilkan kosong (tidak bocor)
    b_filtered = get_export_data(session, bob.id, category_id=alice.food)
    assert b_filtered.transactions == []

    # 2. export_csv isolasi
    b_csv = export_csv(session, bob.id, file_path=tmp_path / "bob.csv")
    csv_text = b_csv.read_text(encoding="utf-8")
    assert "rahasia alice" not in csv_text
    assert "50000" in csv_text

    # 3. export_excel isolasi
    b_xlsx = export_excel(session, bob.id, file_path=tmp_path / "bob.xlsx")
    assert b_xlsx.exists()

    # 4. export_pdf isolasi
    b_pdf = export_pdf(session, bob.id, file_path=tmp_path / "bob.pdf")
    assert b_pdf.exists()


# --- V3: users, invites, & guessed-ID enumeration -----------------------------
def test_users_and_invites_service_isolation(session, alice, bob):
    """Non-admin ditolak dari perintah admin; daftar undangan diisolasi per admin."""
    from database import queries
    from models import (
        ROLE_ADMIN,
        USER_STATUS_ACTIVE,
        USER_STATUS_BLOCKED,
        USER_STATUS_PENDING,
    )

    # Jadikan Alice sebagai admin, Bob tetap user biasa
    alice_user = queries.get_user(session, alice.id)
    alice_user.role = ROLE_ADMIN
    session.flush()

    # 1. Bob (bukan admin) tidak bisa membuat undangan, melihat undangan,
    #    melihat daftar pengguna, menyetujui, atau memblokir siapa pun
    with pytest.raises(ValidationError, match="admin"):
        create_invite(session, bob.id)
    with pytest.raises(ValidationError, match="admin"):
        list_invites(session, bob.id)
    with pytest.raises(ValidationError, match="admin"):
        list_users(session, bob.id)
    with pytest.raises(ValidationError, match="admin"):
        approve_user(session, bob.id, alice.id)
    with pytest.raises(ValidationError, match="admin"):
        block_user(session, bob.id, alice.id)

    # 2. Dua admin memiliki daftar undangan yang terisolasi berdasarkan user_id
    admin2 = User(name="Admin Dua", role=ROLE_ADMIN, status=USER_STATUS_ACTIVE)
    session.add(admin2)
    session.flush()

    inv_alice = create_invite(session, alice.id, code="ALICE-INV-1")
    inv_admin2 = create_invite(session, admin2.id, code="ADMIN2-INV-1")

    alice_invites = list_invites(session, alice.id)
    admin2_invites = list_invites(session, admin2.id)
    assert [i.code for i in alice_invites] == [inv_alice.code]
    assert [i.code for i in admin2_invites] == [inv_admin2.code]

    # 3. Pengguna pending diaktifkan lewat redeem_invite & otomatis punya data default
    pending_u1 = User(telegram_id=700001, name="Calon Satu", status=USER_STATUS_PENDING)
    pending_u2 = User(telegram_id=700002, name="Calon Dua", status=USER_STATUS_PENDING)
    session.add_all([pending_u1, pending_u2])
    session.flush()

    # Sebelum aktif, pengguna pending ditolak dari seluruh service
    with pytest.raises(ValidationError, match="belum aktif"):
        get_export_data(session, pending_u1.id)
    with pytest.raises(ValidationError, match="belum aktif"):
        create_backup_data(session, pending_u1.id)

    activated_u1 = redeem_invite(session, pending_u1.id, "ALICE-INV-1")
    assert activated_u1.status == USER_STATUS_ACTIVE
    assert len(list_categories(session, pending_u1.id)) == 17  # 10 expense + 7 income
    assert len(bal.get_method_balances(session, pending_u1.id)) == 3

    # Kode undangan yang sudah terpakai tidak bisa dipakai ulang oleh pengguna lain
    with pytest.raises(ValidationError, match="sudah pernah digunakan"):
        redeem_invite(session, pending_u2.id, "ALICE-INV-1")

    # 4. Persetujuan langsung oleh admin (approve_user) juga membuat data default
    approved_u2 = approve_user(session, alice.id, pending_u2.telegram_id)
    assert approved_u2.id == pending_u2.id
    assert approved_u2.status == USER_STATUS_ACTIVE
    assert len(bal.get_method_balances(session, pending_u2.id)) == 3

    # 5. Pemblokiran oleh admin (block_user) langsung memutus akses ke seluruh service
    blocked_u2 = block_user(session, alice.id, pending_u2.id)
    assert blocked_u2.status == USER_STATUS_BLOCKED
    with pytest.raises(ValidationError, match="diblokir"):
        svc.add_expense(session, pending_u2.id, 10_000, alice.food, alice.cash, on=D)
    with pytest.raises(ValidationError, match="diblokir"):
        get_export_data(session, pending_u2.id)
    with pytest.raises(ValidationError, match="diblokir"):
        create_backup_data(session, pending_u2.id)

    # Admin tidak bisa memblokir dirinya sendiri
    with pytest.raises(ValidationError, match="sendiri"):
        block_user(session, alice.id, alice.id)

    # list_users dapat difilter berdasarkan status
    blocked_list = list_users(session, alice.id, status=USER_STATUS_BLOCKED)
    assert [u.id for u in blocked_list] == [pending_u2.id]

    # 6. delete_user_account isolasi: menghapus pengguna tidak menghapus data pengguna lain
    delete_user_account(session, pending_u2.id)
    assert queries.get_user(session, pending_u2.id) is None
    assert queries.get_user(session, alice.id) is not None
    assert queries.get_user(session, bob.id) is not None


def test_guessed_id_enumeration_across_all_entities(session, alice, bob, setup, tmp_path):
    """Pengguna B menebak ID berurutan untuk membaca/mengubah/menghapus/mengekspor data A."""
    a_tx, b_tx = setup
    a_trf_res = add_transfer(
        session, alice.id, 150_000, alice.cash, alice.bank, on=D, admin_fee=2_500
    )
    a_rec = create_recurring(
        session,
        alice.id,
        "Langganan Rahasia Alice",
        "expense",
        99_000,
        alice.food,
        alice.cash,
        day_of_month=1,
        start_date=D,
    )
    a_alias = add_alias(session, alice.id, "kode_alice", "category", alice.food)
    a_log = list_audit_logs(session, alice.id, limit=1)[0]
    session.flush()

    alice_balance_before = bal.get_total_balance(session, alice.id)

    # Bob mencoba menebak ID transaksi, transfer, biaya admin, dan angka tebakan 1..30
    guessed_ids = sorted(
        set(range(1, 35))
        | {
            a_tx.id,
            a_trf_res.transfer.id,
            a_trf_res.admin_fee.id,
            alice.food,
            alice.salary,
            alice.cash,
            alice.bank,
            a_rec.id,
            a_alias.id,
            a_log.id,
        }
    )

    for gid in guessed_ids:
        if gid != b_tx.id:
            with pytest.raises(NotFoundError):
                svc.get_transaction(session, bob.id, gid, include_deleted=True)
            with pytest.raises(NotFoundError):
                svc.edit_transaction(session, bob.id, gid, amount=1, note="dibajak")
            with pytest.raises(NotFoundError):
                svc.delete_transaction(session, bob.id, gid)
            with pytest.raises(NotFoundError):
                svc.restore_transaction(session, bob.id, gid)

        # Tebak recurring ID milik Alice
        assert get_recurring(session, bob.id, a_rec.id) is None
        with pytest.raises(NotFoundError):
            update_recurring(session, bob.id, a_rec.id, amount=1)
        with pytest.raises(NotFoundError):
            delete_recurring(session, bob.id, a_rec.id)

        # Tebak alias ID milik Alice
        with pytest.raises(NotFoundError):
            get_alias(session, bob.id, a_alias.id)
        with pytest.raises(NotFoundError):
            delete_alias(session, bob.id, a_alias.id)

        # Tebak audit log ID milik Alice
        with pytest.raises(NotFoundError):
            get_audit_log(session, bob.id, a_log.id)
        with pytest.raises(NotFoundError):
            undo_action(session, bob.id, a_log.id)

    # Tebak category_id dan method_id Alice saat ekspor oleh Bob
    exp_by_alice_cat = get_export_data(session, bob.id, category_id=alice.food)
    exp_by_alice_met = get_export_data(session, bob.id, method_id=alice.cash)
    assert exp_by_alice_cat.transactions == []
    assert exp_by_alice_met.transactions == []

    assert bal.get_total_balance(session, alice.id) == alice_balance_before


def test_anomaly_service_isolation(session, alice, bob, setup):
    """Fungsi deteksi pengeluaran anomali terisolasi ketat per pengguna."""
    from services.anomalies import check_expense_anomaly, list_anomalous_transactions

    # Buat beberapa transaksi pengeluaran untuk Alice (rata-rata 20.000)
    for amt in (20_000, 22_000, 21_000, 19_000, 20_000):
        svc.add_expense(session, alice.id, amt, alice.food, alice.cash, on=D)

    # Bob punya pengeluaran besar rutin (rata-rata 1.000.000)
    for amt in (1_000_000, 1_100_000, 950_000, 1_050_000):
        svc.add_expense(session, bob.id, amt, bob.food, bob.cash, on=D)

    # Pengeluaran Rp 300.000 adalah anomali bagi Alice (karena biasanya ~20.000)
    res_alice = check_expense_anomaly(session, alice.id, 300_000, category_id=alice.food)
    assert res_alice.is_anomaly is True

    # Pengeluaran Rp 300.000 adalah wajar / normal bagi Bob (karena biasanya ~1.000.000)
    res_bob = check_expense_anomaly(session, bob.id, 300_000, category_id=bob.food)
    assert res_bob.is_anomaly is False

    # Tambah transaksi ekstrem untuk Alice
    svc.add_expense(
        session, alice.id, 5_000_000, alice.food, alice.cash, on=D, note="anomali alice"
    )

    # list_anomalous_transactions milik Bob tidak boleh melihat anomali milik Alice
    bob_anomalies = list_anomalous_transactions(session, bob.id)
    assert all("anomali alice" not in (it.transaction.note or "") for it in bob_anomalies)

    alice_anomalies = list_anomalous_transactions(session, alice.id)
    assert any("anomali alice" in (it.transaction.note or "") for it in alice_anomalies)
