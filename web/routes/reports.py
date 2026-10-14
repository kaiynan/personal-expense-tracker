"""Route handler laporan keuangan, ekspor CSV/Excel/PDF, dan impor mutasi CSV (Fase W4)."""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
import logging
from typing import Any

from fastapi import APIRouter, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
from database import queries
from database.engine import session_scope
from models import TYPE_EXPENSE, TYPE_INCOME
from reports import financial as financial_reports
from services import aliases as aliases_service
from services import categories as categories_service
from services import export as export_service
from services import transactions as transactions_service
from utils.dates import format_display, today
from utils.errors import AppError, ValidationError
from utils.formatting import format_rupiah
from utils.validators import parse_date
from web.deps import get_current_user_optional, get_session_factory

logger = logging.getLogger(__name__)

router = APIRouter(tags=["reports"])
templates = Jinja2Templates(directory=str(config.BASE_DIR / "web" / "templates"))


def _clean_amount(value: Any) -> int:
    """Bersihkan string nominal berformat menjadi integer rupiah."""
    if isinstance(value, (int, float)):
        return abs(int(value))
    if isinstance(value, str):
        cleaned = value.replace("Rp", "").replace(".", "").replace(",", "").strip()
        if cleaned.isdigit() or (cleaned.startswith("-") and cleaned[1:].isdigit()):
            return abs(int(cleaned))
    raise ValidationError("Nominal tidak valid.")


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


@router.get("/reports", response_class=HTMLResponse)
@router.get("/analytics", response_class=HTMLResponse)
async def reports_page(
    request: Request,
    period: str = Query("monthly"),
    date_str: str | None = Query(None),
):
    """Halaman laporan keuangan harian, mingguan, bulanan, perbandingan periode,
    dan alat ekspor/impor.
    """
    user = get_current_user_optional(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            user_today = today(user.timezone)
            ref_date = parse_date(date_str, "Tanggal patokan") if date_str else user_today

            # Laporan sesuai periode
            if period == "daily":
                rep = financial_reports.get_daily_report(session, user.id, on=ref_date)
                comp = None
            elif period == "weekly":
                rep = financial_reports.get_weekly_report(session, user.id, on=ref_date)
                comp = None
            elif period == "comparison":
                cur_month = ref_date.strftime("%Y-%m")
                rep = financial_reports.get_monthly_report(session, user.id, month=cur_month)
                comp = financial_reports.get_monthly_comparison(session, user.id, month2=cur_month)
            else:  # monthly (default)
                cur_month = ref_date.strftime("%Y-%m")
                rep = financial_reports.get_monthly_report(session, user.id, month=cur_month)
                comp = None

            # Kategori & metode untuk modal impor
            all_cats = categories_service.list_categories(
                session, user.id, include_archived=False, include_system=False
            )
            all_methods = categories_service.list_payment_methods(
                session, user.id, include_archived=False
            )

            return {
                "period": period,
                "report": rep,
                "comparison": comp,
                "ref_date": ref_date,
                "ref_date_display": format_display(ref_date),
                "categories": all_cats,
                "methods": all_methods,
                "format_rupiah": format_rupiah,
                "format_display": format_display,
            }

    data = await asyncio.to_thread(_sync)
    data["request"] = request
    data["user"] = user
    data["active_tab"] = "reports"

    return templates.TemplateResponse(
        request=request,
        name="reports.html",
        context=data,
    )


# ==============================================================================
# Ekspor Data: CSV, Excel (.xlsx), PDF
# ==============================================================================


@router.get("/reports/export/csv")
async def export_csv_endpoint(
    request: Request,
    start: str | None = Query(None),
    end: str | None = Query(None),
):
    """Unduh data transaksi dalam format CSV (dilindungi dari formula injection)."""
    user = get_current_user_optional(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            return export_service.export_csv(session, user.id, start=start, end=end)

    file_path = await asyncio.to_thread(_sync)
    return FileResponse(
        path=str(file_path),
        media_type="text/csv",
        filename=file_path.name,
    )


@router.get("/reports/export/excel")
async def export_excel_endpoint(
    request: Request,
    start: str | None = Query(None),
    end: str | None = Query(None),
):
    """Unduh laporan keuangan dan mutasi transaksi dalam format Excel (.xlsx)."""
    user = get_current_user_optional(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            return export_service.export_excel(session, user.id, start=start, end=end)

    file_path = await asyncio.to_thread(_sync)
    return FileResponse(
        path=str(file_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=file_path.name,
    )


@router.get("/reports/export/pdf")
async def export_pdf_endpoint(
    request: Request,
    start: str | None = Query(None),
    end: str | None = Query(None),
):
    """Unduh ringkasan laporan keuangan dalam format PDF."""
    user = get_current_user_optional(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            return export_service.export_pdf(session, user.id, start=start, end=end)

    file_path = await asyncio.to_thread(_sync)
    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        filename=file_path.name,
    )


# ==============================================================================
# Impor CSV / Mutasi dengan Deteksi Duplikat dan Pratinjau Kategori
# ==============================================================================


def _parse_csv_date(date_text: str) -> dt.date | None:
    """Konversi tanggal teks berbagai format umum ke objek date."""
    clean = date_text.strip().replace("/", "-")
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d-%m-%y"):
        try:
            return dt.datetime.strptime(clean, fmt).date()
        except ValueError:
            pass
    return None


@router.post("/reports/import/preview", response_class=HTMLResponse)
async def import_preview_endpoint(
    request: Request,
    file: UploadFile,
):
    """Unggah dan parsing CSV, deteksi baris duplikat, dan tampilkan pratinjau konfirmasi."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    factory = get_session_factory(request)
    contents = await file.read()
    if not contents:
        return _render_toast_response(
            request, [{"type": "error", "message": "File CSV kosong atau tidak terbaca."}]
        )

    # Deteksi encoding teks
    try:
        text_data = contents.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text_data = contents.decode("latin-1")
        except Exception:
            return _render_toast_response(
                request, [{"type": "error", "message": "Format encoding file CSV tidak didukung."}]
            )

    def _sync():
        with session_scope(factory) as session:
            reader = csv.reader(io.StringIO(text_data))
            rows = list(reader)
            if not rows or len(rows) < 2:
                raise ValidationError(
                    "File CSV minimal harus memiliki baris judul dan 1 baris data."
                )

            header = [col.strip().lower() for col in rows[0]]
            data_rows = rows[1:]

            # Temukan indeks kolom dengan toleransi nama variatif
            date_idx = next(
                (
                    i
                    for i, h in enumerate(header)
                    if any(k in h for k in ("tanggal", "date", "tgl"))
                ),
                0,
            )
            type_idx = next(
                (
                    i
                    for i, h in enumerate(header)
                    if any(k in h for k in ("jenis", "type", "tipe", "d/k"))
                ),
                -1,
            )
            cat_idx = next(
                (
                    i
                    for i, h in enumerate(header)
                    if any(k in h for k in ("kategori", "category", "pos"))
                ),
                -1,
            )
            method_idx = next(
                (
                    i
                    for i, h in enumerate(header)
                    if any(k in h for k in ("metode", "method", "rekening", "akun"))
                ),
                -1,
            )
            amount_idx = next(
                (
                    i
                    for i, h in enumerate(header)
                    if any(k in h for k in ("nominal", "amount", "jumlah"))
                ),
                -1,
            )
            note_idx = next(
                (
                    i
                    for i, h in enumerate(header)
                    if any(k in h for k in ("keterangan", "note", "deskripsi", "uraian"))
                ),
                -1,
            )

            all_cats = categories_service.list_categories(
                session, user.id, include_archived=False, include_system=False
            )
            exp_cats = [c for c in all_cats if c.type == TYPE_EXPENSE]
            inc_cats = [c for c in all_cats if c.type == TYPE_INCOME]
            methods = categories_service.list_payment_methods(
                session, user.id, include_archived=False
            )
            if not methods:
                raise ValidationError("Pengguna belum memiliki metode pembayaran aktif.")

            default_method = methods[0]
            default_exp_cat = exp_cats[0] if exp_cats else all_cats[0]
            default_inc_cat = inc_cats[0] if inc_cats else all_cats[0]

            preview_items = []
            duplicate_count = 0

            for row_num, row in enumerate(data_rows, start=1):
                if not row or all(not cell.strip() for cell in row):
                    continue

                raw_date_str = row[date_idx] if date_idx < len(row) else ""
                parsed_dt = _parse_csv_date(raw_date_str)
                if parsed_dt is None:
                    continue  # Lewati baris dengan tanggal tidak valid

                # Nominal
                amt_str = row[amount_idx] if amount_idx != -1 and amount_idx < len(row) else "0"
                try:
                    amount_val = _clean_amount(amt_str)
                except Exception:
                    continue
                if amount_val <= 0:
                    continue

                # Keterangan
                note_str = row[note_idx].strip() if note_idx != -1 and note_idx < len(row) else ""

                # Tipe transaksi
                raw_type = row[type_idx].lower() if type_idx != -1 and type_idx < len(row) else ""
                is_income = any(
                    k in raw_type for k in ("pemasukan", "income", "kredit", "cr", "masuk")
                )
                tx_type = TYPE_INCOME if is_income else TYPE_EXPENSE

                # Auto-matching kategori
                matched_cat_id = None
                if cat_idx != -1 and cat_idx < len(row):
                    cat_hint = row[cat_idx].strip().lower()
                    matched = next((c for c in all_cats if c.name.lower() == cat_hint), None)
                    if matched:
                        matched_cat_id = matched.id

                # Coba parse cepat via quick alias jika kategori belum cocok
                if not matched_cat_id and note_str:
                    try:
                        quick_res = aliases_service.parse_quick_input(
                            session, user.id, f"{note_str} {amount_val}"
                        )
                        matched_cat_id = quick_res.category_id
                    except Exception:
                        pass

                if not matched_cat_id:
                    matched_cat_id = (
                        default_inc_cat.id if tx_type == TYPE_INCOME else default_exp_cat.id
                    )

                # Auto-matching metode pembayaran
                matched_method_id = default_method.id
                if method_idx != -1 and method_idx < len(row):
                    meth_hint = row[method_idx].strip().lower()
                    matched_m = next(
                        (
                            m
                            for m in methods
                            if m.name.lower() in meth_hint or meth_hint in m.name.lower()
                        ),
                        None,
                    )
                    if matched_m:
                        matched_method_id = matched_m.id

                # Deteksi duplikat pada tanggal dan nominal yang sama
                existing_txs = queries.list_transactions(
                    session, user.id, start=parsed_dt, end=parsed_dt, include_deleted=False
                )
                is_duplicate = any(
                    t.amount == amount_val and t.type == tx_type for t in existing_txs
                )
                if is_duplicate:
                    duplicate_count += 1

                preview_items.append(
                    {
                        "row_id": row_num,
                        "date_str": format_display(parsed_dt),
                        "date_iso": parsed_dt.isoformat(),
                        "type": tx_type,
                        "amount": amount_val,
                        "amount_formatted": format_rupiah(amount_val),
                        "category_id": matched_cat_id,
                        "method_id": matched_method_id,
                        "note": note_str,
                        "is_duplicate": is_duplicate,
                    }
                )

            return {
                "items": preview_items,
                "categories": all_cats,
                "methods": methods,
                "duplicate_count": duplicate_count,
                "total_rows": len(preview_items),
            }

    try:
        preview_data = await asyncio.to_thread(_sync)
        preview_data["request"] = request
        preview_data["user"] = user

        return templates.TemplateResponse(
            request=request,
            name="components/import_preview.html",
            context=preview_data,
        )
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal membaca file CSV impor: %s", exc)
        return _render_toast_response(
            request, [{"type": "error", "message": "Gagal membaca atau memproses file CSV."}]
        )


@router.post("/reports/import/confirm", response_class=HTMLResponse)
async def import_confirm_endpoint(request: Request):
    """Simpan transaksi terpilih dari hasil pratinjau impor CSV ke basis data."""
    user = get_current_user_optional(request)
    if user is None:
        return HTMLResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    form_data = await request.form()
    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            # Ambil semua row_id yang dicentang
            selected_indices = form_data.getlist("selected_rows")
            if not selected_indices:
                raise ValidationError("Pilih minimal satu transaksi untuk diimpor.")

            saved_count = 0
            for idx in selected_indices:
                date_str = form_data.get(f"date_{idx}")
                type_str = form_data.get(f"type_{idx}")
                amount_str = form_data.get(f"amount_{idx}")
                cat_id_str = form_data.get(f"category_id_{idx}")
                method_id_str = form_data.get(f"method_id_{idx}")
                note_str = form_data.get(f"note_{idx}")

                if not (date_str and type_str and amount_str and cat_id_str and method_id_str):
                    continue

                clean_amt = _clean_amount(amount_str)
                cat_id = int(cat_id_str)
                method_id = int(method_id_str)
                note_clean = note_str.strip() if note_str else None

                if type_str == TYPE_INCOME:
                    transactions_service.add_income(
                        session,
                        user.id,
                        amount=clean_amt,
                        category_id=cat_id,
                        method_id=method_id,
                        on=date_str,
                        note=note_clean,
                    )
                else:
                    transactions_service.add_expense(
                        session,
                        user.id,
                        amount=clean_amt,
                        category_id=cat_id,
                        method_id=method_id,
                        on=date_str,
                        note=note_clean,
                    )
                saved_count += 1

            return saved_count

    try:
        saved_count = await asyncio.to_thread(_sync)
        toasts = [
            {
                "type": "success",
                "message": f"Berhasil mengimpor {saved_count} transaksi ke basis data.",
            }
        ]
        # Bersihkan container pratinjau dan berikan feedback toast
        extra_html = '<div id="import-preview-container" class="mt-4"></div>'
        return _render_toast_response(
            request, toasts, trigger="importCompleted", extra_html=extra_html
        )
    except (ValidationError, AppError) as err:
        return _render_toast_response(request, [{"type": "error", "message": err.message}])
    except Exception as exc:
        logger.exception("Gagal menyimpan transaksi impor: %s", exc)
        return _render_toast_response(
            request, [{"type": "error", "message": "Gagal menyimpan transaksi impor."}]
        )
