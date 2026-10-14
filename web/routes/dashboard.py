"""Route handler dashboard, Quick-Action Bar, dan halaman utama Web Dashboard (Fase W)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
from database.engine import session_scope
from models import TYPE_INCOME, User
from services import aliases as aliases_service
from services import anomalies as anomalies_service
from services import dashboard as dashboard_service
from services import transactions as transactions_service
from utils.dates import format_display, today
from utils.errors import ValidationError
from utils.formatting import format_rupiah
from web.deps import get_current_user_optional, get_session_factory

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory=str(config.BASE_DIR / "web" / "templates"))


def _build_dashboard_context(session: Any, user: User) -> dict[str, Any]:
    """Bangun dictionary konteks lengkap untuk tampilan metrik dan analitik dashboard."""
    summary = dashboard_service.get_dashboard_summary(session, user.id)
    anomalies = anomalies_service.list_anomalous_transactions(session, user.id, limit=3)

    budget_pct = int(summary.budget_percentage) if summary.budget_percentage is not None else 0
    budget_progress_width = min(max(budget_pct, 0), 100)

    # Format method balances
    formatted_methods = []
    for mb in summary.method_balances:
        formatted_methods.append(
            {
                "method_id": mb.method_id,
                "method_name": mb.name,
                "balance": mb.balance,
                "balance_display": format_rupiah(mb.balance),
                "is_archived": mb.is_archived,
            }
        )

    # Format runway
    runway_ctx = {}
    if summary.runway:
        r = summary.runway
        runway_ctx = {
            "daily_burn_rate_display": format_rupiah(r.daily_burn_rate),
            "projected_monthly_spend_display": format_rupiah(r.projected_monthly_spend),
            "recommended_daily_limit_display": (
                format_rupiah(r.recommended_daily_limit)
                if r.recommended_daily_limit is not None
                else None
            ),
            "budget_exhaustion_date_display": (
                format_display(r.budget_exhaustion_date) if r.budget_exhaustion_date else None
            ),
            "balance_exhaustion_date_display": (
                format_display(r.balance_exhaustion_date) if r.balance_exhaustion_date else None
            ),
            "balance_runway_days": r.balance_runway_days,
            "is_balance_exhausted": r.is_balance_exhausted,
            "is_budget_exceeded": r.is_budget_exceeded,
        }

    # Format anomalous items
    formatted_anomalies = []
    for a in anomalies:
        formatted_anomalies.append(
            {
                "id": a.transaction.id,
                "date_display": format_display(a.transaction.date),
                "amount_display": format_rupiah(a.transaction.amount),
                "category_name": (
                    a.transaction.category.name if a.transaction.category else "Tanpa Kategori"
                ),
                "note": a.transaction.note,
                "severity": a.severity,
                "reason": a.reason,
            }
        )

    current_date = today(user.timezone)
    month_names = [
        "Januari",
        "Februari",
        "Maret",
        "April",
        "Mei",
        "Juni",
        "Juli",
        "Agustus",
        "September",
        "Oktober",
        "November",
        "Desember",
    ]
    month_label = f"{month_names[current_date.month - 1]} {current_date.year}"

    return {
        "user": user,
        "active_tab": "dashboard",
        "summary": summary,
        "method_balances": formatted_methods,
        "total_balance_display": format_rupiah(summary.total_balance),
        "total_income_display": format_rupiah(summary.monthly_income),
        "total_expense_display": format_rupiah(summary.monthly_expense),
        "budget_percent": budget_pct,
        "budget_progress_width": budget_progress_width,
        "budget_amount_display": (
            format_rupiah(summary.budget_amount) if summary.budget_amount is not None else None
        ),
        "budget_spent_display": format_rupiah(summary.budget_spent),
        "budget_remaining_display": (
            format_rupiah(summary.budget_remaining)
            if summary.budget_remaining is not None
            else None
        ),
        "runway": runway_ctx,
        "anomalies": formatted_anomalies,
        "current_month_label": month_label,
    }


@router.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Halaman utama Dashboard keuangan."""
    user = get_current_user_optional(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            return _build_dashboard_context(session, user)

    try:
        context = await asyncio.to_thread(_sync)
    except Exception as exc:
        logger.warning("Gagal memuat ringkasan dashboard untuk user %s: %s", user.id, exc)
        context = {
            "user": user,
            "active_tab": "dashboard",
            "summary": None,
            "method_balances": [],
            "total_balance_display": "Rp 0",
            "total_income_display": "Rp 0",
            "total_expense_display": "Rp 0",
            "budget_percent": 0,
            "budget_progress_width": 0,
            "budget_amount_display": None,
            "budget_spent_display": "Rp 0",
            "budget_remaining_display": None,
            "runway": {},
            "anomalies": [],
            "current_month_label": "Bulan Ini",
        }

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=context,
    )


@router.get("/dashboard/summary-cards", response_class=HTMLResponse)
async def dashboard_summary_cards(request: Request):
    """Fragmen HTML kartu ringkasan untuk pembaruan dinamis HTMX."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse(content="", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            return _build_dashboard_context(session, user)

    context = await asyncio.to_thread(_sync)
    context["request"] = request
    return templates.TemplateResponse(
        request=request,
        name="components/summary_cards.html",
        context=context,
    )


# ==============================================================================
# Quick-Action Bar Endpoints (Fase W2)
# ==============================================================================


@router.get("/quick-action/bar", response_class=HTMLResponse)
def quick_action_bar(request: Request):
    """Fragmen default input cepat."""
    return templates.TemplateResponse(
        request=request,
        name="components/quick_action.html",
        context={"request": request},
    )


@router.post("/quick-action/preview", response_class=HTMLResponse)
async def quick_action_preview(request: Request, text: str = Form("")):
    """Proses teks input cepat dan tampilkan kartu konfirmasi pratinjau."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse(
            content="Silakan login terlebih dahulu",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    clean_text = text.strip()
    if not clean_text:
        toast_html = templates.get_template("components/toast.html").render(
            {
                "request": request,
                "toast": {
                    "type": "error",
                    "message": "Teks input cepat tidak boleh kosong.",
                },
            }
        )
        bar_html = templates.get_template("components/quick_action.html").render(
            {"request": request}
        )
        return HTMLResponse(content=bar_html + toast_html)

    factory = get_session_factory(request)

    def _sync_parse():
        with session_scope(factory) as session:
            return aliases_service.parse_quick_input(session, user.id, clean_text)

    try:
        parsed = await asyncio.to_thread(_sync_parse)
        date_display = format_display(today(user.timezone))
        nominal_display = format_rupiah(parsed.amount)

        return templates.TemplateResponse(
            request=request,
            name="components/quick_action_preview.html",
            context={
                "request": request,
                "user": user,
                "parsed": parsed,
                "date_display": date_display,
                "nominal_display": nominal_display,
            },
        )
    except (ValidationError, Exception) as exc:
        err_msg = (
            str(exc) if isinstance(exc, ValidationError) else "Gagal mengurai teks input cepat."
        )
        toast_html = templates.get_template("components/toast.html").render(
            {
                "request": request,
                "toast": {
                    "type": "error",
                    "message": err_msg,
                },
            }
        )
        bar_html = templates.get_template("components/quick_action.html").render(
            {"request": request}
        )
        return HTMLResponse(content=bar_html + toast_html)


@router.post("/quick-action/confirm", response_class=HTMLResponse)
async def quick_action_confirm(
    request: Request,
    type: str = Form(...),
    amount: int = Form(...),
    category_id: int = Form(...),
    method_id: int = Form(...),
    note: str | None = Form(None),
):
    """Simpan transaksi yang telah dikonfirmasi dari Quick-Action Bar."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse(content="Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync_save():
        with session_scope(factory) as session:
            if type == TYPE_INCOME:
                result = transactions_service.add_income(
                    session=session,
                    user_id=user.id,
                    amount=amount,
                    category_id=category_id,
                    method_id=method_id,
                    note=note,
                )
            else:
                result = transactions_service.add_expense(
                    session=session,
                    user_id=user.id,
                    amount=amount,
                    category_id=category_id,
                    method_id=method_id,
                    note=note,
                )
            context = _build_dashboard_context(session, user)
            return result, context

    try:
        result, dash_context = await asyncio.to_thread(_sync_save)

        # Siapkan toast sukses dan toast peringatan (jika ada)
        toasts = []
        action_name = "Pemasukan" if type == TYPE_INCOME else "Pengeluaran"
        toasts.append(
            {
                "type": "success",
                "message": f"Transaksi {action_name} {format_rupiah(amount)} berhasil disimpan.",
            }
        )

        for w in result.warnings:
            toasts.append(
                {
                    "type": "warning",
                    "message": w.message,
                }
            )

        bar_html = templates.get_template("components/quick_action.html").render(
            {"request": request}
        )
        toast_html = templates.get_template("components/toast.html").render(
            {"request": request, "toasts": toasts}
        )
        dash_context["request"] = request
        cards_html = templates.get_template("components/summary_cards.html").render(dash_context)

        return HTMLResponse(
            content=bar_html + toast_html + cards_html,
            headers={"HX-Trigger": "refreshDashboard"},
        )
    except Exception as exc:
        logger.error("Gagal menyimpan transaksi input cepat: %s", exc)
        toast_html = templates.get_template("components/toast.html").render(
            {
                "request": request,
                "toast": {
                    "type": "error",
                    "message": f"Gagal menyimpan transaksi: {exc}",
                },
            }
        )
        bar_html = templates.get_template("components/quick_action.html").render(
            {"request": request}
        )
        return HTMLResponse(content=bar_html + toast_html)


@router.post("/quick-action/cancel", response_class=HTMLResponse)
def quick_action_cancel(request: Request):
    """Batalkan input cepat dan kembalikan ke input bar kosong."""
    return templates.TemplateResponse(
        request=request,
        name="components/quick_action.html",
        context={"request": request},
    )
