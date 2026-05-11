from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go


def make_revenue_chart(df: pd.DataFrame, title: str) -> go.Figure:
    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=df["month"],
            y=df["revenue"],
            mode="lines+markers",
            name="Revenue",
            connectgaps=False,
        )
    )

    for w, color in [(3, "#1f77b4"), (6, "#ff7f0e"), (12, "#2ca02c")]:
        col = f"ma_{w}"
        if col in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df["month"],
                    y=df[col],
                    mode="lines",
                    name=f"MA {w}M",
                    connectgaps=False,
                    line=dict(width=2, color=color, dash="solid"),
                )
            )

    missing = df.loc[df["revenue"].isna(), "month"]
    if len(missing) > 0:
        # show missing months as x markers at y=0 with transparent style (so it doesn't dominate)
        fig.add_trace(
            go.Scatter(
                x=missing,
                y=[0] * len(missing),
                mode="markers",
                name="Missing",
                marker=dict(size=8, symbol="x", color="rgba(200,0,0,0.5)"),
                hovertemplate="Missing revenue data<br>%{x|%Y-%m}<extra></extra>",
            )
        )

    fig.update_layout(
        title=title,
        xaxis_title="Month",
        yaxis_title="Revenue",
        hovermode="x unified",
        legend_title_text="",
        margin=dict(l=40, r=20, t=60, b=40),
    )

    fig.update_xaxes(dtick="M3", tickformat="%Y-%m")
    return fig


def make_price_history_chart(df: pd.DataFrame, title: str) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["month"],
            y=df.get("close"),
            mode="lines",
            name="Close",
            connectgaps=False,
            hovertemplate="%{x|%Y-%m}<br>月收盤：%{y:,.2f}<extra></extra>",
        )
    )

    fig.update_layout(
        title=title,
        xaxis_title="Month",
        yaxis_title="月收盤價",
        hovermode="x unified",
        legend_title_text="",
        margin=dict(l=40, r=20, t=60, b=40),
    )
    fig.update_xaxes(dtick="M3", tickformat="%Y-%m")
    fig.update_yaxes(tickformat=",.2f")
    return fig


def make_price_vs_revenue_chart(df_revenue: pd.DataFrame, df_close: pd.DataFrame, title: str) -> go.Figure:
    fig = go.Figure()

    revenue_10k = df_revenue.get("revenue")
    if revenue_10k is not None:
        revenue_10k = pd.to_numeric(revenue_10k, errors="coerce") / 10000

    fig.add_trace(
        go.Bar(
            x=df_revenue["month"],
            y=revenue_10k,
            name="營收",
            opacity=0.55,
            hovertemplate="%{x|%Y-%m}<br>營收：%{y:,.0f} 10K<extra></extra>",
        )
    )

    fig.add_trace(
        go.Scatter(
            x=df_close["month"],
            y=df_close.get("close"),
            mode="lines",
            name="股價(月收盤)",
            yaxis="y2",
            connectgaps=False,
            hovertemplate="%{x|%Y-%m}<br>月收盤：%{y:,.2f}<extra></extra>",
        )
    )

    fig.update_layout(
        title=title,
        hovermode="x unified",
        legend_title_text="",
        barmode="overlay",
        margin=dict(l=40, r=40, t=60, b=40),
        xaxis_title="Month",
        yaxis=dict(title="營收 (10K)", tickformat="~s", rangemode="tozero"),
        yaxis2=dict(title="股價", tickformat=",.2f", overlaying="y", side="right", rangemode="tozero"),
    )

    fig.update_xaxes(dtick="M3", tickformat="%Y-%m")
    return fig


def make_cash_dividends_chart(df: pd.DataFrame, title: str) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=df["month"],
            y=df.get("cash_dividend"),
            name="配息(元)",
            opacity=0.75,
            hovertemplate="%{x|%Y-%m}<br>配息：%{y:,.2f} 元<extra></extra>",
        )
    )

    fig.update_layout(
        title=title,
        xaxis_title="Month",
        yaxis_title="配息(元)",
        hovermode="x unified",
        legend_title_text="",
        margin=dict(l=40, r=20, t=60, b=40),
    )
    fig.update_xaxes(dtick="M3", tickformat="%Y-%m")
    fig.update_yaxes(tickformat=",.2f", rangemode="tozero")
    return fig


def make_dividend_yield_chart(df: pd.DataFrame, title: str) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["month"],
            y=df.get("dividend_yield"),
            mode="lines+markers",
            name="Dividend Yield (%)",
            connectgaps=False,
            hovertemplate="%{x|%Y-%m}<br>殖利率：%{y:.2f}%<extra></extra>",
        )
    )

    fig.update_layout(
        title=title,
        xaxis_title="Month",
        yaxis_title="殖利率(%)",
        hovermode="x unified",
        legend_title_text="",
        margin=dict(l=40, r=20, t=60, b=40),
    )
    fig.update_xaxes(dtick="M3", tickformat="%Y-%m")
    fig.update_yaxes(tickformat=".2f", ticksuffix="%")
    return fig


# ---------------------------------------------------------------------------
# ROE / ROA
# ---------------------------------------------------------------------------

def make_roe_roa_chart(rows: list[dict[str, Any]], title: str) -> go.Figure:
    """Quarterly near-four-season TTM ROE / ROA."""
    fig = go.Figure()
    if not rows:
        return fig

    labels = [r.get("quarter_label") or r.get("quarter", "") for r in rows]
    roe = [r.get("roe") for r in rows]
    roa = [r.get("roa") for r in rows]

    fig.add_trace(go.Scatter(
        x=labels, y=roe, mode="lines+markers", name="ROE (%)",
        connectgaps=False,
        line=dict(color="#e07b39", width=2),
        marker=dict(color="#e07b39", size=5),
        hovertemplate="%{x}<br>ROE：%{y:.2f}%<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=labels, y=roa, mode="lines+markers", name="ROA (%)",
        connectgaps=False,
        line=dict(color="#4a90d9", width=2),
        marker=dict(color="#4a90d9", size=5),
        hovertemplate="%{x}<br>ROA：%{y:.2f}%<extra></extra>",
    ))

    n = len(labels)
    if n > 0:
        fig.add_trace(go.Scatter(
            x=[labels[0], labels[n - 1]], y=[15, 15], mode="lines",
            name="ROE 優質基準 15%",
            line=dict(color="#e07b39", width=1, dash="dot"),
            hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=[labels[0], labels[n - 1]], y=[5, 5], mode="lines",
            name="ROA 優質基準 5%",
            line=dict(color="#4a90d9", width=1, dash="dot"),
            hoverinfo="skip",
        ))

    fig.update_layout(
        title=title, hovermode="x unified",
        xaxis=dict(type="category", tickangle=-35),
        yaxis=dict(title="（%）", tickformat=".2f", zeroline=True),
        margin=dict(l=50, r=20, t=60, b=60),
    )
    return fig


# ---------------------------------------------------------------------------
# Debt Ratio
# ---------------------------------------------------------------------------

def make_debt_ratio_chart(rows: list[dict[str, Any]], title: str) -> go.Figure:
    """Quarterly debt ratio = TotalLiabilities / TotalAssets × 100%."""
    fig = go.Figure()
    if not rows:
        return fig

    labels = [r.get("quarter_label") or r.get("quarter", "") for r in rows]
    ratios = [r.get("debt_ratio") for r in rows]
    n = len(labels)

    fig.add_trace(go.Scatter(
        x=labels, y=ratios, mode="lines+markers", name="負債比 (%)",
        connectgaps=False,
        line=dict(color="#7c3aed", width=2),
        marker=dict(color="#7c3aed", size=5),
        hovertemplate="%{x}<br>負債比：%{y:.2f}%<extra></extra>",
    ))
    if n > 0:
        fig.add_trace(go.Scatter(
            x=[labels[0], labels[n - 1]], y=[40, 40], mode="lines",
            name="健康門檻 40%",
            line=dict(color="#16a34a", width=1.5, dash="dot"),
            hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=[labels[0], labels[n - 1]], y=[60, 60], mode="lines",
            name="風險門檻 60%",
            line=dict(color="#dc2626", width=1.5, dash="dot"),
            hoverinfo="skip",
        ))

    fig.update_layout(
        title=title, hovermode="x unified",
        xaxis=dict(type="category", tickangle=-35),
        yaxis=dict(title="負債比（%）", tickformat=".1f"),
        margin=dict(l=50, r=20, t=60, b=60),
    )
    return fig


# ---------------------------------------------------------------------------
# Free Cash Flow
# ---------------------------------------------------------------------------

def make_fcf_chart(fcf_data: dict[str, Any], title: str) -> go.Figure:
    """Annual Free Cash Flow grouped bar chart + FCF % of capital line."""
    fig = go.Figure()
    rows = (fcf_data or {}).get("rows") or []
    if not rows:
        return fig

    labels = [str(r["year"]) for r in rows]
    opcf = [r["operating_cf"] / 1000 if r.get("operating_cf") is not None else None for r in rows]
    capex = [-r["capex"] / 1000 if r.get("capex") is not None else None for r in rows]
    fcf = [r["fcf"] / 1000 if r.get("fcf") is not None else None for r in rows]
    pct = [r.get("fcf_pct_capital") for r in rows]

    fig.add_trace(go.Bar(x=labels, y=opcf, name="營業現金流", marker=dict(color="#3b82f6", opacity=0.75),
                         hovertemplate="%{x}年<br>營業CF：%{y:,.1f} M<extra></extra>"))
    fig.add_trace(go.Bar(x=labels, y=capex, name="資本支出（負）", marker=dict(color="#f97316", opacity=0.75),
                         hovertemplate="%{x}年<br>資本支出：%{y:,.1f} M<extra></extra>"))
    fig.add_trace(go.Bar(x=labels, y=fcf, name="自由現金流", marker=dict(color="#22c55e", opacity=0.85),
                         hovertemplate="%{x}年<br>自由CF：%{y:,.1f} M<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=labels, y=pct, mode="lines+markers", name="FCF 佔股本 %",
        yaxis="y2", line=dict(color="#7c3aed", width=2), marker=dict(color="#7c3aed", size=6),
        connectgaps=False, hovertemplate="%{x}年<br>佔股本：%{y:.1f}%<extra></extra>",
    ))

    fig.update_layout(
        title=title, hovermode="x unified", barmode="group",
        xaxis=dict(type="category"),
        yaxis=dict(title="金額（百萬元）", tickformat=",.0f", zeroline=True),
        yaxis2=dict(title="佔股本 (%)", overlaying="y", side="right", tickformat=".1f", showgrid=False),
        margin=dict(l=60, r=60, t=60, b=40),
    )
    return fig


# ---------------------------------------------------------------------------
# Volume / Turnover
# ---------------------------------------------------------------------------

def make_volume_turnover_chart(rows: list[dict[str, Any]], title: str) -> go.Figure:
    """Daily volume and trading turnover (last 3 months)."""
    fig = go.Figure()
    if not rows:
        return fig

    dates = [r.get("date") for r in rows]
    volume = [r.get("volume") for r in rows]
    turnover = [r.get("turnover") for r in rows]

    fig.add_trace(go.Scatter(
        x=dates, y=volume, mode="lines", name="成交量",
        connectgaps=False, yaxis="y",
        hovertemplate="%{x}<br>成交量：%{y:,}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=turnover, mode="lines", name="成交筆數",
        connectgaps=False, yaxis="y2",
        hovertemplate="%{x}<br>成交筆數：%{y:,}<extra></extra>",
    ))

    fig.update_layout(
        title=title, hovermode="x unified",
        xaxis=dict(tickformat="%m-%d"),
        yaxis=dict(title="成交量", tickformat="~s", rangemode="tozero"),
        yaxis2=dict(title="成交筆數", overlaying="y", side="right", tickformat="~s", showgrid=False, rangemode="tozero"),
        margin=dict(l=55, r=55, t=60, b=40),
    )
    return fig


# ---------------------------------------------------------------------------
# Trend Overview (normalized to base 100)
# ---------------------------------------------------------------------------

def make_trend_overview_chart(
    revenue_rows: list[dict],
    price_rows: list[dict],
    yield_rows: list[dict],
    title: str,
) -> go.Figure:
    """Normalized time-series: revenue, MA12, price, dividend yield all indexed to 100."""

    def _normalize(rows: list[dict], key: str) -> tuple[list, list]:
        cleaned = [(r.get("month") or r.get("date"), r.get(key)) for r in rows]
        valid = [(m, v) for m, v in cleaned if v is not None and not pd.isna(v)]
        if not valid:
            return [m for m, _ in cleaned], [None] * len(cleaned)
        base = float(valid[0][1])
        if base == 0:
            return [m for m, _ in cleaned], [None] * len(cleaned)
        return (
            [m for m, _ in cleaned],
            [None if v is None or pd.isna(v) else round(float(v) / base * 100, 2) for _, v in cleaned],
        )

    x_rev, y_rev = _normalize(revenue_rows, "revenue")
    x_ma, y_ma = _normalize(revenue_rows, "ma_12")
    x_pr, y_pr = _normalize(price_rows, "close")
    x_dy, y_dy = _normalize(yield_rows, "dividend_yield")

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x_rev, y=y_rev, mode="lines+markers", name="月營收",
                             connectgaps=False, line=dict(color="#111827", width=2),
                             hovertemplate="%{x|%Y-%m}<br>月營收指數：%{y:.1f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=x_ma, y=y_ma, mode="lines", name="營收 MA12",
                             connectgaps=False, line=dict(color="#e07b39", width=2, dash="dot"),
                             hovertemplate="%{x|%Y-%m}<br>MA12 指數：%{y:.1f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=x_pr, y=y_pr, mode="lines", name="月收盤價",
                             connectgaps=False, line=dict(color="#4a90d9", width=2),
                             hovertemplate="%{x|%Y-%m}<br>股價指數：%{y:.1f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=x_dy, y=y_dy, mode="lines", name="殖利率",
                             connectgaps=False, line=dict(color="#1d8f6a", width=2),
                             hovertemplate="%{x|%Y-%m}<br>殖利率指數：%{y:.1f}<extra></extra>"))

    fig.update_layout(
        title=title, hovermode="x unified",
        xaxis=dict(tickformat="%Y-%m"),
        yaxis=dict(title="基準指數（首個有效值 = 100）", tickformat=".0f", zeroline=True),
        margin=dict(l=55, r=20, t=60, b=40),
    )
    return fig


# ---------------------------------------------------------------------------
# Operating Turnover Days
# ---------------------------------------------------------------------------

def make_turnover_days_chart(rows: list[dict[str, Any]], title: str) -> go.Figure:
    """Quarterly DSO / DIO / DPO / Cash Conversion Cycle."""
    fig = go.Figure()
    if not rows:
        return fig

    labels = [r.get("quarter_label") or r.get("quarter", "") for r in rows]
    dso = [r.get("dso") for r in rows]
    dio = [r.get("dio") for r in rows]
    dpo = [r.get("dpo") for r in rows]
    ccc = [r.get("ccc") for r in rows]

    colors = {"dso": "#3b82f6", "dio": "#f97316", "dpo": "#22c55e", "ccc": "#7c3aed"}

    fig.add_trace(go.Scatter(x=labels, y=dso, mode="lines+markers", name="DSO 應收天數",
                             connectgaps=False, line=dict(color=colors["dso"], width=2),
                             marker=dict(size=5), hovertemplate="%{x}<br>DSO：%{y:.1f} 天<extra></extra>"))
    fig.add_trace(go.Scatter(x=labels, y=dio, mode="lines+markers", name="DIO 存貨天數",
                             connectgaps=False, line=dict(color=colors["dio"], width=2),
                             marker=dict(size=5), hovertemplate="%{x}<br>DIO：%{y:.1f} 天<extra></extra>"))
    fig.add_trace(go.Scatter(x=labels, y=dpo, mode="lines+markers", name="DPO 應付天數",
                             connectgaps=False, line=dict(color=colors["dpo"], width=2, dash="dash"),
                             marker=dict(size=5), hovertemplate="%{x}<br>DPO：%{y:.1f} 天<extra></extra>"))
    fig.add_trace(go.Scatter(x=labels, y=ccc, mode="lines+markers", name="CCC 現金循環",
                             connectgaps=False, line=dict(color=colors["ccc"], width=2.5),
                             marker=dict(size=6), hovertemplate="%{x}<br>CCC：%{y:.1f} 天<extra></extra>"))

    fig.update_layout(
        title=title, hovermode="x unified",
        xaxis=dict(type="category", tickangle=-35),
        yaxis=dict(title="天數", tickformat=".0f", zeroline=True),
        margin=dict(l=50, r=20, t=60, b=70),
    )
    return fig


# ---------------------------------------------------------------------------
# P/E River Chart
# ---------------------------------------------------------------------------

def make_pe_river_chart(data: dict[str, Any], title: str) -> go.Figure:
    """P/E river chart: historical PER line + percentile band fills."""
    fig = go.Figure()
    if not data:
        return fig

    per_rows = data.get("per_rows") or []
    bands = data.get("bands") or {}

    if not per_rows or not bands:
        return fig

    dates = [r["date"] for r in per_rows]
    per_vals = [r["per"] for r in per_rows]
    p5 = bands.get("p5", 0)
    p25 = bands.get("p25", 0)
    p50 = bands.get("p50", 0)
    p75 = bands.get("p75", 0)
    p95 = bands.get("p95", 0)

    band_y_p5 = [p5] * len(dates)
    band_y_p25 = [p25] * len(dates)
    band_y_p50 = [p50] * len(dates)
    band_y_p75 = [p75] * len(dates)
    band_y_p95 = [p95] * len(dates)

    # Band: p5→p25 (low range, blue)
    fig.add_trace(go.Scatter(x=dates, y=band_y_p5, mode="lines",
                             line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=dates, y=band_y_p25, mode="lines", name="P25 低估區",
                             fill="tonexty", fillcolor="rgba(59,130,246,0.15)",
                             line=dict(width=0.5, color="rgba(59,130,246,0.3)"), hoverinfo="skip"))

    # Band: p25→p75 (mid range, green)
    fig.add_trace(go.Scatter(x=dates, y=band_y_p25, mode="lines",
                             line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=dates, y=band_y_p75, mode="lines", name="P75 合理區",
                             fill="tonexty", fillcolor="rgba(34,197,94,0.12)",
                             line=dict(width=0.5, color="rgba(34,197,94,0.3)"), hoverinfo="skip"))

    # Band: p75→p95 (high range, red)
    fig.add_trace(go.Scatter(x=dates, y=band_y_p75, mode="lines",
                             line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=dates, y=band_y_p95, mode="lines", name="P95 高估區",
                             fill="tonexty", fillcolor="rgba(239,68,68,0.12)",
                             line=dict(width=0.5, color="rgba(239,68,68,0.3)"), hoverinfo="skip"))

    # Median line
    fig.add_trace(go.Scatter(x=dates, y=band_y_p50, mode="lines", name=f"中位數 P50 ({p50:.1f}x)",
                             line=dict(color="rgba(100,116,139,0.7)", width=1.5, dash="dash"),
                             hovertemplate=f"中位數 PER：{p50:.1f}x<extra></extra>"))

    # Actual PER line
    fig.add_trace(go.Scatter(
        x=dates, y=per_vals, mode="lines", name="本益比 (PER)",
        connectgaps=False,
        line=dict(color="#1e40af", width=2),
        hovertemplate="%{x}<br>PER：%{y:.1f}x<extra></extra>",
    ))

    fig.update_layout(
        title=title, hovermode="x unified",
        xaxis=dict(tickformat="%Y-%m"),
        yaxis=dict(title="本益比（倍）", tickformat=".1f", rangemode="tozero"),
        margin=dict(l=50, r=20, t=60, b=40),
    )
    return fig

