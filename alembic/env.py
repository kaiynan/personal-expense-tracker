"""Lingkungan Alembic: menghubungkan migrasi dengan model dan config proyek."""

import config as app_config
from alembic import context
from database.engine import create_db_engine
from models import Base

alembic_config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    """URL dari konfigurasi Alembic bila ada, selain itu dari config.py."""
    return alembic_config.get_main_option("sqlalchemy.url") or app_config.DATABASE_URL


def _configure_and_run(connection) -> None:
    """Jalankan migrasi pada koneksi yang diberikan."""
    is_sqlite = connection.dialect.name == "sqlite"
    if is_sqlite:
        if connection.in_transaction():
            connection.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
        if connection.in_transaction():
            connection.commit()
    try:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite tidak mendukung semua ALTER TABLE
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        if connection.in_transaction():
            connection.commit()
    finally:
        if is_sqlite:
            if connection.in_transaction():
                connection.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys = ON")
            if connection.in_transaction():
                connection.commit()


def run_migrations_offline() -> None:
    """Hasilkan SQL tanpa koneksi (alembic upgrade --sql)."""
    app_config.ensure_directories()
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Jalankan migrasi pada database sungguhan."""
    app_config.ensure_directories()
    injected = alembic_config.attributes.get("connection")
    if injected is not None:
        _configure_and_run(injected)
        return

    engine = create_db_engine(_database_url())
    try:
        with engine.connect() as connection:
            _configure_and_run(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
