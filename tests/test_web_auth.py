"""Pengujian unit dan integrasi Web Dashboard Fase W1:
- Healthcheck endpoint (/health)
- Halaman login & App Shell UI (Tailwind, HTMX, Theme, Privacy, Toast, Rupiah Formatter)
- Alur minta OTP & verifikasi OTP (mock pengiriman Telegram Bot)
- Penolakan akun pengguna pending & blocked
- Proteksi route untuk pengguna yang belum terotentikasi
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

import config
from database.engine import create_db_engine, create_session_factory, session_scope
from database.seed import seed_defaults
from models import (
    USER_STATUS_ACTIVE,
    USER_STATUS_BLOCKED,
    USER_STATUS_PENDING,
    Base,
    User,
    WebLoginOtp,
)
from services import auth_web
from utils.errors import NotFoundError, ValidationError
from web.app import create_web_app


@pytest.fixture
def web_db(tmp_path):
    """Sediakan database SQLite berbasis file sementara untuk pengujian web."""
    db_file = tmp_path / "web_test.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_db_engine(db_url)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    yield factory, engine
    engine.dispose()


@pytest.fixture
def web_test_env(web_db):
    """Siapkan aplikasi web dengan mock sender bot Telegram dan TestClient."""
    factory, _engine = web_db
    sent_otps: list[dict[str, int | str]] = []

    def mock_bot_sender(telegram_id: int, code: str):
        sent_otps.append({"telegram_id": telegram_id, "code": code})

    app = create_web_app(factory=factory, bot_sender=mock_bot_sender, enable_bot=False)
    app.state.sent_otps = sent_otps

    with TestClient(app) as client:
        yield client, factory, sent_otps


def _create_user(
    factory,
    name: str = "Kaiynan",
    telegram_id: int = 123456789,
    status: str = USER_STATUS_ACTIVE,
    role: str = "user",
) -> User:
    """Helper untuk membuat entitas User di database uji coba."""
    with session_scope(factory) as session:
        user = User(
            name=name,
            telegram_id=telegram_id,
            status=status,
            role=role,
        )
        session.add(user)
        session.flush()
        if status == USER_STATUS_ACTIVE:
            seed_defaults(session, user.id)
        user_id = user.id

    with session_scope(factory) as session:
        return session.get(User, user_id)


# --- 1. Healthcheck & Lifespan Tests ---


def test_healthcheck_endpoint_healthy(web_test_env):
    client, _, _ = web_test_env
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"
    assert "db_diagnostics" in data


def test_healthcheck_unhealthy_when_db_fails():
    class FailingFactory:
        def __call__(self):
            raise RuntimeError("Database connection failed")

    app = create_web_app(factory=FailingFactory(), enable_bot=False)
    with TestClient(app) as client:
        res = client.get("/health")
        assert res.status_code == 503
        data = res.json()
        assert data["status"] == "unhealthy"
        assert data["database"] == "error"


def test_lifespan_with_bot_running_status(web_db):
    from unittest.mock import MagicMock

    factory, _ = web_db
    mock_bot_app = MagicMock()
    mock_bot_app.updater.running = True

    app = create_web_app(factory=factory, bot_app=mock_bot_app, enable_bot=False)
    with TestClient(app) as client:
        res = client.get("/health")
        assert res.status_code == 200
        assert res.json()["bot"] == "running"


# --- 2. Route Protection & Login Page Tests ---


def test_login_page_renders_cleanly(web_test_env):
    client, _, _ = web_test_env
    res = client.get("/login")
    assert res.status_code == 200
    assert "Masuk dengan Telegram" in res.text
    assert "ID Telegram Anda" in res.text
    assert "Kirim Kode OTP" in res.text
    assert "toast-container" in res.text


def test_protected_routes_redirect_to_login_when_unauthenticated(web_test_env):
    client, _, _ = web_test_env
    for path in ("/", "/history", "/analytics", "/more"):
        res = client.get(path, follow_redirects=False)
        assert res.status_code == 303
        assert res.headers["location"] == "/login"


# --- 3. OTP Request (Step 1) Tests ---


def test_request_otp_invalid_telegram_id_format(web_test_env):
    client, _, sent = web_test_env
    res = client.post("/auth/request-otp", data={"telegram_id": "bukan_angka"})
    assert res.status_code == 400
    assert "ID Telegram harus berupa angka" in res.text
    assert len(sent) == 0


def test_request_otp_user_not_found(web_test_env):
    client, _, sent = web_test_env
    res = client.post("/auth/request-otp", data={"telegram_id": "999999999"})
    assert res.status_code == 400
    assert "ID Telegram tidak terdaftar" in res.text
    assert len(sent) == 0


def test_request_otp_pending_user_rejected(web_test_env):
    client, factory, sent = web_test_env
    pending_user = _create_user(
        factory, name="CalonUser", telegram_id=111222, status=USER_STATUS_PENDING
    )

    res = client.post("/auth/request-otp", data={"telegram_id": str(pending_user.telegram_id)})
    assert res.status_code == 400
    assert "pending" in res.text.lower()
    assert len(sent) == 0


def test_request_otp_blocked_user_rejected(web_test_env):
    client, factory, sent = web_test_env
    blocked_user = _create_user(
        factory, name="BadUser", telegram_id=333444, status=USER_STATUS_BLOCKED
    )

    res = client.post("/auth/request-otp", data={"telegram_id": str(blocked_user.telegram_id)})
    assert res.status_code == 400
    assert "diblokir" in res.text.lower()
    assert len(sent) == 0


def test_request_otp_success_sends_telegram_message(web_test_env):
    client, factory, sent = web_test_env
    active_user = _create_user(factory, name="Budi", telegram_id=555666)

    res = client.post("/auth/request-otp", data={"telegram_id": str(active_user.telegram_id)})
    assert res.status_code == 200
    assert "Verifikasi Kode OTP" in res.text
    assert "555666" in res.text
    assert 'name="code"' in res.text

    # Verifikasi pengiriman Telegram
    assert len(sent) == 1
    assert sent[0]["telegram_id"] == 555666
    code = str(sent[0]["code"])
    assert len(code) == 6
    assert code.isdigit()


def test_request_otp_rate_limiting(web_test_env):
    client, factory, _ = web_test_env
    active_user = _create_user(factory, name="Spammer", telegram_id=777888)

    # Kirim request hingga mencapai limit config.WEB_OTP_RATE_LIMIT_PER_MINUTE
    for _ in range(config.WEB_OTP_RATE_LIMIT_PER_MINUTE):
        res = client.post("/auth/request-otp", data={"telegram_id": str(active_user.telegram_id)})
        assert res.status_code == 200

    # Request berikutnya harus ditolak oleh rate limiter
    res_limited = client.post(
        "/auth/request-otp", data={"telegram_id": str(active_user.telegram_id)}
    )
    assert res_limited.status_code == 400
    assert "Terlalu banyak permintaan OTP" in res_limited.text


# --- 4. OTP Verification (Step 2) Tests ---


def test_verify_otp_wrong_code(web_test_env):
    client, factory, sent = web_test_env
    user = _create_user(factory, name="Siti", telegram_id=888999)

    client.post("/auth/request-otp", data={"telegram_id": str(user.telegram_id)})
    assert len(sent) == 1

    res = client.post(
        "/auth/verify-otp",
        data={"user_id": user.id, "code": "000000", "telegram_id": user.telegram_id},
    )
    assert res.status_code == 400
    assert "Kode OTP salah" in res.text


def test_verify_otp_invalid_code_format(web_test_env):
    client, factory, _sent = web_test_env
    user = _create_user(factory, name="Doni", telegram_id=999000)

    client.post("/auth/request-otp", data={"telegram_id": str(user.telegram_id)})

    res = client.post(
        "/auth/verify-otp",
        data={"user_id": user.id, "code": "123", "telegram_id": user.telegram_id},
    )
    assert res.status_code == 400
    assert "6 digit" in res.text


def test_verify_otp_expired_code(web_test_env):
    client, factory, sent = web_test_env
    user = _create_user(factory, name="Rina", telegram_id=121212)

    client.post("/auth/request-otp", data={"telegram_id": str(user.telegram_id)})
    code = sent[0]["code"]

    # Simulasikan OTP kedaluwarsa secara langsung di database
    with session_scope(factory) as session:
        otp = session.query(WebLoginOtp).filter_by(user_id=user.id).first()
        otp.expires_at = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(minutes=10)

    res = client.post(
        "/auth/verify-otp",
        data={"user_id": user.id, "code": code, "telegram_id": user.telegram_id},
    )
    assert res.status_code == 400
    assert "kedaluwarsa" in res.text.lower()


def test_verify_otp_success_sets_session_and_redirects(web_test_env):
    client, factory, sent = web_test_env
    user = _create_user(factory, name="Rahmat", telegram_id=343434)

    client.post("/auth/request-otp", data={"telegram_id": str(user.telegram_id)})
    code = sent[0]["code"]

    # Request via HTMX
    res = client.post(
        "/auth/verify-otp",
        data={"user_id": user.id, "code": code, "telegram_id": user.telegram_id},
        headers={"HX-Request": "true"},
    )
    assert res.status_code == 200
    assert res.headers.get("HX-Redirect") == "/"

    # Akses Dashboard dengan session cookie yang telah terpasang
    dash_res = client.get("/")
    assert dash_res.status_code == 200
    assert "Halo, Rahmat!" in dash_res.text
    assert "data-balance" in dash_res.text
    assert "TG: 343434" in dash_res.text


def test_verify_otp_used_code_cannot_be_reused(web_test_env):
    client, factory, sent = web_test_env
    user = _create_user(factory, name="Rudi", telegram_id=565656)

    client.post("/auth/request-otp", data={"telegram_id": str(user.telegram_id)})
    code = sent[0]["code"]

    res1 = client.post(
        "/auth/verify-otp",
        data={"user_id": user.id, "code": code, "telegram_id": user.telegram_id},
    )
    assert res1.status_code == 303 or res1.status_code == 200

    # Percobaan kedua dengan kode yang sama harus gagal
    res2 = client.post(
        "/auth/verify-otp",
        data={"user_id": user.id, "code": code, "telegram_id": user.telegram_id},
    )
    assert res2.status_code == 400
    assert "kedaluwarsa" in res2.text.lower() or "tidak ditemukan" in res2.text.lower()


def test_logout_clears_session(web_test_env):
    client, factory, sent = web_test_env
    user = _create_user(factory, name="Lina", telegram_id=787878)

    client.post("/auth/request-otp", data={"telegram_id": str(user.telegram_id)})
    client.post(
        "/auth/verify-otp",
        data={"user_id": user.id, "code": sent[0]["code"], "telegram_id": user.telegram_id},
    )

    # Pastikan dapat mengakses dashboard
    assert client.get("/").status_code == 200

    # Lakukan logout
    res_logout = client.post("/auth/logout", follow_redirects=False)
    assert res_logout.status_code == 303
    assert res_logout.headers["location"] == "/login"

    # Akses kembali harus ter-redirect ke /login
    assert client.get("/", follow_redirects=False).status_code == 303


# --- 5. App Shell & UI Feature Checks ---


def test_app_shell_dom_elements_and_helpers_present(web_test_env):
    client, factory, sent = web_test_env
    user = _create_user(factory, name="Ahmad", telegram_id=909090)

    client.post("/auth/request-otp", data={"telegram_id": str(user.telegram_id)})
    client.post(
        "/auth/verify-otp",
        data={"user_id": user.id, "code": sent[0]["code"], "telegram_id": user.telegram_id},
    )

    res = client.get("/")
    assert res.status_code == 200
    html = res.text

    # CDN scripts
    assert "cdn.tailwindcss.com" in html
    assert "unpkg.com/htmx.org" in html
    assert "cdn.jsdelivr.net/npm/chart.js" in html

    # Navigasi Desktop & Mobile
    assert "Dashboard" in html
    assert "Riwayat" in html
    assert "Analitik" in html
    assert "Lainnya" in html
    assert "openTransactionModal()" in html

    # Controls
    assert 'id="privacy-toggle"' in html
    assert 'id="theme-toggle"' in html
    assert 'id="toast-container"' in html

    # JavaScript helpers
    assert "togglePrivacyMode" in html
    assert "toggleThemeMode" in html
    assert "formatRupiahLive" in html
    assert "data-balance" in html


# --- 6. Direct Service Layer Isolation & Error Handling Tests ---


def test_services_auth_web_direct_unit_tests(web_db):
    factory, _ = web_db
    with session_scope(factory) as session:
        # User not found
        with pytest.raises(NotFoundError):
            auth_web.validate_telegram_user_for_login(session, 999999999)

        # Invalid telegram_id
        with pytest.raises(ValidationError):
            auth_web.validate_telegram_user_for_login(session, -100)

        # Pending user
        pending = User(name="Pending", telegram_id=111, status=USER_STATUS_PENDING)
        session.add(pending)
        session.flush()
        with pytest.raises(ValidationError):
            auth_web.validate_telegram_user_for_login(session, 111)

        # Blocked user
        blocked = User(name="Blocked", telegram_id=222, status=USER_STATUS_BLOCKED)
        session.add(blocked)
        session.flush()
        with pytest.raises(ValidationError):
            auth_web.validate_telegram_user_for_login(session, 222)

        # Active user
        active = User(name="Active", telegram_id=333, status=USER_STATUS_ACTIVE)
        session.add(active)
        session.flush()
        user_valid = auth_web.validate_telegram_user_for_login(session, 333)
        assert user_valid.id == active.id

        # Create OTP
        otp, code = auth_web.create_login_otp(session, active.id)
        assert len(code) == 6
        assert otp.user_id == active.id
        assert otp.used_at is None

        # Verify OTP
        verified_user = auth_web.verify_login_otp(session, active.id, code)
        assert verified_user.id == active.id
        assert otp.used_at is not None

        # Re-verify should fail
        with pytest.raises(ValidationError):
            auth_web.verify_login_otp(session, active.id, code)
