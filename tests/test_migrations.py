"""Tes migrasi Alembic pada database file sementara."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from database.engine import create_db_engine
from database.migrate import make_alembic_config, upgrade_to_head
from models import Base

EXPECTED_TABLES = {
    "users",
    "payment_methods",
    "categories",
    "transactions",
    "opening_balances",
    "budgets",
}


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite:///{(tmp_path / 'migrated.db').as_posix()}"


@pytest.fixture
def migrated_engine(db_url):
    upgrade_to_head(db_url)
    engine = create_db_engine(db_url)
    yield engine
    engine.dispose()


def test_upgrade_creates_all_tables(migrated_engine):
    tables = set(inspect(migrated_engine).get_table_names())
    assert tables >= EXPECTED_TABLES
    assert "alembic_version" in tables


def test_upgrade_records_version(migrated_engine):
    with migrated_engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0002"


def test_upgrade_is_repeatable(db_url):
    upgrade_to_head(db_url)
    upgrade_to_head(db_url)  # tidak boleh error


def test_migrations_match_models(migrated_engine):
    """Jika model diubah tanpa membuat migrasi baru, tes ini gagal."""
    with migrated_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_migration_0002_upgrade_downgrade(db_url):
    """Tes migrasi naik dan turun untuk revisi 0002 (kolom related_transaction_id)."""
    cfg = make_alembic_config(db_url)

    # 1. Migrasi ke 0001: kolom related_transaction_id belum ada
    command.upgrade(cfg, "0001")
    engine = create_db_engine(db_url)
    try:
        insp = inspect(engine)
        col_names_0001 = {c["name"] for c in insp.get_columns("transactions")}
        assert "related_transaction_id" not in col_names_0001
    finally:
        engine.dispose()

    # 2. Migrasi naik ke 0002: kolom dan FK related_transaction_id ditambahkan
    command.upgrade(cfg, "0002")
    engine = create_db_engine(db_url)
    try:
        insp = inspect(engine)
        col_names_0002 = {c["name"] for c in insp.get_columns("transactions")}
        assert "related_transaction_id" in col_names_0002

        # Uji penegakan FK pada database hasil migrasi
        with engine.connect() as conn:
            conn.execute(text("INSERT INTO users (name, created_at) VALUES ('U', '2026-01-01')"))
            conn.execute(
                text("INSERT INTO payment_methods (id, user_id, name) VALUES (1, 1, 'Cash')")
            )
            conn.execute(
                text("INSERT INTO payment_methods (id, user_id, name) VALUES (2, 1, 'Bank')")
            )
            conn.execute(
                text(
                    "INSERT INTO transactions "
                    "(id, user_id, type, amount, date, method_id, to_method_id, "
                    "created_at, updated_at) "
                    "VALUES (10, 1, 'transfer', 50000, '2026-02-01', 1, 2, "
                    "'2026-02-01', '2026-02-01')"
                )
            )
            # FK valid -> sukses
            conn.execute(
                text(
                    "INSERT INTO transactions "
                    "(id, user_id, type, amount, date, method_id, "
                    "related_transaction_id, created_at, updated_at) "
                    "VALUES (11, 1, 'expense', 2500, '2026-02-01', 1, 10, "
                    "'2026-02-01', '2026-02-01')"
                )
            )
            conn.commit()
            # FK invalid -> ditolak oleh constraint
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO transactions "
                        "(id, user_id, type, amount, date, method_id, "
                        "related_transaction_id, created_at, updated_at) "
                        "VALUES (12, 1, 'expense', 2500, '2026-02-01', 1, 9999, "
                        "'2026-02-01', '2026-02-01')"
                    )
                )
    finally:
        engine.dispose()

    # 3. Migrasi turun ke 0001: kolom related_transaction_id dihapus kembali
    command.downgrade(cfg, "0001")
    engine = create_db_engine(db_url)
    try:
        insp = inspect(engine)
        col_names_down = {c["name"] for c in insp.get_columns("transactions")}
        assert "related_transaction_id" not in col_names_down
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0001"
    finally:
        engine.dispose()

    # 4. Migrasi naik kembali ke head
    command.upgrade(cfg, "head")
    engine = create_db_engine(db_url)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0002"
    finally:
        engine.dispose()


def test_check_constraints_exist_in_migrated_database(migrated_engine):
    """Alembic tidak membandingkan CHECK, jadi diuji langsung pada DB hasil migrasi."""
    with migrated_engine.connect() as conn:
        conn.execute(text("INSERT INTO users (name, created_at) VALUES ('A', '2026-01-01')"))
        conn.execute(text("INSERT INTO payment_methods (user_id, name) VALUES (1, 'Cash')"))
        conn.commit()
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO transactions "
                    "(user_id, type, amount, date, method_id, created_at, updated_at) "
                    "VALUES (1, 'income', 0, '2026-02-01', 1, '2026-02-01', '2026-02-01')"
                )
            )


def test_foreign_keys_enforced_in_migrated_database(migrated_engine):
    with migrated_engine.connect() as conn, pytest.raises(IntegrityError):
        conn.execute(text("INSERT INTO payment_methods (user_id, name) VALUES (999, 'X')"))


def test_downgrade_removes_tables(migrated_engine, db_url):
    command.downgrade(make_alembic_config(db_url), "base")
    tables = set(inspect(migrated_engine).get_table_names())
    assert not (EXPECTED_TABLES & tables)
