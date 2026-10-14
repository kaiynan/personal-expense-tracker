"""Aplikasi FastAPI Web Dashboard dengan lifespan bersama Telegram Bot (Fase W)."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

import config
from bot.app import create_bot_app
from database import queries
from database.engine import create_db_engine, create_session_factory, session_scope
from web.routes import (
    admin,
    auth,
    balances,
    budgets,
    categories,
    charts,
    dashboard,
    recurring,
    reports,
    settings,
    transactions,
)

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=str(config.BASE_DIR / "web" / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Siklus hidup aplikasi FastAPI: menyalakan bot Telegram dan web dalam satu proses."""
    config.ensure_directories()

    # Inisialisasi engine dan session factory jika belum diinjeksi (misal oleh pengujian)
    engine = getattr(app.state, "db_engine", None)
    own_engine = False
    if engine is None:
        engine = create_db_engine()
        app.state.db_engine = engine
        own_engine = True

    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        factory = create_session_factory(engine)
        app.state.session_factory = factory

    # Inisialisasi dan jalankan Telegram Bot + JobQueue pada event loop yang sama
    env_enable_bot = os.getenv("ENABLE_BOT_POLLING", "true").lower() in ("1", "true", "yes")
    should_enable_bot = getattr(app.state, "enable_bot", True) and env_enable_bot
    bot_app = getattr(app.state, "bot_app", None)
    own_bot = False
    if bot_app is None and config.TELEGRAM_BOT_TOKEN:
        try:
            bot_app = create_bot_app(factory)
            app.state.bot_app = bot_app
            own_bot = True
            await bot_app.initialize()
            if should_enable_bot:
                await bot_app.start()
                await bot_app.updater.start_polling(drop_pending_updates=True)
                logger.info(
                    "Telegram Bot & JobQueue berhasil dijalankan berdampingan di lifespan FastAPI."
                )
            else:
                logger.info(
                    "Telegram Bot diinisialisasi mode kirim saja (polling dinonaktifkan)."
                )
        except Exception as exc:
            logger.warning("Gagal menginisialisasi Telegram Bot di lifespan: %s", exc)
    elif bot_app is None:
        logger.info("Bot Telegram dilewati di lifespan (token kosong).")

    try:
        yield
    finally:
        # Hentikan Telegram Bot secara tertib
        if bot_app is not None and own_bot:
            try:
                if bot_app.updater and getattr(bot_app.updater, "running", False):
                    await bot_app.updater.stop()
                if should_enable_bot and getattr(bot_app, "running", False):
                    await bot_app.stop()
                await bot_app.shutdown()
                logger.info("Telegram Bot di lifespan berhasil dihentikan.")
            except Exception as exc:
                logger.warning("Kendala saat menghentikan bot di lifespan: %s", exc)

        # Hentikan engine jika dibuat secara internal
        if own_engine and engine is not None:
            engine.dispose()


def create_web_app(
    factory: Any | None = None,
    bot_app: Any | None = None,
    bot_sender: Any | None = None,
    enable_bot: bool = True,
) -> FastAPI:
    """Pabrik pembuatan instance aplikasi FastAPI."""
    app = FastAPI(
        title=f"{config.APP_NAME} Web Dashboard",
        version=config.APP_VERSION,
        lifespan=lifespan,
    )

    app.state.enable_bot = enable_bot
    # Injeksi dependensi state awal bila disediakan
    if factory is not None:
        app.state.session_factory = factory
    if bot_app is not None:
        app.state.bot_app = bot_app
    if bot_sender is not None:
        app.state.bot_sender = bot_sender

    # Pasang SessionMiddleware untuk signed cookie session
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.SECRET_KEY,
        max_age=86400 * 7,  # 7 hari
        same_site="lax",
    )

    # Healthcheck endpoint untuk Docker / Railway
    @app.get("/health", tags=["system"])
    def healthcheck(response: Response):
        """Pemeriksaan kesehatan sistem untuk probe Railway / Docker."""
        current_factory = getattr(app.state, "session_factory", None)
        if current_factory is None:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {
                "status": "unhealthy",
                "database": "not_configured",
            }

        try:
            with session_scope(current_factory) as session:
                diag = queries.get_database_diagnostics(session)
        except Exception as exc:
            logger.warning("Healthcheck gagal mengakses basis data: %s", exc)
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {
                "status": "unhealthy",
                "database": "error",
                "error": str(exc),
            }

        current_bot = getattr(app.state, "bot_app", None)
        updater = getattr(current_bot, "updater", None) if current_bot else None
        bot_running = bool(updater and getattr(updater, "running", False))
        bot_status = "running" if bot_running else "idle"

        return {
            "status": "healthy",
            "app": config.APP_NAME,
            "version": config.APP_VERSION,
            "database": "connected",
            "db_diagnostics": diag,
            "bot": bot_status,
        }

    # Daftarkan router aplikasi
    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(charts.router)
    app.include_router(transactions.router)
    app.include_router(balances.router)
    app.include_router(budgets.router)
    app.include_router(categories.router)
    app.include_router(recurring.router)
    app.include_router(reports.router)
    app.include_router(settings.router)
    app.include_router(admin.router)

    # Global Exception Handlers
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        is_htmx = request.headers.get("HX-Request") == "true"
        accept = request.headers.get("Accept", "")
        if is_htmx:
            toast_html = templates.get_template("components/toast.html").render(
                {"request": request, "toasts": [{"type": "error", "message": str(exc.detail)}]}
            )
            return HTMLResponse(content=toast_html, status_code=exc.status_code)
        if "text/html" in accept or "*/*" in accept:
            title_map = {
                401: "Autentikasi Diperlukan",
                403: "Akses Ditolak",
                404: "Halaman Tidak Ditemukan",
            }
            title = title_map.get(exc.status_code, "Terjadi Kesalahan")
            return templates.TemplateResponse(
                request=request,
                name="error.html",
                context={
                    "status_code": exc.status_code,
                    "error_title": title,
                    "error_message": exc.detail,
                },
                status_code=exc.status_code,
            )
        return JSONResponse(content={"detail": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        # Catat jalur tanpa membocorkan parameter nominal/deskripsi ke log
        logger.exception(
            "Kesalahan server tak terduga pada endpoint %s %s: %s",
            request.method,
            request.url.path,
            type(exc).__name__,
        )
        is_htmx = request.headers.get("HX-Request") == "true"
        accept = request.headers.get("Accept", "")
        if is_htmx:
            toast_html = templates.get_template("components/toast.html").render(
                {
                    "request": request,
                    "toasts": [
                        {
                            "type": "error",
                            "message": (
                                "Terjadi kesalahan pada sistem. Silakan coba beberapa saat lagi."
                            ),
                        }
                    ],
                }
            )
            return HTMLResponse(
                content=toast_html, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        if "text/html" in accept or "*/*" in accept:
            return templates.TemplateResponse(
                request=request,
                name="error.html",
                context={
                    "status_code": 500,
                    "error_title": "Kesalahan Server Internal",
                    "error_message": (
                        "Maaf, terjadi kendala saat memproses permintaan Anda. "
                        "Silakan coba kembali beberapa saat lagi."
                    ),
                },
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return JSONResponse(
            content={"detail": "Terjadi kesalahan internal pada server."},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return app


# Instance default untuk pemanggilan uvicorn (misal: uvicorn web.app:app)
app = create_web_app()
