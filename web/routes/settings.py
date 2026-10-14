"""Route handler pengaturan pengguna, zona waktu, cek konsistensi saldo,
backup/restore, dan hapus akun (Fase W4).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Form, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
from database.engine import session_scope
from services import backup as backup_service
from services import balances as balances_service
from services import reminders as reminders_service
from services import users as users_service
from utils.errors import AppError, ValidationError
from utils.formatting import format_rupiah
from web.deps import get_current_user_optional, get_session_factory

logger = logging.getLogger(__name__)

router = APIRouter(tags=["settings"])
templates = Jinja2Templates(directory=str(config.BASE_DIR / "web" / "templates"))


def _render_toast_response(
    request: Request,
    toasts: list[dict[str, str]],
    trigger: str | None = None,
    extra_html: str = "",
) -> HTMLResponse:
    """Helper membuat HTMLResponse berisi toast dan header event HTMX opsional."""
    toast_html = templates.get_template("components/toast.html").render(
        {"request": request, "toasts": toasts}
    )
    headers = {}
    if trigger:
        headers["HX-Trigger"] = trigger
    return HTMLResponse(content=extra_html + toast_html, headers=headers)


@router.get("/more", response_class=HTMLResponse)
async def more_page(request: Request):
    """Halaman menu hub navigasi untuk layar ponsel dan akses cepat."""
    user = get_current_user_optional(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(
        request=request,
        name="more.html",
        context={"user": user, "active_tab": "more"},
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Halaman pengaturan profil, zona waktu, notifikasi pengingat, dan manajemen data."""
    user = get_current_user_optional(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            settings_obj = reminders_service.get_user_settings(session, user.id)
            return {
                "timezone": settings_obj.timezone,
                "is_daily_active": settings_obj.is_daily_active,
                "daily_time": settings_obj.daily_time,
            }

    data = await asyncio.to_thread(_sync)
    data["request"] = request
    data["user"] = user
    data["active_tab"] = "settings"

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=data,
    )


# ==============================================================================
# Pengaturan Zona Waktu & Preferensi Pengingat Harian
# ==============================================================================


@router.post("/settings/preferences", response_class=HTMLResponse)
async def update_preferences_endpoint(
    request: Request,
    timezone: str = Form(...),
    daily_time: str = Form("20:00"),
    is_daily_active: bool = Form(False),
):
    """Perbarui preferensi zona waktu dan jam pengingat harian."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            reminders_service.update_user_settings(
                session,
                user.id,
                timezone=timezone,
                daily_time=daily_time,
                is_daily_active=is_daily_active,
            )

    try:
        await asyncio.to_thread(_sync)
        remind_info = daily_time if is_daily_active else "Nonaktif"
        toasts = [
            {
                "type": "success",
                "message": (
                    f"Pengaturan berhasil disimpan. Zona waktu: {timezone}, "
                    f"Pengingat: {remind_info}."
                ),
            }
        ]
        return _render_toast_response(request, toasts)
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal menyimpan preferensi pengguna: %s", exc)
        return _render_toast_response(
            request, [{"type": "error", "message": "Gagal menyimpan preferensi pengguna."}]
        )


# ==============================================================================
# Cek Konsistensi Saldo
# ==============================================================================


@router.post("/settings/check-consistency", response_class=HTMLResponse)
async def check_consistency_endpoint(request: Request):
    """Jalankan audit konsistensi saldo antara compute_balance dan agregasi mutasi mentah."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            return balances_service.check_balance_consistency(session, user.id)

    try:
        results = await asyncio.to_thread(_sync)
        all_consistent = all(item.is_consistent for item in results)

        # Render fragmen hasil audit
        res_html = templates.get_template("components/consistency_table.html").render(
            {
                "results": results,
                "all_consistent": all_consistent,
                "format_rupiah": format_rupiah,
            }
        )

        toasts = [
            {
                "type": "success" if all_consistent else "warning",
                "message": (
                    "Seluruh saldo metode 100% konsisten dengan data mutasi mentah."
                    if all_consistent
                    else "Ditemukan ketidakkonsistenan saldo pada salah satu metode pembayaran!"
                ),
            }
        ]
        return _render_toast_response(request, toasts, extra_html=res_html)
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal memeriksa konsistensi saldo: %s", exc)
        return _render_toast_response(
            request, [{"type": "error", "message": "Gagal memeriksa konsistensi saldo."}]
        )


# ==============================================================================
# Backup & Restore Data Pengguna
# ==============================================================================


@router.get("/settings/backup/download")
async def download_backup_endpoint(request: Request):
    """Unduh cadangan data lengkap pengguna dalam arsip ZIP (JSON + CSVs)."""
    user = get_current_user_optional(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            return backup_service.export_user_backup_zip(session, user.id)

    zip_path = await asyncio.to_thread(_sync)
    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        filename=zip_path.name,
    )


@router.post("/settings/backup/restore", response_class=HTMLResponse)
async def restore_backup_endpoint(
    request: Request,
    file: UploadFile,
):
    """Pulihkan data keuangan pengguna dari file cadangan ZIP atau JSON."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)
    contents = await file.read()
    if not contents:
        return _render_toast_response(
            request, [{"type": "error", "message": "File cadangan kosong atau tidak valid."}]
        )

    def _sync():
        with session_scope(factory) as session:
            # restore_backup memvalidasi struktur & checksum, dan hanya memulihkan user_id ini
            return backup_service.restore_backup(session, user.id, contents)

    try:
        summary = await asyncio.to_thread(_sync)
        tx_count = summary.get("transactions", 0)
        cat_count = summary.get("categories", 0)
        toasts = [
            {
                "type": "success",
                "message": (
                    f"Data berhasil dipulihkan dari cadangan! "
                    f"({tx_count} transaksi, {cat_count} kategori)."
                ),
            }
        ]
        return _render_toast_response(request, toasts, trigger="budgetUpdated")
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal memulihkan cadangan: %s", exc)
        return _render_toast_response(
            request,
            [{"type": "error", "message": "Gagal memulihkan cadangan. Pastikan file valid."}],
        )


# ==============================================================================
# Hapus Akun Pengguna (Multi-Step Confirmation)
# ==============================================================================


@router.post("/settings/account/delete", response_class=HTMLResponse)
async def delete_account_endpoint(request: Request, confirm_text: str = Form(...)):
    """Hapus akun pengguna dan seluruh datanya secara permanen setelah konfirmasi."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    if confirm_text.strip().upper() not in ("HAPUS", "DELETE"):
        return _render_toast_response(
            request,
            [
                {
                    "type": "error",
                    "message": (
                        "Ketik kata 'HAPUS' dengan tepat untuk mengonfirmasi penghapusan akun."
                    ),
                }
            ],
        )

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            users_service.delete_user_account(session, user.id)

    try:
        await asyncio.to_thread(_sync)
        request.session.clear()
        # Redirect ke halaman login dengan header HTMX
        return HTMLResponse(
            content="",
            headers={"HX-Redirect": "/login"},
        )
    except Exception as exc:
        logger.exception("Gagal menghapus akun pengguna: %s", exc)
        return _render_toast_response(
            request,
            [{"type": "error", "message": "Gagal menghapus akun. Silakan coba kembali."}],
        )
