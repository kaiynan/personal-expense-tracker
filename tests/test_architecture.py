"""Tes arsitektur: aturan lapisan yang dicek otomatis.

- Tidak ada SQL mentah di luar `database/` (dan `alembic/`, yang memang skrip migrasi).
- Di luar `database/`, `models/`, dan `alembic/`, kode hanya boleh mengimpor
  `Session` dari SQLAlchemy; semua query lewat `database/`.
- Setiap fungsi service publik menerima `(session, user_id, ...)`, sinkron,
  dan punya tes isolasi pengguna.
"""

import ast
import importlib
import inspect
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP_DIRS = ("services", "utils", "cli", "bot", "reports", "charts")
APP_FILES = ("main.py", "config.py")

SERVICE_MODULES = (
    "services.transactions",
    "services.transfers",
    "services.opening_balances",
    "services.adjustments",
    "services.balances",
)

RAW_SQL = re.compile(
    r"\b(SELECT\s+.+\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|"
    r"CREATE\s+TABLE|ALTER\s+TABLE|DROP\s+TABLE|PRAGMA)\b",
    re.IGNORECASE | re.DOTALL,
)
FORBIDDEN_CALLS = {"text", "exec_driver_sql", "literal_column"}


def _app_sources() -> list[Path]:
    files = [ROOT / name for name in APP_FILES]
    for folder in APP_DIRS:
        files += sorted((ROOT / folder).rglob("*.py"))
    return files


@pytest.mark.parametrize("path", _app_sources(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_raw_sql_outside_database(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not RAW_SQL.search(node.value), f"SQL mentah di {path.name}: {node.value!r}"
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            assert name not in FORBIDDEN_CALLS, f"{name}() dipakai di {path.name}"


@pytest.mark.parametrize("path", _app_sources(), ids=lambda p: str(p.relative_to(ROOT)))
def test_only_session_imported_from_sqlalchemy(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("sqlalchemy"), f"import {alias.name} di {path}"
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("sqlalchemy"):
            names = {alias.name for alias in node.names}
            assert node.module == "sqlalchemy.orm" and names <= {"Session"}, (
                f"from {node.module} import {names} di {path.name}"
            )


def _public_service_functions() -> list:
    params = []
    for module_name in SERVICE_MODULES:
        module = importlib.import_module(module_name)
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if fn.__module__ == module_name and not name.startswith("_"):
                params.append(pytest.param(fn, id=f"{module_name}.{name}"))
    return params


@pytest.mark.parametrize("fn", _public_service_functions())
def test_service_signature_is_sync_with_session_and_user_id(fn):
    params = list(inspect.signature(fn).parameters)
    assert params[:2] == ["session", "user_id"]
    assert not inspect.iscoroutinefunction(fn)


@pytest.mark.parametrize("fn", _public_service_functions())
def test_every_service_function_has_isolation_test(fn):
    source = (ROOT / "tests" / "services_layer" / "test_service_isolation.py").read_text(
        encoding="utf-8"
    )
    assert re.search(rf"\b{fn.__name__}\(", source), f"{fn.__name__} belum punya tes isolasi"
