"""Route handler saldo awal dan koreksi saldo (adjustment) Web Dashboard (Fase W3)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import config
from database.engine import session_scope
from services import adjustments as adjustments_service
from services import balances as balances_service
from services import opening_balances as opening_service
from utils.dates import format_display, today
from utils.errors import ValidationError
from utils.formatting import format_rupiah
from web.deps import get_current_user_optional, get_session_factory

logger = logging.getLogger(__name__)

router = APIRouter(tags=["balances"])
templates = Jinja2Templates(directory=str(config.BASE_DIR / "web" / "templates"))


def _clean_balance(value: Any) -> int:
    """Bersihkan string nominal saldo menjadi integer >= 0."""
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        cleaned = value.replace("Rp", "").replace(".", "").replace(",", "").strip()
        if cleaned.isdigit():
            return int(cleaned)
    raise ValidationError("Nominal saldo harus berupa angka bulat >= 0.")


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


@router.get("/balances/modal", response_class=HTMLResponse)
async def balance_modal_partial(request: Request):
    """Ambil fragmen modal pengaturan Saldo Awal dan Koreksi Saldo."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            methods_data = balances_service.get_method_balances(session, user.id)
            active_methods = [m for m in methods_data if not m.is_archived]
            user_today = format_display(today(user.timezone))
            return {
                "methods": active_methods,
                "today_str": user_today,
            }

    data = await asyncio.to_thread(_sync)
    data["request"] = request
    data["user"] = user

    return templates.TemplateResponse(
        request=request,
        name="components/balance_modal.html",
        context=data,
    )


@router.post("/balances/opening", response_class=HTMLResponse)
async def set_opening_balance_endpoint(
    request: Request,
    method_id: int = Form(...),
    amount: str = Form(...),
    effective_date: str | None = Form(None),
):
    """Atur saldo awal metode pembayaran beserta tanggal berlakunya."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            clean_amt = _clean_balance(amount)
            res = opening_service.set_opening_balance(
                session=session,
                user_id=user.id,
                method_id=method_id,
                amount=clean_amt,
                effective_date=effective_date or None,
            )
            return res, clean_amt

    try:
        result, clean_amt = await asyncio.to_thread(_sync)
        toasts = [
            {
                "type": "success",
                "message": f"Saldo awal {format_rupiah(clean_amt)} berhasil disimpan.",
            }
        ]
        for w in result.warnings:
            toasts.append({"type": "warning", "message": w.message})

        return _render_toast_response(request, toasts, trigger="transactionAdded")
    except (ValidationError, Exception) as exc:
        msg = str(exc) if isinstance(exc, ValidationError) else "Gagal menyimpan saldo awal."
        return _render_toast_response(request, [{"type": "error", "message": msg}])


@router.post("/balances/adjust", response_class=HTMLResponse)
async def adjust_balance_endpoint(
    request: Request,
    method_id: int = Form(...),
    actual_balance: str = Form(...),
    date: str | None = Form(None),
    note: str | None = Form(None),
):
    """Lakukan koreksi saldo (adjustment) terhadap saldo tercatat."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            clean_bal = _clean_balance(actual_balance)
            res = adjustments_service.adjust_balance(
                session=session,
                user_id=user.id,
                method_id=method_id,
                actual_balance=clean_bal,
                on=date or None,
                note=note or None,
            )
            return res, clean_bal

    try:
        result, clean_bal = await asyncio.to_thread(_sync)
        if result.transaction is None:
            toasts = [
                {
                    "type": "success",
                    "message": "Saldo tercatat sudah sesuai dengan saldo sebenarnya.",
                }
            ]
        else:
            diff_display = format_rupiah(abs(result.difference))
            sign = "+" if result.difference > 0 else "-"
            msg = (
                f"Koreksi saldo sebesar {sign}{diff_display} berhasil disimpan "
                f"(Saldo kini: {format_rupiah(clean_bal)})."
            )
            toasts = [{"type": "success", "message": msg}]
        for w in result.warnings:
            toasts.append({"type": "warning", "message": w.message})

        return _render_toast_response(request, toasts, trigger="transactionAdded")
    except (ValidationError, Exception) as exc:
        msg = str(exc) if isinstance(exc, ValidationError) else "Gagal melakukan koreksi saldo."
        # Ditampilkan di Toast Merah sesuai PROJECT_BRIEF
        return _render_toast_response(request, [{"type": "error", "message": msg}])
