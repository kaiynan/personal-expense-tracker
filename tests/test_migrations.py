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
    "weekly_budgets",
    "recurring",
    "audit_log",
    "quick_aliases",
    "reminder_settings",
    "invites",
    "user_settings",
    "sent_notifications",
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
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0010"


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
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0010"
    finally:
        engine.dispose()


def test_migration_0003_upgrade_downgrade(db_url):
    """Tes migrasi naik dan turun untuk revisi 0003 (tabel recurring)."""
    cfg = make_alembic_config(db_url)

    # 1. Migrasi ke 0002: tabel recurring belum ada
    command.upgrade(cfg, "0002")
    engine = create_db_engine(db_url)
    try:
        insp = inspect(engine)
        assert "recurring" not in set(insp.get_table_names())
    finally:
        engine.dispose()

    # 2. Migrasi naik ke 0003: tabel recurring dibuat
    command.upgrade(cfg, "0003")
    engine = create_db_engine(db_url)
    try:
        insp = inspect(engine)
        assert "recurring" in set(insp.get_table_names())
        cols = {c["name"] for c in insp.get_columns("recurring")}
        assert {
            "id",
            "user_id",
            "name",
            "type",
            "amount",
            "frequency",
            "day_of_month",
            "is_active",
        } <= cols
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0003"
    finally:
        engine.dispose()

    # 3. Migrasi turun ke 0002: tabel recurring dihapus
    command.downgrade(cfg, "0002")
    engine = create_db_engine(db_url)
    try:
        insp = inspect(engine)
        assert "recurring" not in set(insp.get_table_names())
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0002"
    finally:
        engine.dispose()

    # 4. Migrasi naik kembali ke head
    command.upgrade(cfg, "head")
    engine = create_db_engine(db_url)
    try:
        insp = inspect(engine)
        assert "recurring" in set(insp.get_table_names())
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0010"
    finally:
        engine.dispose()


def test_migration_0004_upgrade_downgrade(db_url):
    """Tes migrasi naik dan turun untuk revisi 0004 (tabel audit_log)."""
    cfg = make_alembic_config(db_url)

    # 1. Migrasi ke 0003: tabel audit_log belum ada
    command.upgrade(cfg, "0003")
    engine = create_db_engine(db_url)
    try:
        insp = inspect(engine)
        assert "audit_log" not in set(insp.get_table_names())
    finally:
        engine.dispose()

    # 2. Migrasi naik ke 0004: tabel audit_log dibuat
    command.upgrade(cfg, "0004")
    engine = create_db_engine(db_url)
    try:
        insp = inspect(engine)
        assert "audit_log" in set(insp.get_table_names())
        cols = {c["name"] for c in insp.get_columns("audit_log")}
        assert {
            "id",
            "user_id",
            "action",
            "entity_type",
            "entity_id",
            "old_values",
            "new_values",
            "description",
            "is_undone",
            "created_at",
        } <= cols
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0004"
    finally:
        engine.dispose()

    # 3. Migrasi turun ke 0003: tabel audit_log dihapus
    command.downgrade(cfg, "0003")
    engine = create_db_engine(db_url)
    try:
        insp = inspect(engine)
        assert "audit_log" not in set(insp.get_table_names())
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0003"
    finally:
        engine.dispose()

    # 4. Migrasi naik kembali ke head
    command.upgrade(cfg, "head")
    engine = create_db_engine(db_url)
    try:
        insp = inspect(engine)
        assert "audit_log" in set(insp.get_table_names())
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0010"
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


def test_recurring_check_constraints_in_migrated_database(migrated_engine):
    with migrated_engine.connect() as conn:
        conn.execute(
            text("INSERT INTO users (id, name, created_at) VALUES (100, 'U', '2026-01-01')")
        )
        conn.execute(
            text("INSERT INTO payment_methods (id, user_id, name) VALUES (100, 100, 'Bank')")
        )
        conn.execute(
            text(
                "INSERT INTO categories (id, user_id, name, type) "
                "VALUES (100, 100, 'Kos', 'expense')"
            )
        )
        conn.commit()

        # amount <= 0 ditolak
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO recurring "
                    "(user_id, name, type, amount, category_id, method_id, frequency, "
                    "start_date, created_at, updated_at) "
                    "VALUES (100, 'Kos', 'expense', 0, 100, 100, 'monthly', "
                    "'2026-01-01', '2026-01-01', '2026-01-01')"
                )
            )

        # frequency tidak valid ditolak
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO recurring "
                    "(user_id, name, type, amount, category_id, method_id, frequency, "
                    "start_date, created_at, updated_at) "
                    "VALUES (100, 'Kos', 'expense', 1000, 100, 100, 'hourly', "
                    "'2026-01-01', '2026-01-01', '2026-01-01')"
                )
            )


def test_audit_log_check_constraints_in_migrated_database(migrated_engine):
    with migrated_engine.connect() as conn:
        conn.execute(
            text("INSERT INTO users (id, name, created_at) VALUES (200, 'AuditUser', '2026-01-01')")
        )
        conn.commit()

        # action tidak valid ditolak
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO audit_log "
                    "(user_id, action, entity_type, entity_id, created_at) "
                    "VALUES (200, 'invalid_action', 'transaction', 1, '2026-01-01')"
                )
            )

        # action kosong ditolak
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO audit_log "
                    "(user_id, action, entity_type, entity_id, created_at) "
                    "VALUES (200, '   ', 'transaction', 1, '2026-01-01')"
                )
            )

        # entity_type kosong ditolak
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO audit_log "
                    "(user_id, action, entity_type, entity_id, created_at) "
                    "VALUES (200, 'create', '   ', 1, '2026-01-01')"
                )
            )


def test_migration_0005_upgrade_downgrade(db_url):
    """Tes upgrade dan downgrade untuk revisi 0005 (tabel quick_aliases)."""
    cfg = make_alembic_config(db_url)

    # 1. Upgrade sampai 0004
    command.upgrade(cfg, "0004")
    engine = create_db_engine(db_url)
    try:
        assert "quick_aliases" not in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    # 2. Upgrade ke 0005
    command.upgrade(cfg, "0005")
    engine = create_db_engine(db_url)
    try:
        assert "quick_aliases" in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    # 3. Downgrade kembali ke 0004
    command.downgrade(cfg, "0004")
    engine = create_db_engine(db_url)
    try:
        assert "quick_aliases" not in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_quick_aliases_check_constraints_in_migrated_database(migrated_engine):
    with migrated_engine.connect() as conn:
        conn.execute(
            text("INSERT INTO users (id, name, created_at) VALUES (300, 'AliasUser', '2026-01-01')")
        )
        conn.execute(
            text(
                "INSERT INTO categories (id, user_id, name, type, is_archived, is_system) "
                "VALUES (301, 300, 'Makanan', 'expense', 0, 0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO payment_methods (id, user_id, name, is_archived) "
                "VALUES (302, 300, 'Cash', 0)"
            )
        )
        conn.commit()

        # Alias kosong ditolak
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO quick_aliases "
                    "(user_id, alias, target_type, category_id, created_at) "
                    "VALUES (300, '   ', 'category', 301, '2026-01-01')"
                )
            )

        # target_type tidak valid ditolak
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO quick_aliases "
                    "(user_id, alias, target_type, category_id, created_at) "
                    "VALUES (300, 'makan', 'invalid', 301, '2026-01-01')"
                )
            )

        # Kategori tanpa category_id ditolak
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO quick_aliases "
                    "(user_id, alias, target_type, category_id, created_at) "
                    "VALUES (300, 'makan', 'category', NULL, '2026-01-01')"
                )
            )

        # Kategori tetapi method_id terisi ditolak
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO quick_aliases "
                    "(user_id, alias, target_type, category_id, method_id, created_at) "
                    "VALUES (300, 'makan', 'category', 301, 302, '2026-01-01')"
                )
            )


def test_migration_0006_upgrade_downgrade(db_url):
    """Tes upgrade dan downgrade untuk revisi 0006 (tabel reminder_settings)."""
    cfg = make_alembic_config(db_url)

    # 1. Upgrade sampai 0005
    command.upgrade(cfg, "0005")
    engine = create_db_engine(db_url)
    try:
        assert "reminder_settings" not in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    # 2. Upgrade ke 0006
    command.upgrade(cfg, "0006")
    engine = create_db_engine(db_url)
    try:
        assert "reminder_settings" in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    # 3. Downgrade kembali ke 0005
    command.downgrade(cfg, "0005")
    engine = create_db_engine(db_url)
    try:
        assert "reminder_settings" not in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_reminder_settings_check_constraints_in_migrated_database(migrated_engine):
    with migrated_engine.connect() as conn:
        conn.execute(
            text("INSERT INTO users (id, name, created_at) VALUES (400, 'RemUser', '2026-01-01')")
        )
        conn.commit()

        # Format jam tidak valid (bukan 5 karakter) ditolak
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO reminder_settings "
                    "(user_id, daily_time, created_at, updated_at) "
                    "VALUES (400, '9:00', '2026-01-01', '2026-01-01')"
                )
            )

        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO reminder_settings "
                    "(user_id, daily_time, created_at, updated_at) "
                    "VALUES (400, '  :  ', '2026-01-01', '2026-01-01')"
                )
            )


def test_migration_0007_upgrade_downgrade(db_url):
    """Tes upgrade dan downgrade untuk revisi 0007 (weekly_budgets dan rollover budgets)."""
    cfg = make_alembic_config(db_url)

    # 1. Upgrade sampai 0006
    command.upgrade(cfg, "0006")
    engine = create_db_engine(db_url)
    try:
        insp = inspect(engine)
        assert "weekly_budgets" not in set(insp.get_table_names())
        b_cols = {c["name"] for c in insp.get_columns("budgets")}
        assert "carryover_amount" not in b_cols
        assert "is_rollover_enabled" not in b_cols
    finally:
        engine.dispose()

    # 2. Upgrade ke 0007
    command.upgrade(cfg, "0007")
    engine = create_db_engine(db_url)
    try:
        insp = inspect(engine)
        assert "weekly_budgets" in set(inspect(engine).get_table_names())
        b_cols = {c["name"] for c in insp.get_columns("budgets")}
        assert "carryover_amount" in b_cols
        assert "is_rollover_enabled" in b_cols
    finally:
        engine.dispose()

    # 3. Downgrade kembali ke 0006
    command.downgrade(cfg, "0006")
    engine = create_db_engine(db_url)
    try:
        insp = inspect(engine)
        assert "weekly_budgets" not in set(insp.get_table_names())
        b_cols = {c["name"] for c in insp.get_columns("budgets")}
        assert "carryover_amount" not in b_cols
    finally:
        engine.dispose()


def test_weekly_budgets_and_rollover_check_constraints_in_migrated_database(migrated_engine):
    with migrated_engine.connect() as conn:
        conn.execute(
            text("INSERT INTO users (id, name, created_at) VALUES (500, 'WbUser', '2026-01-01')")
        )
        conn.commit()

        # WeeklyBudget amount <= 0 ditolak
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO weekly_budgets "
                    "(user_id, category_id, week_start, amount) "
                    "VALUES (500, NULL, '2026-10-05', 0)"
                )
            )

        # Budget carryover_amount < 0 ditolak
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO budgets "
                    "(user_id, category_id, month, amount, carryover_amount, is_rollover_enabled) "
                    "VALUES (500, NULL, '2026-10', 1000000, -50000, 1)"
                )
            )


def test_migration_0008_upgrade_downgrade(db_url):
    """Tes upgrade dan downgrade untuk revisi 0008 (role, status di users dan tabel invites)."""
    cfg = make_alembic_config(db_url)

    # 1. Upgrade sampai 0007
    command.upgrade(cfg, "0007")
    engine = create_db_engine(db_url)
    try:
        insp = inspect(engine)
        assert "invites" not in set(insp.get_table_names())
        u_cols = {c["name"] for c in insp.get_columns("users")}
        assert "role" not in u_cols
        assert "status" not in u_cols
    finally:
        engine.dispose()

    # 2. Upgrade ke 0008
    command.upgrade(cfg, "0008")
    engine = create_db_engine(db_url)
    try:
        insp = inspect(engine)
        assert "invites" in set(insp.get_table_names())
        u_cols = {c["name"] for c in insp.get_columns("users")}
        assert "role" in u_cols
        assert "status" in u_cols
    finally:
        engine.dispose()

    # 3. Downgrade kembali ke 0007
    command.downgrade(cfg, "0007")
    engine = create_db_engine(db_url)
    try:
        insp = inspect(engine)
        assert "invites" not in set(insp.get_table_names())
        u_cols = {c["name"] for c in insp.get_columns("users")}
        assert "role" not in u_cols
        assert "status" not in u_cols
    finally:
        engine.dispose()


def test_users_and_invites_check_constraints_in_migrated_database(migrated_engine):
    with migrated_engine.connect() as conn:
        conn.execute(
            text("INSERT INTO users (id, name, created_at) VALUES (600, 'AdminU', '2026-01-01')")
        )
        conn.commit()

        # role tidak valid ditolak
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO users (name, role, status, created_at) "
                    "VALUES ('X', 'superadmin', 'active', '2026-01-01')"
                )
            )

        # status tidak valid ditolak
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO users (name, role, status, created_at) "
                    "VALUES ('X', 'user', 'suspended', '2026-01-01')"
                )
            )

        # invite code kosong ditolak
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO invites (user_id, code, is_used, created_at) "
                    "VALUES (600, '   ', 0, '2026-01-01')"
                )
            )

        # invite is_used = 1 tapi used_by_user_id NULL ditolak
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO invites (user_id, code, is_used, used_by_user_id, created_at) "
                    "VALUES (600, 'ABC123', 1, NULL, '2026-01-01')"
                )
            )


def test_migration_0009_upgrade_downgrade(db_url):
    """Tes upgrade dan downgrade untuk revisi 0009 (user_settings dan sent_notifications)."""
    cfg = make_alembic_config(db_url)

    # 1. Upgrade sampai 0008
    command.upgrade(cfg, "0008")
    engine = create_db_engine(db_url)
    try:
        insp = inspect(engine)
        tables = set(insp.get_table_names())
        assert "user_settings" not in tables
        assert "sent_notifications" not in tables
    finally:
        engine.dispose()

    # 2. Upgrade ke 0009
    command.upgrade(cfg, "0009")
    engine = create_db_engine(db_url)
    try:
        insp = inspect(engine)
        tables = set(insp.get_table_names())
        assert "user_settings" in tables
        assert "sent_notifications" in tables
        us_cols = {c["name"] for c in insp.get_columns("user_settings")}
        assert {
            "id",
            "user_id",
            "timezone",
            "daily_time",
            "is_daily_active",
            "is_weekly_active",
            "is_monthly_active",
            "is_recurring_active",
        } <= us_cols
        sn_cols = {c["name"] for c in insp.get_columns("sent_notifications")}
        assert {
            "id",
            "user_id",
            "notification_type",
            "period_key",
            "created_at",
        } <= sn_cols
    finally:
        engine.dispose()

    # 3. Downgrade kembali ke 0008
    command.downgrade(cfg, "0008")
    engine = create_db_engine(db_url)
    try:
        insp = inspect(engine)
        tables = set(insp.get_table_names())
        assert "user_settings" not in tables
        assert "sent_notifications" not in tables
    finally:
        engine.dispose()


def test_user_settings_and_sent_notifications_check_constraints_in_migrated_database(
    migrated_engine,
):
    with migrated_engine.connect() as conn:
        conn.execute(
            text("INSERT INTO users (id, name, created_at) VALUES (700, 'TzUser', '2026-01-01')")
        )
        conn.commit()

        # user_settings timezone kosong ditolak
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO user_settings "
                    "(user_id, timezone, daily_time, created_at, updated_at) "
                    "VALUES (700, '   ', '20:00', '2026-01-01', '2026-01-01')"
                )
            )

        # user_settings daily_time tidak 5 karakter ditolak
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO user_settings "
                    "(user_id, timezone, daily_time, created_at, updated_at) "
                    "VALUES (700, 'Asia/Jakarta', '8:00', '2026-01-01', '2026-01-01')"
                )
            )

        # sent_notifications notification_type tidak valid ditolak
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO sent_notifications "
                    "(user_id, notification_type, period_key, created_at) "
                    "VALUES (700, 'yearly', '2026', '2026-01-01')"
                )
            )

        # sent_notifications period_key kosong ditolak
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO sent_notifications "
                    "(user_id, notification_type, period_key, created_at) "
                    "VALUES (700, 'daily', '   ', '2026-01-01')"
                )
            )

        # sent_notifications tipe prediktif diterima di 0010
        conn.execute(
            text(
                "INSERT INTO sent_notifications "
                "(user_id, notification_type, period_key, created_at) "
                "VALUES (700, 'predictive_budget', '2026-10:budget_proj_exceeded', '2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO sent_notifications "
                "(user_id, notification_type, period_key, created_at) "
                "VALUES (700, 'predictive_runway', '2026-10:runway_critical', '2026-01-01')"
            )
        )
        conn.commit()


def test_migration_0010_upgrade_downgrade(db_url):
    """Tes upgrade dan downgrade untuk revisi 0010 (tipe notifikasi prediktif)."""
    cfg = make_alembic_config(db_url)

    # 1. Upgrade sampai 0009
    command.upgrade(cfg, "0009")
    engine = create_db_engine(db_url)
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, name, created_at) "
                    "VALUES (800, 'User0010', '2026-01-01')"
                )
            )
            conn.commit()
            # Di 0009, 'predictive_budget' ditolak
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO sent_notifications "
                        "(user_id, notification_type, period_key, created_at) "
                        "VALUES (800, 'predictive_budget', '2026-10:test', '2026-01-01')"
                    )
                )
    finally:
        engine.dispose()

    # 2. Upgrade ke 0010
    command.upgrade(cfg, "0010")
    engine = create_db_engine(db_url)
    try:
        with engine.connect() as conn:
            # Di 0010, 'predictive_budget' dan 'predictive_runway' diterima
            conn.execute(
                text(
                    "INSERT INTO sent_notifications "
                    "(user_id, notification_type, period_key, created_at) "
                    "VALUES (800, 'predictive_budget', '2026-10:test', '2026-01-01')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO sent_notifications "
                    "(user_id, notification_type, period_key, created_at) "
                    "VALUES (800, 'predictive_runway', '2026-10:test', '2026-01-01')"
                )
            )
            conn.commit()
    finally:
        engine.dispose()

    # 3. Downgrade kembali ke 0009
    # Hapus dulu row prediktif agar downgrade tidak melanggar constraint 0009
    engine = create_db_engine(db_url)
    try:
        with engine.connect() as conn:
            conn.execute(
                text("DELETE FROM sent_notifications WHERE notification_type LIKE 'predictive%'")
            )
            conn.commit()
    finally:
        engine.dispose()

    command.downgrade(cfg, "0009")
    engine = create_db_engine(db_url)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0009"
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO sent_notifications "
                        "(user_id, notification_type, period_key, created_at) "
                        "VALUES (800, 'predictive_budget', '2026-10:test2', '2026-01-01')"
                    )
                )
    finally:
        engine.dispose()


def test_downgrade_removes_tables(migrated_engine, db_url):
    command.downgrade(make_alembic_config(db_url), "base")
    tables = set(inspect(migrated_engine).get_table_names())
    assert not (EXPECTED_TABLES & tables)
