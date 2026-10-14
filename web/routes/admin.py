"""Route handler panel khusus Administrator (Fase W5).

Menyediakan:
- Dashboard status operasional sistem & basis data SQLite.
- Manajemen pengguna: persetujuan (approve) & pemblokiran (block).
- Manajemen kode undangan (invites): pembuatan dan daftar status.
- Unduh cadangan sistem penuh (full admin backup ZIP).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import platform
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
from database import queries
from database.engine import session_scope
from models import (
    USER_STATUS_ACTIVE,
    USER_STATUS_BLOCKED,
    USER_STATUS_PENDING,
)
from services import backup as backup_service
from services import users as users_service
from utils.errors import AppError, ValidationError
from web.deps import get_session_factory, require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])
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


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    """Halaman panel kendali administrator."""
    try:
        user = require_admin(request)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
        raise exc

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            # 1. Diagnostik SQLite & File DB
            diag = queries.get_database_diagnostics(session)
            db_path = Path(config.DB_PATH)
            db_size_kb = (db_path.stat().st_size / 1024) if db_path.is_file() else 0.0

            # 2. Pengguna & Ringkasan Status
            all_users = users_service.list_users(session, user.id)
            active_users_count = sum(1 for u in all_users if u.status == USER_STATUS_ACTIVE)
            pending_users_count = sum(1 for u in all_users if u.status == USER_STATUS_PENDING)
            blocked_users_count = sum(1 for u in all_users if u.status == USER_STATUS_BLOCKED)

            # 3. Metrik Entitas
            total_tx = queries.count_all_transactions(session)
            total_budgets = queries.count_all_budgets(session)
            total_rec = queries.count_all_recurring(session)

            # 4. Daftar Undangan
            all_invites = users_service.list_invites(session, user.id, include_used=True)

            # 5. File Backup Terbaru
            backups = []
            if config.BACKUPS_DIR.is_dir():
                backups = sorted(
                    list(config.BACKUPS_DIR.glob("*.db"))
                    + list(config.BACKUPS_DIR.glob("*.zip"))
                    + list(config.BACKUPS_DIR.glob("*.json")),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
            latest_backup_name = backups[0].name if backups else None
            latest_backup_size = f"{backups[0].stat().st_size / 1024:.1f} KB" if backups else None

            # Enrich invites
            enriched_invites = []
            now_utc = dt.datetime.now(dt.UTC).replace(tzinfo=None)
            for inv in all_invites:
                is_expired = inv.expires_at is not None and inv.expires_at < now_utc
                status_label = (
                    "Terpakai" if inv.is_used else ("Kedaluwarsa" if is_expired else "Aktif")
                )
                enriched_invites.append(
                    {
                        "id": inv.id,
                        "code": inv.code,
                        "is_used": inv.is_used,
                        "is_expired": is_expired,
                        "status_label": status_label,
                        "created_at_display": (
                            inv.created_at.strftime("%d-%m-%Y %H:%M") if inv.created_at else "-"
                        ),
                        "expires_at_display": (
                            inv.expires_at.strftime("%d-%m-%Y %H:%M")
                            if inv.expires_at
                            else "Selamanya"
                        ),
                        "used_at_display": (
                            inv.used_at.strftime("%d-%m-%Y %H:%M") if inv.used_at else "-"
                        ),
                        "used_by_id": inv.used_by_user_id,
                    }
                )

            # Enrich users
            enriched_users = []
            for u in all_users:
                enriched_users.append(
                    {
                        "id": u.id,
                        "name": u.name,
                        "telegram_id": u.telegram_id,
                        "role": u.role,
                        "status": u.status,
                        "timezone": u.timezone,
                        "created_at_display": (
                            u.created_at.strftime("%d-%m-%Y") if u.created_at else "-"
                        ),
                    }
                )

            now_str = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            return {
                "system_status": {
                    "time": now_str,
                    "timezone": config.TIMEZONE_NAME,
                    "python_version": platform.python_version(),
                    "platform": f"{platform.system()} {platform.machine()}",
                    "db_path": config.DB_PATH,
                    "db_size_kb": f"{db_size_kb:.1f} KB",
                    "integrity": diag.get("integrity", "ok"),
                    "journal_mode": diag.get("journal_mode", "wal"),
                    "latest_backup_name": latest_backup_name,
                    "latest_backup_size": latest_backup_size,
                },
                "metrics": {
                    "total_users": len(all_users),
                    "active_users": active_users_count,
                    "pending_users": pending_users_count,
                    "blocked_users": blocked_users_count,
                    "total_transactions": total_tx,
                    "total_budgets": total_budgets,
                    "total_recurring": total_rec,
                },
                "users": enriched_users,
                "invites": enriched_invites,
            }

    data = await asyncio.to_thread(_sync)
    data["request"] = request
    data["user"] = user
    data["active_tab"] = "admin"

    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context=data,
    )


@router.post("/users/{target_id}/approve", response_class=HTMLResponse)
async def approve_user_endpoint(request: Request, target_id: int):
    """Setujui akun pengguna berstatus pending menjadi active."""
    user = require_admin(request)
    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            target = users_service.approve_user(session, user.id, target_id)
            return target.name

    try:
        target_name = await asyncio.to_thread(_sync)
        toasts = [
            {
                "type": "success",
                "message": (
                    f"Pengguna '{target_name}' (ID #{target_id}) berhasil disetujui & diaktifkan."
                ),
            }
        ]
        return _render_toast_response(request, toasts, trigger="adminUpdated")
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal menyetujui pengguna: %s", exc)
        return _render_toast_response(
            request, [{"type": "error", "message": "Gagal menyetujui pengguna."}]
        )


@router.post("/users/{target_id}/block", response_class=HTMLResponse)
async def block_user_endpoint(request: Request, target_id: int):
    """Blokir akun pengguna."""
    user = require_admin(request)
    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            target = users_service.block_user(session, user.id, target_id)
            return target.name

    try:
        target_name = await asyncio.to_thread(_sync)
        toasts = [
            {
                "type": "warning",
                "message": f"Akses pengguna '{target_name}' (ID #{target_id}) berhasil diblokir.",
            }
        ]
        return _render_toast_response(request, toasts, trigger="adminUpdated")
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal memblokir pengguna: %s", exc)
        return _render_toast_response(
            request, [{"type": "error", "message": "Gagal memblokir pengguna."}]
        )


@router.post("/invites/create", response_class=HTMLResponse)
async def create_invite_endpoint(
    request: Request,
    custom_code: str | None = Form(None),
    expires_in_days: int | None = Form(None),
):
    """Buat kode undangan pendaftaran baru."""
    user = require_admin(request)
    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            clean_code = custom_code.strip() if custom_code and custom_code.strip() else None
            exp_days = (
                int(expires_in_days)
                if expires_in_days is not None and int(expires_in_days) > 0
                else None
            )
            inv = users_service.create_invite(
                session, user.id, code=clean_code, expires_in_days=exp_days
            )
            return inv.code

    try:
        invite_code = await asyncio.to_thread(_sync)
        toasts = [
            {
                "type": "success",
                "message": f"Kode undangan '{invite_code}' berhasil dibuat!",
            }
        ]
        return _render_toast_response(request, toasts, trigger="adminUpdated")
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal membuat kode undangan: %s", exc)
        return _render_toast_response(
            request, [{"type": "error", "message": "Gagal membuat kode undangan."}]
        )


@router.get("/backup/download")
async def download_admin_full_backup(request: Request):
    """Unduh cadangan penuh seluruh database sistem (khusus admin)."""
    user = require_admin(request)
    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            return backup_service.export_full_admin_backup(session, user.id)

    try:
        zip_path = await asyncio.to_thread(_sync)
        filename = Path(zip_path).name
        return FileResponse(
            path=str(zip_path),
            filename=filename,
            media_type="application/zip",
        )
    except Exception as exc:
        logger.exception("Gagal menghasilkan cadangan sistem admin: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gagal menghasilkan cadangan sistem admin.",
        ) from exc
