"""Service ekspor data dan laporan keuangan (CSV, Excel, PDF).

Semua fungsi sinkron, menerima `Session` dan `user_id`, dan mendelegasikan
pembuatan dokumen ekspor ke `reports.export`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from reports import export as _rep_export
from reports.export import ExportPayload


def get_export_data(
    session: Session,
    user_id: int,
    *,
    start: date | str | None = None,
    end: date | str | None = None,
    type_: str | None = None,
    category_id: int | None = None,
    method_id: int | None = None,
    keyword: str | None = None,
) -> ExportPayload:
    """Ambil data transaksi dan ringkasan laporan milik `user_id` untuk diekspor."""
    return _rep_export.get_export_data(
        session,
        user_id,
        start=start,
        end=end,
        type_=type_,
        category_id=category_id,
        method_id=method_id,
        keyword=keyword,
    )


def export_csv(
    session: Session,
    user_id: int,
    *,
    start: date | str | None = None,
    end: date | str | None = None,
    type_: str | None = None,
    category_id: int | None = None,
    method_id: int | None = None,
    keyword: str | None = None,
    file_path: str | Path | None = None,
) -> Path:
    """Ekspor transaksi milik `user_id` ke file CSV."""
    return _rep_export.export_csv(
        session,
        user_id,
        start=start,
        end=end,
        type_=type_,
        category_id=category_id,
        method_id=method_id,
        keyword=keyword,
        file_path=file_path,
    )


def export_excel(
    session: Session,
    user_id: int,
    *,
    start: date | str | None = None,
    end: date | str | None = None,
    type_: str | None = None,
    category_id: int | None = None,
    method_id: int | None = None,
    keyword: str | None = None,
    file_path: str | Path | None = None,
) -> Path:
    """Ekspor laporan dan transaksi milik `user_id` ke file Excel (.xlsx)."""
    return _rep_export.export_excel(
        session,
        user_id,
        start=start,
        end=end,
        type_=type_,
        category_id=category_id,
        method_id=method_id,
        keyword=keyword,
        file_path=file_path,
    )


def export_pdf(
    session: Session,
    user_id: int,
    *,
    start: date | str | None = None,
    end: date | str | None = None,
    type_: str | None = None,
    category_id: int | None = None,
    method_id: int | None = None,
    keyword: str | None = None,
    file_path: str | Path | None = None,
) -> Path:
    """Ekspor laporan dan transaksi milik `user_id` ke file PDF (.pdf)."""
    return _rep_export.export_pdf(
        session,
        user_id,
        start=start,
        end=end,
        type_=type_,
        category_id=category_id,
        method_id=method_id,
        keyword=keyword,
        file_path=file_path,
    )
