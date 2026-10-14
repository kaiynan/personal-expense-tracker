"""Route handler transaksi, riwayat, transfer, dan modal transaksi Web Dashboard (Fase W3)."""

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
from models import (
    ACTION_DELETE,
    TYPE_ADJUSTMENT,
    TYPE_EXPENSE,
    TYPE_INCOME,
    TYPE_TRANSFER,
    Transaction,
)
from services import audit as audit_service
from services import balances as balances_service
from services import categories as categories_service
from services import transactions as transactions_service
from services import transfers as transfers_service
from utils.dates import format_display, today
from utils.errors import AppError, ValidationError
from utils.formatting import format_rupiah
from web.deps import get_current_user_optional, get_session_factory

logger = logging.getLogger(__name__)

router = APIRouter(tags=["transactions"])
templates = Jinja2Templates(directory=str(config.BASE_DIR / "web" / "templates"))


def _clean_amount(value: Any) -> int:
    """Bersihkan string nominal berformat (misal 'Rp 15.000' atau '15.000') menjadi integer."""
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        cleaned = value.replace("Rp", "").replace(".", "").replace(",", "").strip()
        if cleaned.isdigit() or (cleaned.startswith("-") and cleaned[1:].isdigit()):
            return int(cleaned)
    raise ValidationError("Nominal tidak valid.")


def _enrich_transaction(
    tx: Transaction,
    categories_map: dict[int, str],
    methods_map: dict[int, str],
) -> dict[str, Any]:
    """Bungkus objek Transaction dengan data tampilan yang ramah UI."""
    is_fee = tx.related_transaction_id is not None
    is_adj = tx.type == TYPE_ADJUSTMENT

    cat_name = "-"
    if tx.type == TYPE_TRANSFER:
        cat_name = "Transfer"
    elif is_adj:
        cat_name = "Koreksi Saldo"
    elif tx.category_id in categories_map:
        cat_name = categories_map[tx.category_id]

    method_name = methods_map.get(tx.method_id, f"Metode #{tx.method_id}")
    to_method_name = (
        methods_map.get(tx.to_method_id, f"Metode #{tx.to_method_id}") if tx.to_method_id else None
    )

    amount_abs = abs(tx.amount)
    if tx.type == TYPE_INCOME:
        badge_class = "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300"
        amount_prefix = "+"
        amount_color = "text-emerald-600 dark:text-emerald-400"
    elif tx.type == TYPE_EXPENSE:
        badge_class = "bg-rose-100 text-rose-800 dark:bg-rose-950/70 dark:text-rose-300"
        amount_prefix = "-"
        amount_color = "text-rose-600 dark:text-rose-400"
    elif tx.type == TYPE_TRANSFER:
        badge_class = "bg-blue-100 text-blue-800 dark:bg-blue-950/70 dark:text-blue-300"
        amount_prefix = "⇄ "
        amount_color = "text-blue-600 dark:text-blue-400"
    else:  # adjustment
        badge_class = "bg-purple-100 text-purple-800 dark:bg-purple-950/70 dark:text-purple-300"
        amount_prefix = "+" if tx.amount > 0 else "-"
        amount_color = (
            "text-emerald-600 dark:text-emerald-400"
            if tx.amount > 0
            else "text-rose-600 dark:text-rose-400"
        )

    return {
        "tx": tx,
        "id": tx.id,
        "type": tx.type,
        "date_display": format_display(tx.date),
        "category_name": cat_name,
        "method_name": method_name,
        "to_method_name": to_method_name,
        "amount_display": f"{amount_prefix}{format_rupiah(amount_abs)}",
        "amount_color": amount_color,
        "badge_class": badge_class,
        "note": tx.note or "",
        "is_fee": is_fee,
        "is_adjustment": is_adj,
        "can_edit": (not is_fee) and (not is_adj),
        "can_delete": not is_fee,
    }


def _group_transactions(
    enriched_items: list[dict[str, Any]], user_today: dt.date
) -> list[dict[str, Any]]:
    """Kelompokkan transaksi per tanggal untuk tampilan M-Banking HP."""
    grouped: list[dict[str, Any]] = []
    current_label: str | None = None
    current_group: dict[str, Any] | None = None

    for item in enriched_items:
        tx_date = item["tx"].date
        if tx_date == user_today:
            label = "Hari Ini"
        elif tx_date == user_today - dt.timedelta(days=1):
            label = "Kemarin"
        else:
            label = format_display(tx_date)

        if label != current_label:
            current_label = label
            current_group = {
                "label": label,
                "date": tx_date,
                "date_display": format_display(tx_date),
                "tx_items": [],
            }
            grouped.append(current_group)

        if current_group is not None:
            current_group["tx_items"].append(item)

    return grouped


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


# ==============================================================================
# Halaman Riwayat Transaksi (/transactions & /history)
# ==============================================================================


@router.get("/transactions", response_class=HTMLResponse)
@router.get("/history", response_class=HTMLResponse)
async def transactions_page(request: Request):
    """Halaman riwayat transaksi utama dengan filter dinamis dan tampilan responsif."""
    user = get_current_user_optional(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            user_today = today(user.timezone)
            cats = categories_service.list_categories(
                session, user.id, include_archived=True, include_system=True
            )
            cats_map = {c.id: c.name for c in cats}
            methods_data = balances_service.get_method_balances(session, user.id)
            methods_map = {m.method_id: m.name for m in methods_data}

            txs = transactions_service.list_transactions(session, user.id, limit=50)
            enriched = [_enrich_transaction(tx, cats_map, methods_map) for tx in txs]
            grouped = _group_transactions(enriched, user_today)

            active_cats = [c for c in cats if not c.is_archived and not c.is_system]
            active_methods = [m for m in methods_data if not m.is_archived]

            return {
                "categories": active_cats,
                "methods": active_methods,
                "transactions": enriched,
                "grouped_transactions": grouped,
                "user_today": format_display(user_today),
            }

    data = await asyncio.to_thread(_sync)
    data["request"] = request
    data["user"] = user
    data["active_tab"] = "history"

    return templates.TemplateResponse(
        request=request,
        name="transactions.html",
        context=data,
    )


@router.get("/transactions/list", response_class=HTMLResponse)
async def transactions_list_partial(
    request: Request,
    type: str | None = Query(None),
    category_id: int | None = Query(None),
    method_id: int | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    min_amount: str | None = Query(None),
    max_amount: str | None = Query(None),
    keyword: str | None = Query(None),
):
    """Fragmen daftar transaksi untuk filter interaktif HTMX."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            user_today = today(user.timezone)
            min_val = _clean_amount(min_amount) if min_amount else None
            max_val = _clean_amount(max_amount) if max_amount else None

            cats = categories_service.list_categories(
                session, user.id, include_archived=True, include_system=True
            )
            cats_map = {c.id: c.name for c in cats}
            methods_data = balances_service.get_method_balances(session, user.id)
            methods_map = {m.method_id: m.name for m in methods_data}

            txs = transactions_service.list_transactions(
                session,
                user.id,
                type_=type or None,
                category_id=category_id or None,
                method_id=method_id or None,
                start=start or None,
                end=end or None,
                keyword=keyword or None,
                min_amount=min_val,
                max_amount=max_val,
                limit=100,
            )
            enriched = [_enrich_transaction(tx, cats_map, methods_map) for tx in txs]
            grouped = _group_transactions(enriched, user_today)

            return {
                "transactions": enriched,
                "grouped_transactions": grouped,
            }

    try:
        data = await asyncio.to_thread(_sync)
    except ValidationError as exc:
        toast_html = templates.get_template("components/toast.html").render(
            {
                "request": request,
                "toast": {"type": "error", "message": str(exc)},
            }
        )
        return HTMLResponse(content=toast_html)

    data["request"] = request
    data["user"] = user

    return templates.TemplateResponse(
        request=request,
        name="components/transaction_list.html",
        context=data,
    )


# ==============================================================================
# Modal Form Endpoint (Lazy Loaded via HTMX)
# ==============================================================================


@router.get("/transactions/modal", response_class=HTMLResponse)
async def transaction_modal_partial(request: Request):
    """Ambil fragmen modal catat transaksi (+) dengan data kategori dan metode aktif."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            all_cats = categories_service.list_categories(
                session, user.id, include_archived=False, include_system=False
            )
            expense_cats = [c for c in all_cats if c.type == TYPE_EXPENSE]
            income_cats = [c for c in all_cats if c.type == TYPE_INCOME]
            methods_data = balances_service.get_method_balances(session, user.id)
            active_methods = [m for m in methods_data if not m.is_archived]
            user_today = format_display(today(user.timezone))

            return {
                "expense_categories": expense_cats,
                "income_categories": income_cats,
                "methods": active_methods,
                "today_str": user_today,
            }

    data = await asyncio.to_thread(_sync)
    data["request"] = request
    data["user"] = user

    return templates.TemplateResponse(
        request=request,
        name="components/transaction_modal.html",
        context=data,
    )


# ==============================================================================
# Tambah Transaksi: Pengeluaran, Pemasukan, dan Transfer
# ==============================================================================


@router.post("/transactions/expense", response_class=HTMLResponse)
async def add_expense_endpoint(
    request: Request,
    amount: str = Form(...),
    category_id: int = Form(...),
    method_id: int = Form(...),
    date: str | None = Form(None),
    note: str | None = Form(None),
):
    """Catat pengeluaran baru."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            clean_amt = _clean_amount(amount)
            res = transactions_service.add_expense(
                session=session,
                user_id=user.id,
                amount=clean_amt,
                category_id=category_id,
                method_id=method_id,
                on=date or None,
                note=note or None,
            )
            return res, clean_amt

    try:
        result, clean_amt = await asyncio.to_thread(_sync)
        toasts = [
            {
                "type": "success",
                "message": f"Pengeluaran {format_rupiah(clean_amt)} berhasil dicatat.",
            }
        ]
        for w in result.warnings:
            toasts.append({"type": "warning", "message": w.message})

        return _render_toast_response(request, toasts, trigger="transactionAdded")
    except (AppError, Exception) as exc:
        msg = str(exc) if isinstance(exc, AppError) else "Gagal mencatat pengeluaran."
        return _render_toast_response(request, [{"type": "error", "message": msg}])


@router.post("/transactions/income", response_class=HTMLResponse)
async def add_income_endpoint(
    request: Request,
    amount: str = Form(...),
    category_id: int = Form(...),
    method_id: int = Form(...),
    date: str | None = Form(None),
    note: str | None = Form(None),
):
    """Catat pemasukan baru."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            clean_amt = _clean_amount(amount)
            res = transactions_service.add_income(
                session=session,
                user_id=user.id,
                amount=clean_amt,
                category_id=category_id,
                method_id=method_id,
                on=date or None,
                note=note or None,
            )
            return res, clean_amt

    try:
        result, clean_amt = await asyncio.to_thread(_sync)
        toasts = [
            {
                "type": "success",
                "message": f"Pemasukan {format_rupiah(clean_amt)} berhasil dicatat.",
            }
        ]
        for w in result.warnings:
            toasts.append({"type": "warning", "message": w.message})

        return _render_toast_response(request, toasts, trigger="transactionAdded")
    except (AppError, Exception) as exc:
        msg = str(exc) if isinstance(exc, AppError) else "Gagal mencatat pemasukan."
        return _render_toast_response(request, [{"type": "error", "message": msg}])


@router.post("/transactions/transfer", response_class=HTMLResponse)
async def add_transfer_endpoint(
    request: Request,
    amount: str = Form(...),
    from_method_id: int = Form(...),
    to_method_id: int = Form(...),
    admin_fee: str | None = Form(None),
    date: str | None = Form(None),
    note: str | None = Form(None),
):
    """Catat transfer antar metode pembayaran dengan opsi biaya admin."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    if from_method_id == to_method_id:
        return _render_toast_response(
            request,
            [
                {
                    "type": "error",
                    "message": "Metode asal dan tujuan transfer tidak boleh sama.",
                }
            ],
        )

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            clean_amt = _clean_amount(amount)
            fee = _clean_amount(admin_fee) if admin_fee else 0
            res = transfers_service.add_transfer(
                session=session,
                user_id=user.id,
                amount=clean_amt,
                from_method_id=from_method_id,
                to_method_id=to_method_id,
                on=date or None,
                note=note or None,
                admin_fee=fee,
            )
            return res, clean_amt, fee

    try:
        result, clean_amt, fee = await asyncio.to_thread(_sync)
        msg = f"Transfer {format_rupiah(clean_amt)} berhasil dicatat."
        if fee > 0:
            msg += f" (Biaya admin: {format_rupiah(fee)})"
        toasts = [{"type": "success", "message": msg}]
        for w in result.warnings:
            toasts.append({"type": "warning", "message": w.message})

        return _render_toast_response(request, toasts, trigger="transactionAdded")
    except (AppError, Exception) as exc:
        msg = str(exc) if isinstance(exc, AppError) else "Gagal mencatat transfer."
        return _render_toast_response(request, [{"type": "error", "message": msg}])


# ==============================================================================
# Edit, Delete & Undo Transaksi
# ==============================================================================


@router.get("/transactions/{id}/edit", response_class=HTMLResponse)
async def edit_transaction_modal(request: Request, id: int):
    """Buka modal edit transaksi."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            tx = transactions_service.get_transaction(session, user.id, id)
            if tx.type == TYPE_ADJUSTMENT:
                raise ValidationError(
                    "Koreksi saldo tidak bisa diedit; hapus lalu buat koreksi baru."
                )
            if tx.related_transaction_id is not None:
                raise ValidationError(
                    f"Transaksi #{tx.id} adalah biaya admin yang terhubung ke transfer; "
                    "perubahan harus dilakukan lewat transfernya."
                )

            all_cats = categories_service.list_categories(
                session, user.id, include_archived=True, include_system=False
            )
            cats = [c for c in all_cats if c.type == tx.type]
            methods_data = balances_service.get_method_balances(session, user.id)
            active_methods = [
                m for m in methods_data if not m.is_archived or m.method_id == tx.method_id
            ]

            return {
                "tx": tx,
                "categories": cats,
                "methods": active_methods,
                "date_display": format_display(tx.date),
                "amount_val": tx.amount,
            }

    try:
        data = await asyncio.to_thread(_sync)
    except (AppError, Exception) as exc:
        return _render_toast_response(request, [{"type": "error", "message": str(exc)}])

    data["request"] = request
    data["user"] = user

    return templates.TemplateResponse(
        request=request,
        name="components/transaction_edit_modal.html",
        context=data,
    )


@router.post("/transactions/{id}/edit", response_class=HTMLResponse)
async def edit_transaction_endpoint(
    request: Request,
    id: int,
    amount: str = Form(...),
    date: str | None = Form(None),
    category_id: int | None = Form(None),
    method_id: int | None = Form(None),
    to_method_id: int | None = Form(None),
    note: str | None = Form(None),
):
    """Simpan perubahan transaksi aktif."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            clean_amt = _clean_amount(amount)
            res = transactions_service.edit_transaction(
                session=session,
                user_id=user.id,
                transaction_id=id,
                amount=clean_amt,
                on=date or None,
                category_id=category_id,
                method_id=method_id,
                to_method_id=to_method_id,
                note=note if note is not None else "",
            )
            return res, clean_amt

    try:
        result, clean_amt = await asyncio.to_thread(_sync)
        toasts = [
            {
                "type": "success",
                "message": f"Transaksi #{id} berhasil diperbarui ({format_rupiah(clean_amt)}).",
            }
        ]
        for w in result.warnings:
            toasts.append({"type": "warning", "message": w.message})

        return _render_toast_response(request, toasts, trigger="transactionAdded")
    except (AppError, Exception) as exc:
        msg = str(exc) if isinstance(exc, AppError) else "Gagal memperbarui transaksi."
        return _render_toast_response(request, [{"type": "error", "message": msg}])


@router.post("/transactions/{id}/delete", response_class=HTMLResponse)
async def delete_transaction_endpoint(request: Request, id: int):
    """Soft delete transaksi dan kembalikan toast dengan tombol Undo."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            tx = transactions_service.get_transaction(session, user.id, id)
            if tx.related_transaction_id is not None:
                raise ValidationError(
                    f"Transaksi #{tx.id} adalah biaya admin yang terhubung ke transfer; "
                    "penghapusan harus dilakukan lewat transfernya."
                )
            result = transactions_service.delete_transaction(session, user.id, id)
            logs = audit_service.list_audit_logs(
                session,
                user.id,
                entity_type="transaction",
                entity_id=id,
                action=ACTION_DELETE,
                limit=1,
            )
            audit_log_id = logs[0].id if logs else None
            return result, audit_log_id

    try:
        result, audit_log_id = await asyncio.to_thread(_sync)
        undo_btn_html = ""
        if audit_log_id:
            btn_cls = "ml-2 font-bold underline cursor-pointer hover:text-emerald-700"
            undo_btn_html = (
                f'<button type="button" hx-post="/transactions/undo/{audit_log_id}" '
                f'hx-target="#toast-container" hx-swap="beforeend" '
                f'class="{btn_cls}">Urungkan (Undo)</button>'
            )

        msg = f"Transaksi #{id} berhasil dihapus. {undo_btn_html}"
        toasts = [{"type": "success", "message": msg}]
        for w in result.warnings:
            toasts.append({"type": "warning", "message": w.message})

        return _render_toast_response(request, toasts, trigger="transactionAdded")
    except (AppError, Exception) as exc:
        msg = str(exc) if isinstance(exc, AppError) else "Gagal menghapus transaksi."
        return _render_toast_response(request, [{"type": "error", "message": msg}])


@router.post("/transactions/undo/{audit_log_id}", response_class=HTMLResponse)
async def undo_transaction_endpoint(request: Request, audit_log_id: int):
    """Batalkan perubahan (undo) transaksi berdasarkan audit_log_id."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            return audit_service.undo_action(session, user.id, audit_log_id)

    try:
        result = await asyncio.to_thread(_sync)
        toasts = [
            {
                "type": "success",
                "message": f"Perubahan aksi #{audit_log_id} berhasil dibatalkan (Undo sukses).",
            }
        ]
        for w in result.warnings:
            toasts.append({"type": "warning", "message": w.message})

        return _render_toast_response(request, toasts, trigger="transactionAdded")
    except (AppError, Exception) as exc:
        msg = str(exc) if isinstance(exc, AppError) else "Gagal melakukan undo."
        return _render_toast_response(request, [{"type": "error", "message": msg}])
