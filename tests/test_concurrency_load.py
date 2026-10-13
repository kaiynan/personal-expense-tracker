"""Tes beban sederhana dengan penulisan bersamaan (concurrency) pada basis data SQLite."""

from __future__ import annotations

import concurrent.futures
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from database import queries
from database.engine import create_db_engine, create_session_factory, session_scope
from models import Base, Category, PaymentMethod, User
from services.balances import get_total_balance
from services.transactions import add_expense, add_income


def _setup_concurrency_db() -> tuple[Path, Any, Any]:
    """Siapkan file SQLite sementara dengan migrasi skema dan WAL mode."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_file:
        tmp_path = Path(tmp_file.name)

    engine = create_db_engine(f"sqlite:///{tmp_path.as_posix()}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    return tmp_path, factory, engine


def test_concurrent_writes_single_user():
    """Uji konkurensi: banyak thread menulis transaksi bersamaan untuk satu pengguna."""
    db_path, factory, engine = _setup_concurrency_db()
    try:
        # Inisialisasi pengguna dan master data
        with session_scope(factory) as session:
            user = User(telegram_id=999001, name="Concurrent User", role="user", status="active")
            session.add(user)
            session.flush()
            user_id = user.id

            pm = PaymentMethod(user_id=user_id, name="Dompet Tunai")
            cat = Category(user_id=user_id, name="Makanan", type="expense")
            session.add_all([pm, cat])
            session.flush()
            pm_id = pm.id
            cat_id = cat.id

        total_threads = 5
        tx_per_thread = 10
        amount_per_tx = 15000

        def _worker(thread_idx: int) -> int:
            success_count = 0
            for i in range(tx_per_thread):
                with session_scope(factory) as s:
                    res = add_expense(
                        session=s,
                        user_id=user_id,
                        amount=amount_per_tx,
                        category_id=cat_id,
                        method_id=pm_id,
                        on=date(2026, 10, 1),
                        note=f"Thread-{thread_idx} Tx-{i}",
                    )
                    if res.transaction.id:
                        success_count += 1
            return success_count

        with concurrent.futures.ThreadPoolExecutor(max_workers=total_threads) as executor:
            futures = [executor.submit(_worker, t) for t in range(total_threads)]
            results = [f.result() for f in futures]

        assert sum(results) == total_threads * tx_per_thread

        with session_scope(factory) as session:
            txs = queries.list_transactions(session, user_id)
            assert len(txs) == total_threads * tx_per_thread

            expected_expense = amount_per_tx * (total_threads * tx_per_thread)
            assert get_total_balance(session, user_id) == -expected_expense
    finally:
        engine.dispose()
        db_path.unlink(missing_ok=True)


def test_concurrent_writes_multiple_users():
    """Uji konkurensi: beberapa pengguna menulis transaksi secara simultan dari thread berbeda."""
    db_path, factory, engine = _setup_concurrency_db()
    try:
        user_ids = []
        cat_map = {}
        pm_map = {}

        with session_scope(factory) as session:
            for i in range(4):
                u = User(telegram_id=888000 + i, name=f"User-{i}", role="user", status="active")
                session.add(u)
                session.flush()
                user_ids.append(u.id)

                pm = PaymentMethod(user_id=u.id, name=f"Rekening-{i}")
                cat = Category(user_id=u.id, name=f"Kategori-{i}", type="income")
                session.add_all([pm, cat])
                session.flush()
                pm_map[u.id] = pm.id
                cat_map[u.id] = cat.id

        tx_count_each = 8
        income_amount = 50000

        def _user_worker(uid: int) -> int:
            count = 0
            for k in range(tx_count_each):
                with session_scope(factory) as s:
                    res = add_income(
                        session=s,
                        user_id=uid,
                        amount=income_amount,
                        category_id=cat_map[uid],
                        method_id=pm_map[uid],
                        on=date(2026, 10, 5),
                        note=f"Pemasukan simultan {k}",
                    )
                    if res.transaction.id:
                        count += 1
            return count

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_user_worker, uid) for uid in user_ids]
            counts = [f.result() for f in futures]

        assert all(c == tx_count_each for c in counts)

        # Verifikasi saldo masing-masing pengguna terisolasi dan akurat
        with session_scope(factory) as session:
            for uid in user_ids:
                txs = queries.list_transactions(session, uid)
                assert len(txs) == tx_count_each
                expected_income = income_amount * tx_count_each
                assert get_total_balance(session, uid) == expected_income
    finally:
        engine.dispose()
        db_path.unlink(missing_ok=True)
