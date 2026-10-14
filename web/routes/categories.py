"""Route handler manajemen kategori kustom, metode pembayaran, dan alias input cepat (Fase W4)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
from database.engine import session_scope
from models import TARGET_CATEGORY, TARGET_PAYMENT_METHOD, TYPE_EXPENSE, TYPE_INCOME
from services import aliases as aliases_service
from services import categories as categories_service
from utils.errors import AppError, ValidationError
from web.deps import get_current_user_optional, get_session_factory

logger = logging.getLogger(__name__)

router = APIRouter(tags=["categories"])
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


@router.get("/categories", response_class=HTMLResponse)
async def categories_page(request: Request):
    """Halaman kelola kategori kustom, metode pembayaran, dan alias input cepat."""
    user = get_current_user_optional(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            all_cats = categories_service.list_categories(
                session, user.id, include_archived=True, include_system=True
            )
            expense_cats = [c for c in all_cats if c.type == TYPE_EXPENSE and not c.is_archived]
            income_cats = [c for c in all_cats if c.type == TYPE_INCOME and not c.is_archived]
            archived_cats = [c for c in all_cats if c.is_archived]

            all_methods = categories_service.list_payment_methods(
                session, user.id, include_archived=True
            )
            active_methods = [m for m in all_methods if not m.is_archived]
            archived_methods = [m for m in all_methods if m.is_archived]

            aliases = aliases_service.list_quick_aliases(session, user.id)

            return {
                "expense_categories": expense_cats,
                "income_categories": income_cats,
                "archived_categories": archived_cats,
                "active_methods": active_methods,
                "archived_methods": archived_methods,
                "aliases": aliases,
                "all_active_cats": [c for c in all_cats if not c.is_archived and not c.is_system],
            }

    data = await asyncio.to_thread(_sync)
    data["request"] = request
    data["user"] = user
    data["active_tab"] = "categories"

    return templates.TemplateResponse(
        request=request,
        name="categories.html",
        context=data,
    )


# ==============================================================================
# Kategori Transaksi
# ==============================================================================


@router.post("/categories/add", response_class=HTMLResponse)
async def add_category_endpoint(
    request: Request,
    name: str = Form(...),
    type: str = Form(...),
):
    """Tambah kategori baru milik pengguna."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            cat = categories_service.add_category(session, user.id, name, type)
            return cat.name, cat.type

    try:
        cat_name, cat_type = await asyncio.to_thread(_sync)
        type_label = "Pemasukan" if cat_type == TYPE_INCOME else "Pengeluaran"
        toasts = [
            {
                "type": "success",
                "message": f"Kategori {type_label} '{cat_name}' berhasil ditambahkan.",
            }
        ]
        return _render_toast_response(request, toasts, trigger="categoryUpdated")
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal menambah kategori: %s", exc)
        return _render_toast_response(
            request, [{"type": "error", "message": "Gagal menambah kategori."}]
        )


@router.post("/categories/{category_id}/rename", response_class=HTMLResponse)
async def rename_category_endpoint(
    request: Request,
    category_id: int,
    new_name: str = Form(...),
):
    """Ubah nama kategori milik pengguna."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            cat = categories_service.rename_category(session, user.id, category_id, new_name)
            return cat.name

    try:
        cat_name = await asyncio.to_thread(_sync)
        toasts = [
            {
                "type": "success",
                "message": f"Kategori berhasil diubah namanya menjadi '{cat_name}'.",
            }
        ]
        return _render_toast_response(request, toasts, trigger="categoryUpdated")
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal mengubah nama kategori: %s", exc)
        return _render_toast_response(
            request, [{"type": "error", "message": "Gagal mengubah nama kategori."}]
        )


@router.post("/categories/{category_id}/archive", response_class=HTMLResponse)
async def archive_category_endpoint(request: Request, category_id: int):
    """Arsipkan kategori milik pengguna (kategori sistem ditolak)."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            cat = categories_service.archive_category(session, user.id, category_id)
            return cat.name

    try:
        cat_name = await asyncio.to_thread(_sync)
        toasts = [
            {
                "type": "success",
                "message": f"Kategori '{cat_name}' berhasil diarsipkan.",
            }
        ]
        return _render_toast_response(request, toasts, trigger="categoryUpdated")
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal mengarsipkan kategori: %s", exc)
        return _render_toast_response(
            request, [{"type": "error", "message": "Gagal mengarsipkan kategori."}]
        )


@router.post("/categories/{category_id}/unarchive", response_class=HTMLResponse)
async def unarchive_category_endpoint(request: Request, category_id: int):
    """Pulihkan kategori yang diarsipkan."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            cat = categories_service.unarchive_category(session, user.id, category_id)
            return cat.name

    try:
        cat_name = await asyncio.to_thread(_sync)
        toasts = [
            {
                "type": "success",
                "message": f"Kategori '{cat_name}' berhasil dipulihkan dari arsip.",
            }
        ]
        return _render_toast_response(request, toasts, trigger="categoryUpdated")
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal memulihkan kategori: %s", exc)
        return _render_toast_response(
            request, [{"type": "error", "message": "Gagal memulihkan kategori."}]
        )


# ==============================================================================
# Metode Pembayaran
# ==============================================================================


@router.post("/methods/add", response_class=HTMLResponse)
async def add_method_endpoint(request: Request, name: str = Form(...)):
    """Tambah metode pembayaran baru milik pengguna."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            m = categories_service.add_payment_method(session, user.id, name)
            return m.name

    try:
        m_name = await asyncio.to_thread(_sync)
        toasts = [
            {
                "type": "success",
                "message": f"Metode pembayaran '{m_name}' berhasil ditambahkan.",
            }
        ]
        return _render_toast_response(request, toasts, trigger="categoryUpdated")
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal menambah metode pembayaran: %s", exc)
        return _render_toast_response(
            request, [{"type": "error", "message": "Gagal menambah metode pembayaran."}]
        )


@router.post("/methods/{method_id}/rename", response_class=HTMLResponse)
async def rename_method_endpoint(request: Request, method_id: int, new_name: str = Form(...)):
    """Ubah nama metode pembayaran milik pengguna."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            m = categories_service.rename_payment_method(session, user.id, method_id, new_name)
            return m.name

    try:
        m_name = await asyncio.to_thread(_sync)
        toasts = [
            {
                "type": "success",
                "message": f"Metode pembayaran berhasil diubah namanya menjadi '{m_name}'.",
            }
        ]
        return _render_toast_response(request, toasts, trigger="categoryUpdated")
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal mengubah nama metode pembayaran: %s", exc)
        return _render_toast_response(
            request, [{"type": "error", "message": "Gagal mengubah nama metode pembayaran."}]
        )


@router.post("/methods/{method_id}/archive", response_class=HTMLResponse)
async def archive_method_endpoint(request: Request, method_id: int):
    """Arsipkan metode pembayaran milik pengguna."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            m = categories_service.archive_payment_method(session, user.id, method_id)
            return m.name

    try:
        m_name = await asyncio.to_thread(_sync)
        toasts = [
            {
                "type": "success",
                "message": f"Metode pembayaran '{m_name}' berhasil diarsipkan.",
            }
        ]
        return _render_toast_response(request, toasts, trigger="categoryUpdated")
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal mengarsipkan metode pembayaran: %s", exc)
        return _render_toast_response(
            request, [{"type": "error", "message": "Gagal mengarsipkan metode pembayaran."}]
        )


@router.post("/methods/{method_id}/unarchive", response_class=HTMLResponse)
async def unarchive_method_endpoint(request: Request, method_id: int):
    """Pulihkan metode pembayaran yang diarsipkan."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            m = categories_service.unarchive_payment_method(session, user.id, method_id)
            return m.name

    try:
        m_name = await asyncio.to_thread(_sync)
        toasts = [
            {
                "type": "success",
                "message": f"Metode pembayaran '{m_name}' berhasil dipulihkan dari arsip.",
            }
        ]
        return _render_toast_response(request, toasts, trigger="categoryUpdated")
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal memulihkan metode pembayaran: %s", exc)
        return _render_toast_response(
            request, [{"type": "error", "message": "Gagal memulihkan metode pembayaran."}]
        )


# ==============================================================================
# Alias Input Cepat & Aturan Kategori
# ==============================================================================


@router.post("/aliases/add", response_class=HTMLResponse)
async def add_alias_endpoint(
    request: Request,
    alias: str = Form(...),
    target_type: str = Form(...),
    target_id: int = Form(...),
):
    """Tambah alias input cepat baru untuk kategori atau metode pembayaran."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            clean_type = TARGET_CATEGORY if target_type == "category" else TARGET_PAYMENT_METHOD
            qa = aliases_service.add_alias(session, user.id, alias, clean_type, target_id)
            return qa.alias

    try:
        alias_text = await asyncio.to_thread(_sync)
        toasts = [
            {
                "type": "success",
                "message": f"Alias input cepat '{alias_text}' berhasil didaftarkan.",
            }
        ]
        return _render_toast_response(request, toasts, trigger="categoryUpdated")
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal menambah alias: %s", exc)
        return _render_toast_response(
            request, [{"type": "error", "message": "Gagal menambah alias."}]
        )


@router.post("/aliases/{alias_id}/delete", response_class=HTMLResponse)
async def delete_alias_endpoint(request: Request, alias_id: int):
    """Hapus alias input cepat."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            qa = aliases_service.get_alias(session, user.id, alias_id)
            alias_name = qa.alias if qa else str(alias_id)
            aliases_service.delete_alias(session, user.id, alias_id)
            return alias_name

    try:
        alias_name = await asyncio.to_thread(_sync)
        toasts = [
            {
                "type": "success",
                "message": f"Alias '{alias_name}' berhasil dihapus.",
            }
        ]
        return _render_toast_response(request, toasts, trigger="categoryUpdated")
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal menghapus alias: %s", exc)
        return _render_toast_response(
            request, [{"type": "error", "message": "Gagal menghapus alias."}]
        )
