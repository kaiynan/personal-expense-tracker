"""Route handler autentikasi Web Dashboard (Fase W)."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

import config
from database.engine import session_scope
from services import auth_web
from utils.errors import NotFoundError, ValidationError
from web.deps import get_current_user_optional, get_session_factory, send_telegram_otp

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=str(config.BASE_DIR / "web" / "templates"))


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    """Tampilkan halaman login. Jika sudah login, redirect langsung ke Dashboard."""
    user = get_current_user_optional(request)
    if user is not None:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"user": None},
    )


@router.post("/auth/request-otp", response_class=HTMLResponse)
async def request_otp(
    request: Request,
    telegram_id: Annotated[str, Form()],
):
    """Langkah 1 login: validasi ID Telegram pengguna dan kirimkan kode OTP 6 digit."""
    clean_id = (telegram_id or "").strip()
    if not clean_id or not clean_id.lstrip("-").isdigit():
        return templates.TemplateResponse(
            request=request,
            name="components/toast.html",
            context={
                "toast": {
                    "type": "error",
                    "message": "ID Telegram harus berupa angka bulat positif.",
                }
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    tid = int(clean_id)
    factory = get_session_factory(request)

    def _sync_request_otp():
        with session_scope(factory) as session:
            return auth_web.request_login_otp(session, tid)

    try:
        user, code = await asyncio.to_thread(_sync_request_otp)
    except (NotFoundError, ValidationError) as exc:
        return templates.TemplateResponse(
            request=request,
            name="components/toast.html",
            context={
                "toast": {
                    "type": "error",
                    "message": str(exc),
                }
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:
        logger.exception("Gagal memproses permintaan OTP untuk ID Telegram")
        return templates.TemplateResponse(
            request=request,
            name="components/toast.html",
            context={
                "toast": {
                    "type": "error",
                    "message": f"Terjadi kesalahan sistem: {exc}",
                }
            },
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Kirimkan kode OTP ke chat Telegram (async, di luar transaksi database)
    await send_telegram_otp(request, user.telegram_id, code)

    # Tampilkan form OTP langkah 2
    return templates.TemplateResponse(
        request=request,
        name="components/otp_form.html",
        context={
            "user_id": user.id,
            "telegram_id": user.telegram_id,
            "toast": {
                "type": "success",
                "message": "Kode OTP 6 digit berhasil dikirim ke akun Telegram Anda!",
            },
        },
    )


@router.post("/auth/verify-otp")
async def verify_otp(
    request: Request,
    user_id: Annotated[int, Form()],
    code: Annotated[str, Form()],
    telegram_id: Annotated[int | None, Form()] = None,
):
    """Langkah 2 login: verifikasi kode OTP dan tetapkan sesi login."""
    factory = get_session_factory(request)

    def _sync_verify_otp():
        with session_scope(factory) as session:
            return auth_web.verify_login_otp(session, user_id, code)

    try:
        user = await asyncio.to_thread(_sync_verify_otp)
    except (NotFoundError, ValidationError) as exc:
        return templates.TemplateResponse(
            request=request,
            name="components/otp_form.html",
            context={
                "user_id": user_id,
                "telegram_id": telegram_id or "",
                "error": str(exc),
                "toast": {
                    "type": "error",
                    "message": str(exc),
                },
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:
        logger.exception("Gagal memverifikasi OTP login")
        return templates.TemplateResponse(
            request=request,
            name="components/otp_form.html",
            context={
                "user_id": user_id,
                "telegram_id": telegram_id or "",
                "error": f"Kesalahan sistem: {exc}",
                "toast": {
                    "type": "error",
                    "message": f"Kesalahan sistem: {exc}",
                },
            },
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Tetapkan cookie sesi
    request.session["user_id"] = user.id
    request.session["role"] = user.role
    request.session["name"] = user.name
    request.session["telegram_id"] = user.telegram_id

    # Untuk request HTMX, gunakan header HX-Redirect agar browser dialihkan ke /
    if request.headers.get("HX-Request"):
        response = Response(status_code=status.HTTP_200_OK)
        response.headers["HX-Redirect"] = "/"
        return response

    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/auth/logout")
@router.get("/auth/logout")
def logout(request: Request):
    """Hapus sesi login dan alihkan ke halaman login."""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
