"""Tes service kategori kustom: tambah, ubah nama, arsipkan, dan pulihkan."""

import pytest

from database.seed import ADMIN_FEE_CATEGORY
from models import TYPE_EXPENSE, TYPE_INCOME
from services import categories as cat_svc
from services import transactions as tx_svc
from tests.services_layer.conftest import D
from utils.errors import NotFoundError, ValidationError


# --- 1. Tambah Kategori (add_category) -----------------------------------------
def test_add_category_income_and_expense(session, alice):
    inc = cat_svc.add_category(session, alice.id, "Dividen Saham", TYPE_INCOME)
    assert inc.id is not None
    assert inc.name == "Dividen Saham"
    assert inc.type == TYPE_INCOME
    assert inc.is_archived is False
    assert inc.is_system is False

    exp = cat_svc.add_category(session, alice.id, "Kopi & Kafe", TYPE_EXPENSE)
    assert exp.id is not None
    assert exp.name == "Kopi & Kafe"
    assert exp.type == TYPE_EXPENSE
    assert exp.is_archived is False
    assert exp.is_system is False


def test_add_category_strips_whitespace(session, alice):
    cat = cat_svc.add_category(session, alice.id, "   Donasi Sosial   ", TYPE_EXPENSE)
    assert cat.name == "Donasi Sosial"


@pytest.mark.parametrize("bad_name", ["", "   ", None])
def test_add_category_rejects_empty_name(session, alice, bad_name):
    with pytest.raises(ValidationError):
        cat_svc.add_category(session, alice.id, bad_name, TYPE_EXPENSE)


def test_add_category_rejects_name_too_long(session, alice):
    with pytest.raises(ValidationError):
        cat_svc.add_category(session, alice.id, "A" * 51, TYPE_EXPENSE)


@pytest.mark.parametrize("bad_type", ["transfer", "adjustment", "random", "", None])
def test_add_category_rejects_invalid_type(session, alice, bad_type):
    with pytest.raises(ValidationError):
        cat_svc.add_category(session, alice.id, "Kategori Baru", bad_type)


def test_add_category_rejects_duplicate_active_name(session, alice):
    cat_svc.add_category(session, alice.id, "Hobi", TYPE_EXPENSE)
    with pytest.raises(ValidationError, match="sudah ada"):
        cat_svc.add_category(session, alice.id, "Hobi", TYPE_EXPENSE)


def test_add_category_rejects_duplicate_case_insensitive(session, alice):
    cat_svc.add_category(session, alice.id, "Peliharaan", TYPE_EXPENSE)
    with pytest.raises(ValidationError, match="sudah ada"):
        cat_svc.add_category(session, alice.id, "peliharaan", TYPE_EXPENSE)


def test_add_category_allows_same_name_different_type(session, alice):
    # Nama sama boleh untuk tipe berbeda (mis. Investasi pengeluaran dan pemasukan)
    exp = cat_svc.add_category(session, alice.id, "Investasi", TYPE_EXPENSE)
    inc = cat_svc.add_category(session, alice.id, "Investasi", TYPE_INCOME)
    assert exp.id != inc.id
    assert exp.type == TYPE_EXPENSE
    assert inc.type == TYPE_INCOME


def test_add_category_rejects_if_archived_duplicate_exists(session, alice):
    cat = cat_svc.add_category(session, alice.id, "Langganan", TYPE_EXPENSE)
    cat_svc.archive_category(session, alice.id, cat.id)

    with pytest.raises(ValidationError, match="arsip"):
        cat_svc.add_category(session, alice.id, "Langganan", TYPE_EXPENSE)


# --- 2. Ubah Nama Kategori (rename_category) -----------------------------------
def test_rename_category_success(session, alice):
    cat = cat_svc.add_category(session, alice.id, "Nongkrong", TYPE_EXPENSE)
    renamed = cat_svc.rename_category(session, alice.id, cat.id, "Kafe & Resto")
    assert renamed.id == cat.id
    assert renamed.name == "Kafe & Resto"
    assert cat.name == "Kafe & Resto"


def test_rename_category_strips_whitespace(session, alice):
    cat = cat_svc.add_category(session, alice.id, "Gadget", TYPE_EXPENSE)
    renamed = cat_svc.rename_category(session, alice.id, cat.id, "  Elektronik  ")
    assert renamed.name == "Elektronik"


@pytest.mark.parametrize("bad_name", ["", "   ", None])
def test_rename_category_rejects_empty_name(session, alice, bad_name):
    cat = cat_svc.add_category(session, alice.id, "Elektronik", TYPE_EXPENSE)
    with pytest.raises(ValidationError):
        cat_svc.rename_category(session, alice.id, cat.id, bad_name)


def test_rename_category_rejects_too_long_name(session, alice):
    cat = cat_svc.add_category(session, alice.id, "Elektronik", TYPE_EXPENSE)
    with pytest.raises(ValidationError):
        cat_svc.rename_category(session, alice.id, cat.id, "B" * 51)


def test_rename_category_same_name_is_noop(session, alice):
    cat = cat_svc.add_category(session, alice.id, "Elektronik", TYPE_EXPENSE)
    result = cat_svc.rename_category(session, alice.id, cat.id, "Elektronik")
    assert result.name == "Elektronik"


def test_rename_category_rejects_duplicate_to_another_category(session, alice):
    cat1 = cat_svc.add_category(session, alice.id, "Pakaian", TYPE_EXPENSE)
    cat_svc.add_category(session, alice.id, "Sepatu", TYPE_EXPENSE)

    with pytest.raises(ValidationError, match="sudah ada"):
        cat_svc.rename_category(session, alice.id, cat1.id, "Sepatu")


def test_rename_category_rejects_system_category(session, alice):
    admin_cat = alice.expense[ADMIN_FEE_CATEGORY]
    with pytest.raises(ValidationError, match="sistem"):
        cat_svc.rename_category(session, alice.id, admin_cat.id, "Biaya Transfer")


def test_rename_category_not_found(session, alice):
    with pytest.raises(NotFoundError):
        cat_svc.rename_category(session, alice.id, 99999, "Nama Baru")


# --- 3. Arsipkan Kategori (archive_category) -----------------------------------
def test_archive_category_success(session, alice):
    cat = cat_svc.add_category(session, alice.id, "Gym", TYPE_EXPENSE)
    archived = cat_svc.archive_category(session, alice.id, cat.id)
    assert archived.is_archived is True

    # Default list_categories tidak lagi menampilkannya
    active_names = [c.name for c in cat_svc.list_categories(session, alice.id, type_=TYPE_EXPENSE)]
    assert "Gym" not in active_names

    # Termasuk jika include_archived=True
    all_names = [
        c.name
        for c in cat_svc.list_categories(
            session, alice.id, type_=TYPE_EXPENSE, include_archived=True
        )
    ]
    assert "Gym" in all_names


def test_archive_category_rejects_system_category(session, alice):
    admin_cat = alice.expense[ADMIN_FEE_CATEGORY]
    with pytest.raises(ValidationError, match="sistem"):
        cat_svc.archive_category(session, alice.id, admin_cat.id)


def test_archive_category_rejects_already_archived(session, alice):
    cat = cat_svc.add_category(session, alice.id, "Gym", TYPE_EXPENSE)
    cat_svc.archive_category(session, alice.id, cat.id)
    with pytest.raises(ValidationError, match="sudah dalam keadaan diarsipkan"):
        cat_svc.archive_category(session, alice.id, cat.id)


def test_archive_category_not_found(session, alice):
    with pytest.raises(NotFoundError):
        cat_svc.archive_category(session, alice.id, 99999)


def test_archived_category_cannot_be_used_in_new_transactions(session, alice):
    alice.open(alice.cash, 100_000)
    cat = cat_svc.add_category(session, alice.id, "Kamera", TYPE_EXPENSE)
    cat_svc.archive_category(session, alice.id, cat.id)

    with pytest.raises(ValidationError, match="sudah diarsipkan"):
        tx_svc.add_expense(session, alice.id, 20_000, cat.id, alice.cash, on=D)


# --- 4. Pulihkan Kategori dari Arsip (unarchive_category) -----------------------
def test_unarchive_category_success(session, alice):
    cat = cat_svc.add_category(session, alice.id, "Buku", TYPE_EXPENSE)
    cat_svc.archive_category(session, alice.id, cat.id)
    assert cat.is_archived is True

    unarchived = cat_svc.unarchive_category(session, alice.id, cat.id)
    assert unarchived.is_archived is False

    active_names = [c.name for c in cat_svc.list_categories(session, alice.id, type_=TYPE_EXPENSE)]
    assert "Buku" in active_names


def test_unarchive_category_rejects_active_category(session, alice):
    cat = cat_svc.add_category(session, alice.id, "Buku", TYPE_EXPENSE)
    with pytest.raises(ValidationError, match="tidak dalam keadaan diarsipkan"):
        cat_svc.unarchive_category(session, alice.id, cat.id)


def test_unarchive_category_rejects_system_category(session, alice):
    admin_cat = alice.expense[ADMIN_FEE_CATEGORY]
    with pytest.raises(ValidationError):
        cat_svc.unarchive_category(session, alice.id, admin_cat.id)


def test_unarchive_category_not_found(session, alice):
    with pytest.raises(NotFoundError):
        cat_svc.unarchive_category(session, alice.id, 99999)
