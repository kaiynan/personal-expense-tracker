"""Route handler manajemen anggaran/budget bulanan, kategori, mingguan, dan rollover (Fase W4)."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any

from fastapi import APIRouter, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
from database.engine import session_scope
from models import TYPE_EXPENSE
from services import budgets as budgets_service
from services import categories as categories_service
from utils.dates import format_display, today
from utils.errors import AppError, ValidationError
from utils.formatting import format_rupiah
from web.deps import get_current_user_optional, get_session_factory

logger = logging.getLogger(__name__)

router = APIRouter(tags=["budgets"])
templates = Jinja2Templates(directory=str(config.BASE_DIR / "web" / "templates"))


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


@router.get("/budgets", response_class=HTMLResponse)
async def budgets_page(request: Request, month: str | None = Query(None)):
    """Halaman utama pengelolaan anggaran/budget bulanan, per kategori, dan mingguan."""
    user = get_current_user_optional(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            user_today = today(user.timezone)
            selected_month = month or user_today.strftime("%Y-%m")

            # Evaluasi budget bulanan & kategori
            eval_result = budgets_service.evaluate_budgets(session, user.id, month=selected_month)

            # Evaluasi budget mingguan
            weekly_eval = budgets_service.evaluate_weekly_budgets(session, user.id, on=user_today)

            # Kategori pengeluaran aktif untuk form input
            expense_cats = categories_service.list_categories(
                session, user.id, type_=TYPE_EXPENSE, include_archived=False, include_system=False
            )

            # Format nama bulan
            try:
                y, m = (int(x) for x in selected_month.split("-"))
                month_dt = dt.date(y, m, 1)
                month_display = month_dt.strftime("%B %Y")
            except Exception:
                month_display = selected_month

            return {
                "month_str": selected_month,
                "month_display": month_display,
                "evaluation": eval_result,
                "weekly_evaluation": weekly_eval,
                "expense_categories": expense_cats,
                "format_rupiah": format_rupiah,
                "format_display": format_display,
            }

    data = await asyncio.to_thread(_sync)
    data["request"] = request
    data["user"] = user
    data["active_tab"] = "budgets"

    return templates.TemplateResponse(
        request=request,
        name="budgets.html",
        context=data,
    )


@router.post("/budgets/monthly", response_class=HTMLResponse)
async def set_monthly_budget_endpoint(
    request: Request,
    amount: str = Form(...),
    month: str | None = Form(None),
    is_rollover_enabled: bool = Form(False),
):
    """Atur atau perbarui anggaran bulanan total."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            clean_amt = _clean_amount(amount)
            b = budgets_service.set_monthly_budget(
                session,
                user.id,
                clean_amt,
                month=month,
                is_rollover_enabled=is_rollover_enabled,
            )
            return b.amount, b.month

    try:
        saved_amt, saved_month = await asyncio.to_thread(_sync)
        formatted = format_rupiah(saved_amt)
        toasts = [
            {
                "type": "success",
                "message": f"Anggaran bulanan {saved_month} berhasil diatur: {formatted}",
            }
        ]
        return _render_toast_response(request, toasts, trigger="budgetUpdated")
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal menyimpan anggaran bulanan: %s", exc)
        return _render_toast_response(
            request,
            [{"type": "error", "message": "Gagal menyimpan anggaran bulanan."}],
        )


@router.post("/budgets/category", response_class=HTMLResponse)
async def set_category_budget_endpoint(
    request: Request,
    category_id: int = Form(...),
    amount: str = Form(...),
    month: str | None = Form(None),
    is_rollover_enabled: bool = Form(False),
):
    """Atur atau perbarui anggaran per kategori."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            clean_amt = _clean_amount(amount)
            b = budgets_service.set_category_budget(
                session,
                user.id,
                category_id,
                clean_amt,
                month=month,
                is_rollover_enabled=is_rollover_enabled,
            )
            cat = categories_service.get_category(session, user.id, category_id)
            cat_name = cat.name if cat else "-"
            return b.amount, cat_name, b.month

    try:
        saved_amt, cat_name, saved_month = await asyncio.to_thread(_sync)
        formatted = format_rupiah(saved_amt)
        toasts = [
            {
                "type": "success",
                "message": (
                    f"Anggaran kategori '{cat_name}' ({saved_month}) berhasil diatur: {formatted}"
                ),
            }
        ]
        return _render_toast_response(request, toasts, trigger="budgetUpdated")
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal menyimpan anggaran kategori: %s", exc)
        return _render_toast_response(
            request,
            [{"type": "error", "message": "Gagal menyimpan anggaran kategori."}],
        )


@router.post("/budgets/weekly", response_class=HTMLResponse)
async def set_weekly_budget_endpoint(
    request: Request,
    amount: str = Form(...),
    week_date: str | None = Form(None),
):
    """Atur atau perbarui anggaran mingguan total."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            clean_amt = _clean_amount(amount)
            wb = budgets_service.set_weekly_budget(
                session,
                user.id,
                clean_amt,
                on=week_date,
            )
            return wb.amount, wb.week_start

    try:
        saved_amt, week_start = await asyncio.to_thread(_sync)
        formatted = format_rupiah(saved_amt)
        start_str = format_display(week_start)
        toasts = [
            {
                "type": "success",
                "message": f"Anggaran mingguan (mulai {start_str}) berhasil diatur: {formatted}",
            }
        ]
        return _render_toast_response(request, toasts, trigger="budgetUpdated")
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal menyimpan anggaran mingguan: %s", exc)
        return _render_toast_response(
            request,
            [{"type": "error", "message": "Gagal menyimpan anggaran mingguan."}],
        )


@router.post("/budgets/rollover", response_class=HTMLResponse)
async def rollover_budgets_endpoint(
    request: Request,
    from_month: str | None = Form(None),
    to_month: str | None = Form(None),
):
    """Jalankan rollover sisa budget dari bulan sebelumnya ke bulan tujuan."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            return budgets_service.rollover_budgets(
                session,
                user.id,
                from_month=from_month,
                to_month=to_month,
            )

    try:
        results = await asyncio.to_thread(_sync)
        if not results:
            toasts = [
                {
                    "type": "warning",
                    "message": (
                        "Tidak ada anggaran dengan sisa positif dan rollover aktif yang "
                        "dipindahkan."
                    ),
                }
            ]
        else:
            total_carried = sum(r.surplus_carried_over for r in results)
            toasts = [
                {
                    "type": "success",
                    "message": (
                        f"Rollover berhasil! Total {format_rupiah(total_carried)} sisa anggaran "
                        f"dipindahkan ke bulan tujuan ({len(results)} anggaran)."
                    ),
                }
            ]
        return _render_toast_response(request, toasts, trigger="budgetUpdated")
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal menjalankan rollover anggaran: %s", exc)
        return _render_toast_response(
            request,
            [{"type": "error", "message": "Gagal menjalankan rollover anggaran."}],
        )
