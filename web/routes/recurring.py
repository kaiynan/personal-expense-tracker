"""Route handler manajemen transaksi rutin (recurring) (Fase W4)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
from database.engine import session_scope
from models import (
    FREQUENCY_DAILY,
    FREQUENCY_MONTHLY,
    FREQUENCY_WEEKLY,
    FREQUENCY_YEARLY,
    TYPE_EXPENSE,
    TYPE_INCOME,
)
from services import categories as categories_service
from services import recurring as recurring_service
from utils.dates import format_display, today
from utils.errors import AppError, ValidationError
from utils.formatting import format_rupiah
from web.deps import get_current_user_optional, get_session_factory

logger = logging.getLogger(__name__)

router = APIRouter(tags=["recurring"])
templates = Jinja2Templates(directory=str(config.BASE_DIR / "web" / "templates"))

FREQ_LABELS = {
    FREQUENCY_DAILY: "Harian",
    FREQUENCY_WEEKLY: "Mingguan",
    FREQUENCY_MONTHLY: "Bulanan",
    FREQUENCY_YEARLY: "Tahunan",
}


def _clean_amount(value: Any) -> int:
    """Bersihkan string nominal berformat (misal 'Rp 1.500.000' atau '1500000') menjadi integer."""
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        cleaned = value.replace("Rp", "").replace(".", "").replace(",", "").strip()
        if cleaned.isdigit():
            return int(cleaned)
    raise ValidationError("Nominal harus berupa bilangan bulat positif.")


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


@router.get("/recurring", response_class=HTMLResponse)
async def recurring_page(request: Request):
    """Halaman kelola transaksi rutin (daftar jadwal dan form tambah)."""
    user = get_current_user_optional(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            raw_rules = recurring_service.list_recurring(session, user.id)
            all_cats = categories_service.list_categories(
                session, user.id, include_archived=True, include_system=True
            )
            cats_map = {c.id: c.name for c in all_cats}

            all_methods = categories_service.list_payment_methods(
                session, user.id, include_archived=True
            )
            methods_map = {m.id: m.name for m in all_methods}

            enriched_rules = []
            for r in raw_rules:
                next_due = recurring_service.get_next_due_date(session, user.id, r.id)
                enriched_rules.append(
                    {
                        "rule": r,
                        "id": r.id,
                        "name": r.name,
                        "type": r.type,
                        "amount_formatted": format_rupiah(r.amount),
                        "amount": r.amount,
                        "category_name": cats_map.get(r.category_id, "-"),
                        "method_name": methods_map.get(r.method_id, "-"),
                        "frequency": r.frequency,
                        "frequency_label": FREQ_LABELS.get(r.frequency, r.frequency),
                        "day_of_month": r.day_of_month,
                        "start_date_display": format_display(r.start_date),
                        "end_date_display": format_display(r.end_date) if r.end_date else None,
                        "next_due_display": format_display(next_due) if next_due else None,
                        "is_active": r.is_active,
                        "note": r.note or "",
                    }
                )

            active_expense_cats = [
                c
                for c in all_cats
                if c.type == TYPE_EXPENSE and not c.is_archived and not c.is_system
            ]
            active_income_cats = [
                c
                for c in all_cats
                if c.type == TYPE_INCOME and not c.is_archived and not c.is_system
            ]
            active_methods = [m for m in all_methods if not m.is_archived]
            today_str = format_display(today(user.timezone))

            return {
                "recurring_rules": enriched_rules,
                "expense_categories": active_expense_cats,
                "income_categories": active_income_cats,
                "methods": active_methods,
                "today_str": today_str,
            }

    data = await asyncio.to_thread(_sync)
    data["request"] = request
    data["user"] = user
    data["active_tab"] = "recurring"

    return templates.TemplateResponse(
        request=request,
        name="recurring.html",
        context=data,
    )


@router.post("/recurring/add", response_class=HTMLResponse)
async def add_recurring_endpoint(
    request: Request,
    name: str = Form(...),
    type: str = Form(...),
    amount: str = Form(...),
    category_id: int = Form(...),
    method_id: int = Form(...),
    frequency: str = Form(FREQUENCY_MONTHLY),
    day_of_month: int | None = Form(None),
    start_date: str | None = Form(None),
    end_date: str | None = Form(None),
    note: str | None = Form(None),
):
    """Tambah transaksi rutin baru."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            clean_amt = _clean_amount(amount)
            r = recurring_service.create_recurring(
                session,
                user.id,
                name=name,
                type_=type,
                amount=clean_amt,
                category_id=category_id,
                method_id=method_id,
                frequency=frequency,
                day_of_month=day_of_month,
                start_date=start_date if start_date else None,
                end_date=end_date if end_date else None,
                note=note,
            )
            return r.name

    try:
        r_name = await asyncio.to_thread(_sync)
        toasts = [
            {
                "type": "success",
                "message": f"Transaksi rutin '{r_name}' berhasil dijadwalkan.",
            }
        ]
        return _render_toast_response(request, toasts, trigger="recurringUpdated")
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal menambah transaksi rutin: %s", exc)
        return _render_toast_response(
            request, [{"type": "error", "message": "Gagal menambah transaksi rutin."}]
        )


@router.post("/recurring/{recurring_id}/toggle", response_class=HTMLResponse)
async def toggle_recurring_endpoint(request: Request, recurring_id: int):
    """Aktifkan atau nonaktifkan jadwal transaksi rutin."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            r = recurring_service.get_recurring(session, user.id, recurring_id)
            if r is None:
                raise ValidationError("Transaksi rutin tidak ditemukan.")
            if r.is_active:
                updated = recurring_service.deactivate_recurring(session, user.id, recurring_id)
                status_str = "dinonaktifkan"
            else:
                updated = recurring_service.activate_recurring(session, user.id, recurring_id)
                status_str = "diaktifkan"
            return updated.name, status_str

    try:
        r_name, status_str = await asyncio.to_thread(_sync)
        toasts = [
            {
                "type": "success",
                "message": f"Transaksi rutin '{r_name}' berhasil {status_str}.",
            }
        ]
        return _render_toast_response(request, toasts, trigger="recurringUpdated")
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal mengubah status transaksi rutin: %s", exc)
        return _render_toast_response(
            request, [{"type": "error", "message": "Gagal mengubah status transaksi rutin."}]
        )


@router.post("/recurring/{recurring_id}/delete", response_class=HTMLResponse)
async def delete_recurring_endpoint(request: Request, recurring_id: int):
    """Hapus aturan transaksi rutin."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            r = recurring_service.get_recurring(session, user.id, recurring_id)
            r_name = r.name if r else str(recurring_id)
            recurring_service.delete_recurring(session, user.id, recurring_id)
            return r_name

    try:
        r_name = await asyncio.to_thread(_sync)
        toasts = [
            {
                "type": "success",
                "message": f"Transaksi rutin '{r_name}' berhasil dihapus.",
            }
        ]
        return _render_toast_response(request, toasts, trigger="recurringUpdated")
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal menghapus transaksi rutin: %s", exc)
        return _render_toast_response(
            request, [{"type": "error", "message": "Gagal menghapus transaksi rutin."}]
        )
