"""MoneyDJ capital formation scraper + FinMind reconstruction fallback.

MoneyDJ (https://www.moneydj.com/z/zc/zcb/zcb_{stock_id}.djhtm) provides
cumulative 股本形成 breakdown (現金增資 / 盈餘轉增資 / 其他) for some stocks
without login.  For stocks that require login (response < 5 KB), falls back to
reconstructing the breakdown from FinMind balance-sheet + dividend data.

FinMind reconstruction caveat:
  The FinMind balance-sheet dataset starts from ~2012.  Capital formed before
  that date is lumped into 其他 as an initial unclassified block.  Data quality
  is good for recently-listed or actively-capitalizing stocks.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any

import pandas as pd
import requests

_MONEYDJ_URL = "https://www.moneydj.com/z/zc/zcb/zcb_{stock_id}.djhtm"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
_REQUEST_TIMEOUT = 15
_LOGIN_SIZE_THRESHOLD = 5000  # bytes; login-redirect pages are ~3 295 bytes


class MoneyDJError(RuntimeError):
    pass


def _parse_moneydj_html(content: bytes) -> list[dict[str, Any]] | None:
    """Parse Big5-encoded HTML table from MoneyDJ capital formation page.

    Returns list of {year, cash, earnings, other} dicts (values in 億元),
    or None if the page is a login redirect or unparseable.
    """
    if len(content) < _LOGIN_SIZE_THRESHOLD:
        return None  # login page

    text = content.decode("big5", errors="replace")

    rows: list[dict[str, Any]] = []
    for row in re.findall(r"<TR[^>]*>(.*?)</TR>", text, re.DOTALL | re.IGNORECASE):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
        clean = [
            re.sub(r"<[^>]+>", "", c).strip().replace("\xa0", "").replace(",", "")
            for c in cells
        ]
        clean = [c for c in clean if c]
        # Row: [year, cash, cash_pct%, earnings, earnings_pct%, other, other_pct%]
        if len(clean) >= 7 and re.match(r"^\d{4}$", clean[0]):
            try:
                rows.append(
                    {
                        "year": int(clean[0]),
                        "cash": round(float(clean[1]), 2),
                        "earnings": round(float(clean[3]), 2),
                        "other": round(float(clean[5]), 2),
                    }
                )
            except (ValueError, IndexError):
                continue

    if not rows:
        return None
    return sorted(rows, key=lambda r: r["year"])


def fetch_capital_formation_moneydj(stock_id: str) -> list[dict[str, Any]] | None:
    """Fetch cumulative capital formation table from MoneyDJ.

    Returns list of annual rows [{year, cash, earnings, other}] in 億元,
    sorted ascending by year.  Returns None if login is required or fetch fails.
    """
    url = _MONEYDJ_URL.format(stock_id=stock_id)
    try:
        r = requests.get(url, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
        if r.status_code != 200:
            return None
        return _parse_moneydj_html(r.content)
    except Exception:
        return None


def fetch_capital_formation_finmind(
    stock_id: str, token: str
) -> list[dict[str, Any]] | None:
    """Reconstruct cumulative capital formation from FinMind data.

    Uses TaiwanStockBalanceSheet (CapitalStock) + TaiwanStockDividend to
    classify year-over-year changes into 現金增資 / 盈餘轉增資 / 其他.

    Returns list of annual rows [{year, cash, earnings, other}] in 億元,
    sorted ascending by year.  Returns None on fetch failure.
    """
    base = "https://api.finmindtrade.com/api/v4/data"
    common = {"data_id": stock_id, "start_date": "2000-01-01",
              "end_date": date.today().isoformat(), "token": token}

    try:
        r_bs = requests.get(
            base,
            params={"dataset": "TaiwanStockBalanceSheet", **common},
            timeout=20,
        )
        r_div = requests.get(
            base,
            params={"dataset": "TaiwanStockDividend", **common},
            timeout=20,
        )
    except Exception:
        return None

    bs_data = r_bs.json().get("data", []) if r_bs.status_code == 200 else []
    div_data = r_div.json().get("data", []) if r_div.status_code == 200 else []

    if not bs_data:
        return None

    # Build CapitalStock per year (latest quarter in that year).
    cap_by_year: dict[int, float] = {}
    for row in bs_data:
        if row.get("type") != "CapitalStock":
            continue
        try:
            year = int(row["date"][:4])
            cap_by_year[year] = float(row["value"])
        except (KeyError, ValueError):
            continue

    if not cap_by_year:
        return None

    # Build dividend lookup by ex-dividend year.
    div_by_year: dict[int, dict[str, float]] = defaultdict(
        lambda: {"earnings": 0.0, "surplus": 0.0}
    )
    for d in div_data:
        ex_date = d.get("StockExDividendTradingDate") or d.get("date", "")
        if not ex_date or len(ex_date) < 4:
            continue
        try:
            year = int(ex_date[:4])
        except ValueError:
            continue
        div_by_year[year]["earnings"] += float(d.get("StockEarningsDistribution") or 0)
        div_by_year[year]["surplus"] += float(d.get("StockStatutorySurplus") or 0)

    sorted_years = sorted(cap_by_year)
    cum_cash = 0.0
    cum_earnings = 0.0
    cum_other = 0.0
    result: list[dict[str, Any]] = []
    prev_cap: float | None = None

    for year in sorted_years:
        cur_cap = cap_by_year[year]

        if prev_cap is None:
            # First available year: all existing capital is unclassified → 其他
            cum_other = cur_cap / 1e8
            result.append(
                {
                    "year": year,
                    "cash": round(cum_cash, 2),
                    "earnings": round(cum_earnings, 2),
                    "other": round(cum_other, 2),
                }
            )
            prev_cap = cur_cap
            continue

        delta = cur_cap - prev_cap
        if abs(delta) < 1e6:
            # Negligible change — repeat previous row
            result.append(
                {
                    "year": year,
                    "cash": round(cum_cash, 2),
                    "earnings": round(cum_earnings, 2),
                    "other": round(cum_other, 2),
                }
            )
            prev_cap = cur_cap
            continue

        div = div_by_year.get(year, {})
        earnings_rate = div.get("earnings", 0.0)
        surplus_rate = div.get("surplus", 0.0)

        # Stock bonus from earnings = rate × previous capital (in NTD)
        earnings_inc = earnings_rate * prev_cap
        surplus_inc = surplus_rate * prev_cap
        classified = earnings_inc + surplus_inc

        cash_inc = max(0.0, delta - classified)
        unclassified = max(0.0, delta - cash_inc - earnings_inc - surplus_inc)

        cum_cash += cash_inc / 1e8
        cum_earnings += earnings_inc / 1e8
        cum_other += (surplus_inc + unclassified) / 1e8

        result.append(
            {
                "year": year,
                "cash": round(cum_cash, 2),
                "earnings": round(cum_earnings, 2),
                "other": round(cum_other, 2),
            }
        )
        prev_cap = cur_cap

    return result or None
