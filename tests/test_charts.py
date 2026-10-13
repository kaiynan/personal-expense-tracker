"""Tes pembuatan 4 grafik keuangan (charts) menggunakan matplotlib Agg."""

from __future__ import annotations

from datetime import date

import pytest

from charts import generate_all_charts
from database.engine import create_session_factory, session_scope
from database.seed import ensure_default_user, seed_defaults
from services import opening_balances, transactions


@pytest.fixture
def chart_ctx(engine, tmp_path, monkeypatch):
    """Factory session dan user id dengan folder output sementara."""
    import config

    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path / "reports_output")

    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        u = ensure_default_user(session)
        seed_defaults(session, u.id)
        user_id = u.id

    return factory, user_id, config.REPORTS_DIR


def test_generate_all_four_charts_with_sample_data(chart_ctx):
    """Empat grafik (pengeluaran, pemasukan, arus kas, saldo kumulatif) dibuat dan valid PNG."""
    factory, user_id, _reports_dir = chart_ctx

    start_date = date(2026, 10, 1)
    end_date = date(2026, 10, 15)

    with session_scope(factory) as session:
        from models import Category, PaymentMethod

        salary = session.query(Category).filter_by(user_id=user_id, name="Gaji").one().id
        bonus = session.query(Category).filter_by(user_id=user_id, name="Bonus").one().id
        food = session.query(Category).filter_by(user_id=user_id, name="Makanan").one().id
        transport = session.query(Category).filter_by(user_id=user_id, name="Transportasi").one().id
        cash = session.query(PaymentMethod).filter_by(user_id=user_id, name="Cash").one().id
        bank = session.query(PaymentMethod).filter_by(user_id=user_id, name="Bank").one().id

        # Saldo awal
        opening_balances.set_opening_balance(session, user_id, bank, 5_000_000, "01-10-2026")

        # Pemasukan
        transactions.add_income(session, user_id, 3_000_000, salary, bank, on="02-10-2026")
        transactions.add_income(session, user_id, 500_000, bonus, bank, on="05-10-2026")

        # Pengeluaran
        transactions.add_expense(session, user_id, 150_000, food, cash, on="03-10-2026")
        transactions.add_expense(session, user_id, 50_000, transport, cash, on="04-10-2026")
        transactions.add_expense(session, user_id, 200_000, food, cash, on="10-10-2026")

    with session_scope(factory) as session:
        result_charts = generate_all_charts(session, user_id, start_date, end_date, prefix="test")

    expected_keys = {
        "expense_by_category",
        "income_by_category",
        "income_vs_expense",
        "balance_trend",
    }
    assert set(result_charts.keys()) == expected_keys

    # Verifikasi setiap file PNG ada, tidak kosong, dan punya magic bytes PNG
    png_magic = b"\x89PNG\r\n\x1a\n"
    for name, path in result_charts.items():
        assert path.exists(), f"File grafik {name} tidak ditemukan di {path}"
        assert path.stat().st_size > 0, f"File grafik {name} kosong"
        with open(path, "rb") as f:
            header = f.read(8)
            assert header == png_magic, f"File {name} bukan file PNG yang valid"


def test_generate_charts_empty_data(chart_ctx):
    """Grafik pada periode tanpa data tetap dibuat sebagai placeholder valid tanpa error."""
    factory, user_id, _reports_dir = chart_ctx
    start_date = date(2026, 10, 1)
    end_date = date(2026, 10, 7)

    with session_scope(factory) as session:
        charts = generate_all_charts(session, user_id, start_date, end_date, prefix="empty")

    assert len(charts) == 4
    for path in charts.values():
        assert path.exists()
        assert path.stat().st_size > 0
