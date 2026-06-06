"""TDCC (集保結算所) scraper for 股權分散表.

Fetches weekly shareholding distribution data directly from the TDCC website
(https://www.tdcc.com.tw/portal/zh/smWeb/qryStock) as a free fallback when
FinMind's TaiwanStockHoldingSharesPer is not accessible.

The TDCC website returns ~52 weeks (1 year) of history.
Each date requires a GET (for CSRF token) + POST pair.
Parallel fetching via ThreadPoolExecutor keeps latency acceptable (~7s for 52 weeks).
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Optional

import pandas as pd
import requests

_TDCC_URL = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
_MAX_WORKERS = 10
_REQUEST_TIMEOUT = 15


class TDCCError(RuntimeError):
    pass


class TDCCClient:
    """Scraper for TDCC 股權分散表 (weekly shareholding distribution)."""

    def fetch_available_dates(self) -> list[str]:
        """Return list of YYYYMMDD strings available in the TDCC date picker."""
        r = requests.get(_TDCC_URL, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
        if r.status_code != 200:
            raise TDCCError(f"TDCC GET failed: HTTP {r.status_code}")
        dates = re.findall(r'<option[^>]*value="(\d{8})"', r.text)
        if not dates:
            raise TDCCError("TDCC: no dates found in page")
        return dates

    def _fetch_snapshot(self, stock_id: str, sca_date: str) -> dict[str, float]:
        """Fetch one week's level→percent mapping.

        Returns dict {level_str: float_pct} for levels 1–15.
        Returns empty dict on failure so callers can skip gracefully.
        """
        session = requests.Session()
        try:
            r = session.get(_TDCC_URL, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
            csrf_m = re.search(r'name="SYNCHRONIZER_TOKEN" value="([^"]+)"', r.text)
            fir_m = re.search(r'name="firDate" value="([^"]+)"', r.text)
            if not csrf_m or not fir_m:
                return {}

            form = {
                "SYNCHRONIZER_TOKEN": csrf_m.group(1),
                "SYNCHRONIZER_URI": "/portal/zh/smWeb/qryStock",
                "method": "submit",
                "firDate": fir_m.group(1),
                "scaDate": sca_date,
                "sqlMethod": "StockNo",
                "stockNo": stock_id,
                "stockName": "",
            }
            h2 = {
                "User-Agent": _HEADERS["User-Agent"],
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": _TDCC_URL,
            }
            r2 = session.post(_TDCC_URL, data=form, headers=h2, timeout=_REQUEST_TIMEOUT)
            text = r2.content.decode("utf-8", errors="replace")

            result: dict[str, float] = {}
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.DOTALL):
                cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
                clean = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
                if len(clean) >= 5 and clean[0].strip().isdigit():
                    level = clean[0].strip()
                    try:
                        if 1 <= int(level) <= 15:
                            result[level] = float(clean[4].replace(",", ""))
                    except (ValueError, IndexError):
                        pass
            return result
        except Exception:
            return {}

    def fetch_shareholding_spread(
        self,
        stock_id: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """Fetch all weekly snapshots in [start_date, end_date] in parallel.

        Returns DataFrame with columns: date (datetime), HoldingSharesLevel (str), percent (float).
        Levels 1–15 only (same schema as FinMind TaiwanStockHoldingSharesPer).
        """
        try:
            available = self.fetch_available_dates()
        except TDCCError as exc:
            raise TDCCError(f"TDCC: cannot fetch date list: {exc}") from exc

        # Convert to datetime for comparison; skip index-0 (current-week pending data)
        def _to_date(s: str) -> Optional[date]:
            try:
                return datetime.strptime(s, "%Y%m%d").date()
            except ValueError:
                return None

        target = [
            d for d in available[1:]
            if (dt := _to_date(d)) is not None and start_date <= dt <= end_date
        ]

        if not target:
            return pd.DataFrame(columns=["date", "HoldingSharesLevel", "percent"])

        snapshots: dict[str, dict[str, float]] = {}
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = {pool.submit(self._fetch_snapshot, stock_id, d): d for d in target}
            for fut in as_completed(futures):
                d = futures[fut]
                snap = fut.result()
                if snap:
                    snapshots[d] = snap

        if not snapshots:
            return pd.DataFrame(columns=["date", "HoldingSharesLevel", "percent"])

        rows: list[dict] = []
        for date_str, snap in snapshots.items():
            dt = datetime.strptime(date_str, "%Y%m%d")
            for level, pct in snap.items():
                rows.append({"date": dt, "HoldingSharesLevel": level, "percent": pct})

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df["HoldingSharesLevel"] = df["HoldingSharesLevel"].astype(str)
        df["percent"] = pd.to_numeric(df["percent"], errors="coerce")
        return df.sort_values("date").reset_index(drop=True)
