"""Pengujian unit dan integrasi Web Dashboard Fase W5:
- Otorisasi Admin (/admin, require_admin): 401 redirect untuk tamu, 403 untuk pengguna biasa.
- Tampilan Dashboard Admin: kartu status sistem SQLite, metrik pengguna & entitas.
- Visibilitas Menu Admin: muncul di sidebar & /more hanya untuk admin.
- Manajemen Pengguna: Persetujuan (approve) pengguna pending dan pemblokiran (block).
- Manajemen Undangan: Pembuatan kode kustom/acak dengan masa berlaku dan pelacakan status.
- Cadangan Penuh: Unduh full admin backup ZIP.
- Penanganan Kesalahan Global: HTTP 403 ramah pengguna dan error 500 tanpa bocoran log.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest
from fastapi.testclient import TestClient

from database.engine import create_db_engine, create_session_factory, session_scope
from database.queries import get_user
from database.seed import seed_defaults
from models import (
    USER_STATUS_ACTIVE,
    USER_STATUS_BLOCKED,
    USER_STATUS_PENDING,
    Base,
    User,
)
from web.app import create_web_app


@pytest.fixture
def web_db(tmp_path):
    """Database SQLite sementara untuk pengujian admin web."""
    db_file = tmp_path / "web_admin_test.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_db_engine(db_url)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    yield factory, engine
    engine.dispose()


@pytest.fixture
def web_test_env(web_db):
    """TestClient FastAPI dengan mock sender bot Telegram."""
    factory, _engine = web_db
    sent_otps: list[dict[str, Any]] = []

    def mock_bot_sender(telegram_id: int, code: str):
        sent_otps.append({"telegram_id": telegram_id, "code": code})

    app = create_web_app(factory=factory, bot_sender=mock_bot_sender, enable_bot=False)
    app.state.sent_otps = sent_otps

    with TestClient(app) as client:
        yield client, factory, sent_otps


def _create_user(
    factory,
    name: str,
    telegram_id: int,
    role: str = "user",
    status: str = USER_STATUS_ACTIVE,
) -> User:
    """Helper membuat entitas User."""
    with session_scope(factory) as session:
        user = User(name=name, telegram_id=telegram_id, role=role, status=status)
        session.add(user)
        session.flush()
        if status == USER_STATUS_ACTIVE:
            seed_defaults(session, user.id)
        user_id = user.id

    with session_scope(factory) as session:
        return session.get(User, user_id)


def _login(client: TestClient, sent_otps: list, user: User):
    """Helper login OTP."""
    client.post("/auth/request-otp", data={"telegram_id": str(user.telegram_id)})
    code = sent_otps[-1]["code"]
    res = client.post(
        "/auth/verify-otp",
        data={"user_id": user.id, "code": code, "telegram_id": user.telegram_id},
    )
    assert res.status_code in (200, 303)


# ==============================================================================
# 1. Otorisasi & Akses Role
# ==============================================================================


def test_admin_unauthenticated_redirects_to_login(web_test_env):
    """Pengguna tanpa sesi login diarahkan ke halaman login."""
    client, _, _ = web_test_env
    res = client.get("/admin", follow_redirects=False)
    assert res.status_code == 303
    assert "/login" in res.headers["location"]


def test_admin_forbidden_for_regular_user(web_test_env):
    """Pengguna dengan peran 'user' ditolak dengan kode status 403 Forbidden."""
    client, factory, sent_otps = web_test_env
    normal_user = _create_user(factory, "NormalUser", 1001, role="user")

    _login(client, sent_otps, normal_user)

    # 1. Akses halaman admin
    res = client.get("/admin")
    assert res.status_code == 403
    assert "Akses Ditolak" in res.text or "403" in res.text

    # 2. Akses endpoint mutasi admin
    res_approve = client.post("/admin/users/1/approve")
    assert res_approve.status_code == 403

    res_block = client.post("/admin/users/1/block")
    assert res_block.status_code == 403

    res_invite = client.post("/admin/invites/create", data={"expires_in_days": "7"})
    assert res_invite.status_code == 403

    res_backup = client.get("/admin/backup/download")
    assert res_backup.status_code == 403


def test_admin_menu_visibility_in_sidebar_and_more(web_test_env):
    """Menu panel admin hanya muncul jika pengguna adalah admin."""
    client, factory, sent_otps = web_test_env
    normal_user = _create_user(factory, "Biasa", 1001, role="user")
    admin_user = _create_user(factory, "AdminUtama", 9999, role="admin")

    # User biasa tidak melihat link /admin
    _login(client, sent_otps, normal_user)
    res_dash = client.get("/")
    assert 'href="/admin"' not in res_dash.text
    res_more = client.get("/more")
    assert 'href="/admin"' not in res_more.text

    # Logout
    client.post("/auth/logout")

    # Admin melihat link /admin di sidebar & /more
    _login(client, sent_otps, admin_user)
    res_dash = client.get("/")
    assert 'href="/admin"' in res_dash.text
    res_more = client.get("/more")
    assert 'href="/admin"' in res_more.text


# ==============================================================================
# 2. Dashboard Panel Admin & Status Sistem
# ==============================================================================


def test_admin_dashboard_view_and_diagnostics(web_test_env):
    """Admin dapat membuka /admin dan melihat diagnostik SQLite serta metrik."""
    client, factory, sent_otps = web_test_env
    admin_user = _create_user(factory, "SuperAdmin", 7777, role="admin")
    _login(client, sent_otps, admin_user)

    res = client.get("/admin")
    assert res.status_code == 200
    assert "Pusat Kendali Administrator" in res.text
    assert "Status Operasional & Lingkungan Server" in res.text
    assert "Manajemen Pengguna Sistem" in res.text
    assert "Manajemen Kode Undangan (Invites)" in res.text
    assert "Unduh Backup Penuh" in res.text
    assert "SuperAdmin" in res.text


# ==============================================================================
# 3. Manajemen Pengguna: Approve & Block
# ==============================================================================


def test_admin_approve_and_block_user(web_test_env):
    """Admin dapat menyetujui pengguna pending dan memblokir pengguna aktif."""
    client, factory, sent_otps = web_test_env
    admin_user = _create_user(factory, "BossAdmin", 8888, role="admin")
    pending_user = _create_user(factory, "NewUser", 12345, role="user", status=USER_STATUS_PENDING)

    _login(client, sent_otps, admin_user)

    # 1. Setujui pengguna pending
    res = client.post(f"/admin/users/{pending_user.id}/approve")
    assert res.status_code == 200
    assert "berhasil disetujui" in res.text
    assert "adminUpdated" in res.headers.get("HX-Trigger", "")

    # Verifikasi status database berubah menjadi active
    with session_scope(factory) as session:
        u = get_user(session, pending_user.id)
        assert u.status == USER_STATUS_ACTIVE

    # 2. Blokir pengguna
    res = client.post(f"/admin/users/{pending_user.id}/block")
    assert res.status_code == 200
    assert "berhasil diblokir" in res.text

    # Verifikasi status database berubah menjadi blocked
    with session_scope(factory) as session:
        u = get_user(session, pending_user.id)
        assert u.status == USER_STATUS_BLOCKED

    # 3. Cegah admin memblokir dirinya sendiri
    res_self = client.post(f"/admin/users/{admin_user.id}/block")
    assert res_self.status_code == 200
    assert "Admin tidak dapat memblokir akunnya sendiri" in res_self.text


# ==============================================================================
# 4. Manajemen Kode Undangan (Invites)
# ==============================================================================


def test_admin_create_invites(web_test_env):
    """Admin dapat membuat kode undangan kustom dan acak."""
    client, factory, sent_otps = web_test_env
    admin_user = _create_user(factory, "InviteAdmin", 5555, role="admin")
    _login(client, sent_otps, admin_user)

    # 1. Buat kode kustom
    res = client.post(
        "/admin/invites/create",
        data={"custom_code": "PRO-2026", "expires_in_days": "7"},
    )
    assert res.status_code == 200
    assert "PRO-2026" in res.text
    assert "adminUpdated" in res.headers.get("HX-Trigger", "")

    # 2. Buat kode acak
    res_rand = client.post(
        "/admin/invites/create",
        data={"custom_code": "", "expires_in_days": "30"},
    )
    assert res_rand.status_code == 200
    assert "berhasil dibuat" in res_rand.text

    # 3. Periksa tampilan daftar undangan di dashboard admin
    res_page = client.get("/admin")
    assert res_page.status_code == 200
    assert "PRO-2026" in res_page.text
    assert "Aktif" in res_page.text


# ==============================================================================
# 5. Unduh Cadangan Penuh (Full Admin Backup ZIP)
# ==============================================================================


def test_admin_download_full_backup(web_test_env):
    """Admin dapat mengunduh file ZIP cadangan penuh seluruh database."""
    client, factory, sent_otps = web_test_env
    admin_user = _create_user(factory, "BackupAdmin", 3333, role="admin")
    _login(client, sent_otps, admin_user)

    res = client.get("/admin/backup/download")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    cd_header = res.headers.get("content-disposition", "")
    assert cd_header.endswith('.zip"') or ".zip" in cd_header

    # Validasi isi file ZIP
    zip_bytes = io.BytesIO(res.content)
    with zipfile.ZipFile(zip_bytes, "r") as zf:
        namelist = zf.namelist()
        assert len(namelist) > 0
        assert "system_manifest.json" in namelist
        assert "invites.json" in namelist
        assert any(n.startswith("users/user_") for n in namelist)


# ==============================================================================
# 6. Global Exception Handlers (404, 500, HTMX & Log Sanitization)
# ==============================================================================


def test_global_404_handler(web_test_env):
    """Permintaan ke route yang tidak dikenal menghasilkan respons 404 ramah pengguna."""
    client, _, _ = web_test_env
    res = client.get("/route-tidak-ditemukan-12345", headers={"Accept": "text/html"})
    assert res.status_code == 404
    assert "Halaman Tidak Ditemukan" in res.text or "404" in res.text


def test_global_500_handler(web_db):
    """Kesalahan tak terduga menghasilkan 500 ramah pengguna tanpa membocorkan detail internal."""
    from unittest.mock import patch

    factory, _ = web_db
    admin_user = _create_user(factory, "ErrAdmin", 2222, role="admin")
    sent_otps: list[dict[str, Any]] = []

    def mock_bot_sender(telegram_id: int, code: str):
        sent_otps.append({"telegram_id": telegram_id, "code": code})

    app = create_web_app(factory=factory, bot_sender=mock_bot_sender, enable_bot=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        _login(client, sent_otps, admin_user)

        with patch("services.users.list_users", side_effect=RuntimeError("Simulated DB Crash")):
            # Request HTML biasa
            res = client.get("/admin", headers={"Accept": "text/html"})
            assert res.status_code == 500
            assert "Kesalahan Server Internal" in res.text or "500" in res.text
            assert "Simulated DB Crash" not in res.text

            # Request via HTMX
            res_htmx = client.get("/admin", headers={"HX-Request": "true"})
            assert res_htmx.status_code == 500
            assert "Terjadi kesalahan pada sistem" in res_htmx.text
            assert "Simulated DB Crash" not in res_htmx.text
