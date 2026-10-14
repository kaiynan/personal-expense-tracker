"""Endpoint API JSON untuk menyuplai data 4 grafik interaktif Chart.js (Fase W2).

Semua perhitungan memanggil services/dashboard.py lewat session_scope(factory).
Transfer dan adjustment dieksklusikan dari grafik pemasukan dan pengeluaran.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse

from database.engine import session_scope
from models import TYPE_EXPENSE, TYPE_INCOME
from services import dashboard as dashboard_service
from utils.errors import ValidationError
from web.deps import get_current_user_optional, get_session_factory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/charts", tags=["charts"])


@router.get("/expenses-by-category")
async def chart_expenses_by_category(
    request: Request,
    period: str = Query("monthly", description="Periode: 'monthly' atau 'weekly'"),
):
    """Data pengeluaran per kategori (Doughnut Chart)."""
    user = get_current_user_optional(request)
    if user is None:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": "Unauthorized", "message": "Silakan login terlebih dahulu."},
        )

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            return dashboard_service.get_category_chart_data(
                session, user.id, type_=TYPE_EXPENSE, period=period
            )

    try:
        data = await asyncio.to_thread(_sync)
        return JSONResponse(status_code=status.HTTP_200_OK, content=data)
    except ValidationError as exc:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "ValidationError", "message": str(exc)},
        )
    except Exception as exc:
        logger.error("Gagal mengambil data chart pengeluaran: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "InternalError", "message": "Terjadi kesalahan internal."},
        )


@router.get("/income-by-category")
async def chart_income_by_category(
    request: Request,
    period: str = Query("monthly", description="Periode: 'monthly' atau 'weekly'"),
):
    """Data pemasukan per kategori (Doughnut Chart)."""
    user = get_current_user_optional(request)
    if user is None:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": "Unauthorized", "message": "Silakan login terlebih dahulu."},
        )

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            return dashboard_service.get_category_chart_data(
                session, user.id, type_=TYPE_INCOME, period=period
            )

    try:
        data = await asyncio.to_thread(_sync)
        return JSONResponse(status_code=status.HTTP_200_OK, content=data)
    except ValidationError as exc:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "ValidationError", "message": str(exc)},
        )
    except Exception as exc:
        logger.error("Gagal mengambil data chart pemasukan: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "InternalError", "message": "Terjadi kesalahan internal."},
        )


@router.get("/income-vs-expense")
async def chart_income_vs_expense(
    request: Request,
    period: str = Query("monthly", description="Periode: 'monthly' atau 'weekly'"),
):
    """Data arus kas pemasukan vs pengeluaran (Bar Chart)."""
    user = get_current_user_optional(request)
    if user is None:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": "Unauthorized", "message": "Silakan login terlebih dahulu."},
        )

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            return dashboard_service.get_cash_flow_chart_data(session, user.id, period=period)

    try:
        data = await asyncio.to_thread(_sync)
        return JSONResponse(status_code=status.HTTP_200_OK, content=data)
    except ValidationError as exc:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "ValidationError", "message": str(exc)},
        )
    except Exception as exc:
        logger.error("Gagal mengambil data chart arus kas: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "InternalError", "message": "Terjadi kesalahan internal."},
        )


@router.get("/balance-trend")
async def chart_balance_trend(
    request: Request,
    period: str = Query("monthly", description="Periode: 'monthly' atau 'weekly'"),
):
    """Data perkembangan saldo kumulatif (Line Chart)."""
    user = get_current_user_optional(request)
    if user is None:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": "Unauthorized", "message": "Silakan login terlebih dahulu."},
        )

    factory = get_session_factory(request)

    def _sync():
        with session_scope(factory) as session:
            return dashboard_service.get_balance_trend_chart_data(session, user.id, period=period)

    try:
        data = await asyncio.to_thread(_sync)
        return JSONResponse(status_code=status.HTTP_200_OK, content=data)
    except ValidationError as exc:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "ValidationError", "message": str(exc)},
        )
    except Exception as exc:
        logger.error("Gagal mengambil data chart perkembangan saldo: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "InternalError", "message": "Terjadi kesalahan internal."},
        )
