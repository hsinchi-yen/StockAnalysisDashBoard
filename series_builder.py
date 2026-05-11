from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class RevenuePoint:
    month_start: pd.Timestamp
    revenue: float | None


def month_start(d: date) -> pd.Timestamp:
    return pd.Timestamp(d.year, d.month, 1)


def build_continuous_month_index(start_month: pd.Timestamp, end_month: pd.Timestamp) -> pd.DatetimeIndex:
    start = pd.Timestamp(start_month.year, start_month.month, 1)
    end = pd.Timestamp(end_month.year, end_month.month, 1)
    return pd.date_range(start=start, end=end, freq="MS")


def points_to_frame(points: Iterable[RevenuePoint]) -> pd.DataFrame:
    rows = [(p.month_start, p.revenue) for p in points]
    df = pd.DataFrame(rows, columns=["month", "revenue"])
    df = df.drop_duplicates(subset=["month"], keep="last").sort_values("month")
    df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce")
    return df


def reindex_to_continuous_months(df: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    out = df.set_index("month").reindex(index)
    out.index.name = "month"
    out = out.reset_index()
    return out


def moving_average_masked(revenue: pd.Series, window_months: int) -> pd.Series:
    ma = revenue.rolling(window=window_months, min_periods=window_months).mean()
    # If the underlying month is missing, hide MA too
    ma = ma.where(revenue.notna())
    return ma


def add_mas(df: pd.DataFrame, windows: list[int] = [3, 6, 12]) -> pd.DataFrame:
    out = df.copy()
    for w in windows:
        out[f"ma_{w}"] = moving_average_masked(out["revenue"], w)
    return out


def compute_sloan_ratio(
    net_income_annual: float,
    operating_cf_annual: float,
    avg_total_assets: float,
) -> float | None:
    """Sloan Ratio (應計比率 / 盈餘品質).

    Measures the gap between accounting earnings and cash earnings.
    |ratio| < 0.1 indicates high-quality earnings backed by cash flow.
    Formula: (Net Income - Operating CF) / Avg Total Assets
    """
    if avg_total_assets == 0:
        return None
    return (net_income_annual - operating_cf_annual) / avg_total_assets
