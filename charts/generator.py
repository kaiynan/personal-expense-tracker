"""Pembuatan 4 jenis grafik (charts) keuangan menggunakan matplotlib backend Agg.

Semua grafik disimpan dalam format PNG di folder `reports_output/`.
1. Pengeluaran per kategori (pie chart / donut)
2. Pemasukan per kategori (pie chart / donut)
3. Pemasukan vs pengeluaran per periode (grouped bar chart)
4. Perkembangan saldo kumulatif (line chart)
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sqlalchemy.orm import Session

import config
from database import balances
from database.reports import (
    get_category_breakdown,
    get_daily_balance_deltas,
    get_daily_flows,
)
from models import TYPE_EXPENSE, TYPE_INCOME
from utils.dates import format_display

CHART_COLORS = [
    "#2b5c8f",
    "#348aa7",
    "#525174",
    "#5dd39e",
    "#bce784",
    "#f4a261",
    "#e76f51",
    "#e63946",
    "#a8dadc",
    "#457b9d",
]


def _setup_figure():
    fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
    return fig, ax


def _save_fig(fig, output_path: Path) -> Path:
    config.ensure_directories()
    fig.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)
    return output_path


def create_category_pie_chart(
    items: list[tuple[str, int]],
    title: str,
    output_path: Path,
) -> Path:
    """Buat pie chart / donut chart untuk distribusi kategori."""
    fig, ax = _setup_figure()
    if not items or sum(amt for _, amt in items) == 0:
        ax.annotate(
            "Tidak ada data transaksi pada periode ini",
            xy=(0.5, 0.5),
            ha="center",
            va="center",
            fontsize=12,
            color="#666666",
        )
        ax.axis("off")
        ax.set_title(title, fontsize=14, weight="bold")
        return _save_fig(fig, output_path)

    labels = [name for name, _ in items]
    values = [amt for _, amt in items]
    colors = CHART_COLORS[: len(values)]

    _wedges, texts, autotexts = ax.pie(
        values,
        labels=labels,
        autopct="%1.1f%%",
        startangle=140,
        colors=colors,
        pctdistance=0.8,
        wedgeprops={"edgecolor": "white", "linewidth": 1.5, "width": 0.5},
    )
    plt.setp(autotexts, size=9, weight="bold")
    plt.setp(texts, size=9)
    ax.set_title(title, fontsize=14, weight="bold", pad=20)
    return _save_fig(fig, output_path)


def create_income_vs_expense_chart(
    daily_data: list[tuple[date, int, int]],
    title: str,
    output_path: Path,
) -> Path:
    """Buat grouped bar chart perbandingan pemasukan vs pengeluaran harian."""
    fig, ax = _setup_figure()
    if not daily_data:
        ax.annotate(
            "Tidak ada transaksi pada periode ini",
            xy=(0.5, 0.5),
            ha="center",
            va="center",
            fontsize=12,
            color="#666666",
        )
        ax.axis("off")
        ax.set_title(title, fontsize=14, weight="bold")
        return _save_fig(fig, output_path)

    labels = [d.strftime("%d/%m") for d, _, _ in daily_data]
    incomes = [inc for _, inc, _ in daily_data]
    expenses = [exp for _, _, exp in daily_data]

    x = range(len(labels))
    width = 0.35

    ax.bar([i - width / 2 for i in x], incomes, width, label="Pemasukan", color="#2a9d8f")
    ax.bar([i + width / 2 for i in x], expenses, width, label="Pengeluaran", color="#e76f51")

    ax.set_ylabel("Nominal (Rp)")
    ax.set_title(title, fontsize=14, weight="bold")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.5)

    return _save_fig(fig, output_path)


def create_balance_trend_chart(
    trend_data: list[tuple[date, int]],
    title: str,
    output_path: Path,
) -> Path:
    """Buat line chart perkembangan saldo total kumulatif."""
    fig, ax = _setup_figure()
    if not trend_data:
        ax.annotate(
            "Tidak ada data saldo pada periode ini",
            xy=(0.5, 0.5),
            ha="center",
            va="center",
            fontsize=12,
            color="#666666",
        )
        ax.axis("off")
        ax.set_title(title, fontsize=14, weight="bold")
        return _save_fig(fig, output_path)

    dates = [d.strftime("%d/%m") for d, _ in trend_data]
    balances_vals = [bal for _, bal in trend_data]

    ax.plot(dates, balances_vals, marker="o", color="#1d3557", linewidth=2.5, markersize=4)
    ax.fill_between(range(len(dates)), balances_vals, alpha=0.15, color="#457b9d")

    ax.set_ylabel("Saldo Total (Rp)")
    ax.set_title(title, fontsize=14, weight="bold")
    # Tampilkan label tiap beberapa hari jika rentang panjang
    step = max(1, len(dates) // 10)
    ax.set_xticks(list(range(0, len(dates), step)))
    ax.set_xticklabels([dates[i] for i in range(0, len(dates), step)], rotation=45, ha="right")
    ax.grid(True, linestyle="--", alpha=0.5)

    return _save_fig(fig, output_path)


def generate_all_charts(
    session: Session,
    user_id: int,
    start: date,
    end: date,
    prefix: str = "",
) -> dict[str, Path]:
    """Hasilkan keempat chart keuangan lengkap untuk rentang tanggal tertentu.

    1. Pengeluaran per kategori
    2. Pemasukan per kategori
    3. Pemasukan vs pengeluaran per periode
    4. Perkembangan saldo kumulatif

    Returns:
        Peta nama chart ke path file PNG yang berhasil dibuat.
    """
    config.ensure_directories()
    base_tag = f"u{user_id}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
    tag = f"{prefix}_{base_tag}" if prefix else base_tag
    period_label = f"({format_display(start)} - {format_display(end)})"

    # 1. Pengeluaran per kategori
    exp_breakdown = get_category_breakdown(session, user_id, TYPE_EXPENSE, start, end)
    exp_items = [(name, amt) for _, name, amt in exp_breakdown]
    p1 = config.REPORTS_DIR / f"pengeluaran_kategori_{tag}.png"
    f1 = create_category_pie_chart(exp_items, f"Pengeluaran per Kategori {period_label}", p1)

    # 2. Pemasukan per kategori
    inc_breakdown = get_category_breakdown(session, user_id, TYPE_INCOME, start, end)
    inc_items = [(name, amt) for _, name, amt in inc_breakdown]
    p2 = config.REPORTS_DIR / f"pemasukan_kategori_{tag}.png"
    f2 = create_category_pie_chart(inc_items, f"Pemasukan per Kategori {period_label}", p2)

    # 3. Pemasukan vs Pengeluaran
    daily_flows = get_daily_flows(session, user_id, start, end)
    p3 = config.REPORTS_DIR / f"arus_kas_periode_{tag}.png"
    f3 = create_income_vs_expense_chart(daily_flows, f"Pemasukan vs Pengeluaran {period_label}", p3)

    # 4. Perkembangan saldo kumulatif
    baseline = balances.total_balance(session, user_id, as_of=start - timedelta(days=1))
    deltas = dict(get_daily_balance_deltas(session, user_id, start, end))
    trend_data: list[tuple[date, int]] = []
    curr = baseline
    cur_date = start
    while cur_date <= end:
        curr += deltas.get(cur_date, 0)
        trend_data.append((cur_date, curr))
        cur_date += timedelta(days=1)

    p4 = config.REPORTS_DIR / f"perkembangan_saldo_{tag}.png"
    f4 = create_balance_trend_chart(trend_data, f"Perkembangan Saldo Kumulatif {period_label}", p4)

    return {
        "expense_by_category": f1,
        "income_by_category": f2,
        "income_vs_expense": f3,
        "balance_trend": f4,
    }
