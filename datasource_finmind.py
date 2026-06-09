from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import pandas as pd
import numpy as np
import requests


FINMIND_API = "https://api.finmindtrade.com/api/v4/data"

# FinMind throttles by stalling the TCP connection (so requests fail with a
# read timeout) rather than returning HTTP 429. A single 3 s back-off is not
# enough to clear the rate-limit window, so we retry several times with an
# exponential, jittered back-off. All knobs are env-tunable for slow networks
# or aggressive batch crawls.
_FINMIND_MAX_RETRIES = int(os.environ.get("FINMIND_MAX_RETRIES", "3"))
_FINMIND_BACKOFF_BASE = float(os.environ.get("FINMIND_BACKOFF_BASE", "3.0"))
_FINMIND_BACKOFF_MAX = float(os.environ.get("FINMIND_BACKOFF_MAX", "30.0"))
# HTTP statuses worth retrying (transient throttle / upstream hiccups).
_FINMIND_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# FinMind TaiwanStockHoldingSharesPer returns descriptive strings for HoldingSharesLevel.
# Map them to canonical numeric codes "1"–"15" used throughout this project.
_FINMIND_LEVEL_MAP: dict[str, str] = {
    "1-999": "1",
    "1,000-5,000": "2",
    "5,001-10,000": "3",
    "10,001-15,000": "4",
    "15,001-20,000": "5",
    "20,001-30,000": "6",
    "30,001-40,000": "7",
    "40,001-50,000": "8",
    "50,001-100,000": "9",
    "100,001-200,000": "10",
    "200,001-400,000": "11",
    "400,001-600,000": "12",
    "600,001-800,000": "13",
    "800,001-1,000,000": "14",
    "more than 1,000,001": "15",
}


class FinMindError(RuntimeError):
    pass


@dataclass
class FinMindClient:
    api_key: Optional[str] = None
    session: Optional[requests.Session] = None
    # Allow Android (or slow networks) to override via MICROECO_FINMIND_TIMEOUT env var.
    default_timeout: float = field(
        default_factory=lambda: float(os.environ.get("MICROECO_FINMIND_TIMEOUT", "30"))
    )
    # Per-request-session cache: deduplicates repeated fetches of the same dataset
    # (e.g. TaiwanStockBalanceSheet is used by 5+ methods in buy_score).
    _ds_cache: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.session is None:
            self.session = requests.Session()

    def _session_get_retry(
        self, params: dict, timeout: float, retries: int
    ) -> requests.Response:
        """GET FINMIND_API, retrying timeouts and transient HTTP errors.

        FinMind's rate limiter stalls connections instead of returning 429, so a
        read timeout is the dominant throttle signal. We widen the back-off on
        each attempt (3s, 6s, 12s … capped at _FINMIND_BACKOFF_MAX) so the
        rate-limit window can reset, and add jitter so concurrent crawls don't
        retry in lock-step. The final attempt re-raises / returns as-is.
        """
        last_exc: Exception | None = None
        for attempt in range(1 + retries):
            try:
                r = self.session.get(FINMIND_API, params=params, timeout=timeout)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_exc = exc
                if attempt >= retries:
                    raise
            else:
                if r.status_code not in _FINMIND_RETRY_STATUSES or attempt >= retries:
                    return r
                last_exc = None
            backoff = min(_FINMIND_BACKOFF_BASE * (2 ** attempt), _FINMIND_BACKOFF_MAX)
            time.sleep(backoff + random.uniform(0.0, 1.0))
        # Loop always returns or raises above; this satisfies type checkers.
        if last_exc is not None:
            raise last_exc
        raise FinMindError("FinMind request failed after retries")

    def _get_dataset(
        self,
        *,
        dataset: str,
        stock_id: str,
        start_date: date,
        end_date: date,
        timeout: float = 0,
        _retry: int = _FINMIND_MAX_RETRIES,
    ) -> list[dict]:
        if timeout <= 0:
            timeout = self.default_timeout

        # Deduplicate: same (dataset, stock, start, end) within one client instance
        # avoids re-fetching e.g. TaiwanStockBalanceSheet 5 times in buy_score.
        _cache_key = (dataset, stock_id, start_date.isoformat(), end_date.isoformat())
        if _cache_key in self._ds_cache:
            return self._ds_cache[_cache_key]

        params = {
            "dataset": dataset,
            "data_id": stock_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
        if self.api_key:
            params["token"] = self.api_key

        r = self._session_get_retry(params, timeout, _retry)

        # FinMind returns HTTP 400 {"msg":"Token is illegal."} when the token is
        # a website session JWT (no `exp`) instead of an API-login-issued JWT.
        # Retry without the token to fall back to unauthenticated (rate-limited) free access.
        if r.status_code == 400:
            try:
                msg = r.json().get("msg", "") or ""
            except Exception:
                msg = r.text
            if "illegal" in msg.lower() or "illegal" in r.text.lower():
                params_no_tok = {k: v for k, v in params.items() if k != "token"}
                r = self._session_get_retry(params_no_tok, timeout, _retry)

        if r.status_code == 402:
            raise FinMindError("FinMind quota exceeded (HTTP 402).")
        if r.status_code >= 400:
            raise FinMindError(f"FinMind request failed: HTTP {r.status_code} {r.text[:200]}")

        payload = r.json()
        if payload.get("status") != 200:
            raise FinMindError(f"FinMind API error: {payload}")

        data = payload.get("data", [])
        if not isinstance(data, list):
            raise FinMindError(f"FinMind API error: unexpected payload: {payload}")

        self._ds_cache[_cache_key] = data
        return data

    def fetch_month_revenue(self, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0) -> pd.DataFrame:
        data = self._get_dataset(
            dataset="TaiwanStockMonthRevenue",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
            timeout=timeout,
        )
        df = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame(columns=["month", "revenue"])

        # IMPORTANT: FinMind's `date` field for TaiwanStockMonthRevenue is the
        # *announcement* date (≈ the 10th of the month AFTER the revenue period),
        # not the revenue month itself. Deriving the month from `date` shifts
        # every figure forward by one month (e.g. May revenue announced Jun 10
        # would be mislabeled as June). The dataset carries the true period in
        # `revenue_year` / `revenue_month`, so use those when present.
        if "revenue_year" in df.columns and "revenue_month" in df.columns:
            ry = pd.to_numeric(df["revenue_year"], errors="coerce")
            rm = pd.to_numeric(df["revenue_month"], errors="coerce")
            month = pd.to_datetime(
                {"year": ry, "month": rm, "day": 1}, errors="coerce"
            )
            # Fall back to date-derived month only for rows missing the period fields.
            fallback = pd.to_datetime(df["date"], errors="coerce").dt.to_period("M").dt.to_timestamp(how="start")
            df["month"] = month.fillna(fallback)
        else:
            df["month"] = pd.to_datetime(df["date"]).dt.to_period("M").dt.to_timestamp(how="start")
        df["revenue"] = pd.to_numeric(df.get("revenue"), errors="coerce")
        df = df[["month", "revenue"]].dropna(subset=["month"]).drop_duplicates(subset=["month"], keep="last").sort_values("month")
        return df

    def fetch_stock_price(self, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0) -> pd.DataFrame:
        """Fetch daily OHLCV-like price data (FinMind dataset: TaiwanStockPrice)."""

        data = self._get_dataset(
            dataset="TaiwanStockPrice",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
            timeout=timeout,
        )
        df = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame(
                columns=[
                    "date",
                    "open",
                    "max",
                    "min",
                    "close",
                    "spread",
                    "Trading_Volume",
                    "Trading_money",
                    "Trading_turnover",
                ]
            )

        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        for col in ["open", "max", "min", "close", "spread", "Trading_Volume", "Trading_money", "Trading_turnover"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        keep_cols = [
            c
            for c in [
                "date",
                "open",
                "max",
                "min",
                "close",
                "spread",
                "Trading_Volume",
                "Trading_money",
                "Trading_turnover",
            ]
            if c in df.columns
        ]

        df = df[keep_cols].dropna(subset=["date"]).drop_duplicates(subset=["date"], keep="last").sort_values("date")
        return df

    def fetch_institutional_investors_buy_sell(
        self,
        stock_id: str,
        start_date: date,
        end_date: date,
        timeout: float = 30.0,
    ) -> pd.DataFrame:
        """Fetch institutional investors buy/sell (FinMind dataset: TaiwanStockInstitutionalInvestorsBuySell).

        Returns a dataframe with at least: date, name, buy, sell, net.
        """

        data = self._get_dataset(
            dataset="TaiwanStockInstitutionalInvestorsBuySell",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
            timeout=timeout,
        )
        df = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame(columns=["date", "name", "buy", "sell", "net"])

        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["buy"] = pd.to_numeric(df.get("buy"), errors="coerce")
        df["sell"] = pd.to_numeric(df.get("sell"), errors="coerce")
        df["name"] = df.get("name").astype(str)
        df["net"] = df["buy"].fillna(0) - df["sell"].fillna(0)

        df = df.dropna(subset=["date"])
        df = df[["date", "name", "buy", "sell", "net"]].sort_values(["date", "name"])
        return df

    def fetch_stock_name(self, stock_id: str, timeout: float = 20.0) -> Optional[str]:
        """Fetch a stock's Chinese name from FinMind.

        Uses dataset `TaiwanStockInfo` if available.
        Returns None when not found or API doesn't provide the dataset.
        """

        params = {
            "dataset": "TaiwanStockInfo",
            "data_id": stock_id,
        }
        if self.api_key:
            params["token"] = self.api_key

        r = self.session.get(FINMIND_API, params=params, timeout=timeout)
        if r.status_code == 402:
            # quota exceeded; treat as non-fatal for name lookup
            return None
        if r.status_code >= 400:
            return None

        try:
            payload = r.json()
        except Exception:
            return None

        if payload.get("status") != 200:
            return None

        data = payload.get("data") or []
        if not data:
            return None

        # Expect fields like: stock_id, stock_name
        first = data[0]
        name = first.get("stock_name") or first.get("name")
        if not name:
            return None
        return str(name).strip() or None

    def fetch_stock_industry(self, stock_id: str, timeout: float = 20.0) -> Optional[str]:
        """Fetch a stock's industry category from FinMind TaiwanStockInfo.

        Returns the industry_category string (e.g. '半導體業', '金融保險業') or None.
        """
        params = {"dataset": "TaiwanStockInfo", "data_id": stock_id}
        if self.api_key:
            params["token"] = self.api_key
        try:
            r = self.session.get(FINMIND_API, params=params, timeout=timeout)
        except Exception:
            return None
        if r.status_code >= 400:
            return None
        try:
            payload = r.json()
        except Exception:
            return None
        if payload.get("status") != 200:
            return None
        data = payload.get("data") or []
        if not data:
            return None
        first = data[0]
        industry = first.get("industry_category") or first.get("industry")
        if not industry:
            return None
        return str(industry).strip() or None

    def fetch_stock_per(self, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0) -> pd.DataFrame:
        """Fetch daily PER/PBR/dividend_yield (FinMind dataset: TaiwanStockPER)."""

        data = self._get_dataset(
            dataset="TaiwanStockPER",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
            timeout=timeout,
        )
        df = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame(columns=["date", "dividend_yield", "PER", "PBR"])

        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        for col in ["dividend_yield", "PER", "PBR"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df.get(col), errors="coerce")

        keep_cols = [c for c in ["date", "dividend_yield", "PER", "PBR"] if c in df.columns]
        df = df[keep_cols].dropna(subset=["date"]).drop_duplicates(subset=["date"], keep="last").sort_values("date")
        return df

    def fetch_stock_dividend(self, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0) -> pd.DataFrame:
        """Fetch dividend policy and compute cash dividend per share (FinMind dataset: TaiwanStockDividend).

        Returns dataframe with: year (int), ex_dividend_date (datetime), cash_dividend (float).
        """

        data = self._get_dataset(
            dataset="TaiwanStockDividend",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
            timeout=timeout,
        )
        df = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame(columns=["year", "ex_dividend_date", "cash_dividend"])

        # Prefer cash ex-dividend trading date
        ex_col = "CashExDividendTradingDate" if "CashExDividendTradingDate" in df.columns else "date"
        ex_raw = df.get(ex_col)
        if ex_raw is None:
            ex_raw = df.get("date")
        # some FinMind fields can be empty strings; coerce them to NaT
        df["ex_dividend_date"] = pd.to_datetime(ex_raw, errors="coerce")

        # cash dividend components (per share)
        cash_earn = pd.to_numeric(df.get("CashEarningsDistribution"), errors="coerce")
        cash_surplus = pd.to_numeric(df.get("CashStatutorySurplus"), errors="coerce")
        df["cash_dividend"] = cash_earn.fillna(0) + cash_surplus.fillna(0)

        # FinMind 'year' may be strings like '104年' (Minguo year). For user display and grouping,
        # prefer the Gregorian year of the ex-dividend date (the actual event year).
        year_from_ex = df["ex_dividend_date"].dt.year
        if year_from_ex.notna().any():
            df["year"] = year_from_ex
        else:
            year_raw = df.get("year")
            if year_raw is None:
                df["year"] = pd.NA
            else:
                year_digits = year_raw.astype(str).str.extract(r"(\d+)")[0]
                year_num = pd.to_numeric(year_digits, errors="coerce")
                # if it's Minguo year (e.g. 104), convert to AD (1911+)
                year_ad = year_num.where(year_num >= 1911, year_num + 1911)
                df["year"] = year_ad

        df["year"] = pd.to_numeric(df.get("year"), errors="coerce")
        df = df.dropna(subset=["year"]).copy()
        df["year"] = df["year"].astype(int)

        df = df[["year", "ex_dividend_date", "cash_dividend"]]
        df = df.drop_duplicates(subset=["year", "ex_dividend_date", "cash_dividend"], keep="last").sort_values(
            ["year", "ex_dividend_date"]
        )
        return df

    def fetch_financial_statements(self, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0) -> pd.DataFrame:
        """Fetch quarterly financial statement ratios (FinMind dataset: TaiwanStockFinancialStatements).

        Returns a DataFrame with columns: date (quarter end), type, value.
        Useful types include ROE and ROA.
        """

        data = self._get_dataset(
            dataset="TaiwanStockFinancialStatements",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
            timeout=timeout,
        )
        df = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame(columns=["date", "type", "value"])

        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["value"] = pd.to_numeric(df.get("value"), errors="coerce")
        df["type"] = df.get("type").astype(str)

        df = df.dropna(subset=["date", "value"])
        df = df[["date", "type", "value"]].sort_values("date")
        return df


    def fetch_shareholding_spread(self, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0) -> pd.DataFrame:
        """Fetch TaiwanStockHoldingSharesPer (集保股權分散表).

        Returns DataFrame with columns: date, HoldingSharesLevel (str "1"–"15"), percent (float).
        Rows for 'total' and 'difference' are dropped; only the 15 lot brackets are kept.
        """
        try:
            data = self._get_dataset(
                dataset="TaiwanStockHoldingSharesPer",
                stock_id=stock_id,
                start_date=start_date,
                end_date=end_date,
                timeout=timeout,
            )
        except FinMindError as exc:
            msg = str(exc).lower()
            if "level is register" in msg or "sponsor" in msg:
                return pd.DataFrame(columns=["date", "HoldingSharesLevel", "percent"])
            raise

        df = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame(columns=["date", "HoldingSharesLevel", "percent"])

        df["date"] = pd.to_datetime(df["date"])
        df["percent"] = pd.to_numeric(df["percent"], errors="coerce")
        # Normalize FinMind's descriptive level strings → canonical "1"–"15".
        # Rows that don't map (total / difference) are dropped via dropna.
        df["HoldingSharesLevel"] = df["HoldingSharesLevel"].astype(str).map(_FINMIND_LEVEL_MAP)
        df = df.dropna(subset=["HoldingSharesLevel"])
        return df[["date", "HoldingSharesLevel", "percent"]].copy()

    def fetch_balance_sheet(self, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0) -> pd.DataFrame:
        """Fetch quarterly balance sheet (FinMind dataset: TaiwanStockBalanceSheet).

        Returns DataFrame with columns: date (quarter end), type, value.
        Relevant types: TotalAssets, StockholdersEquity / EquityAttributableToOwnersOfParent.
        """

        data = self._get_dataset(
            dataset="TaiwanStockBalanceSheet",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
            timeout=timeout,
        )
        df = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame(columns=["date", "type", "value"])

        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["value"] = pd.to_numeric(df.get("value"), errors="coerce")
        df["type"] = df.get("type").astype(str)

        df = df.dropna(subset=["date", "value"])
        return df[["date", "type", "value"]].sort_values("date")

    def fetch_income_statement(self, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0) -> pd.DataFrame:
        """Fetch quarterly income statement (FinMind dataset: TaiwanStockFinancialStatements).

        FinMind does NOT have a dataset called 'TaiwanStockIncomeStatement'; the P&L line items
        live in 'TaiwanStockFinancialStatements' (綜合損益表).
        Returns DataFrame with columns: date (quarter end), type, value.
        IMPORTANT: Taiwan reports cumulative YTD figures (e.g. Q2 value = Q1+Q2 income).
        Relevant types: NetIncome / ProfitLossAttributableToOwnersOfParent.
        """

        data = self._get_dataset(
            dataset="TaiwanStockFinancialStatements",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
            timeout=timeout,
        )
        df = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame(columns=["date", "type", "value"])

        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["value"] = pd.to_numeric(df.get("value"), errors="coerce")
        df["type"] = df.get("type").astype(str)

        df = df.dropna(subset=["date", "value"])
        return df[["date", "type", "value"]].sort_values("date")


    def fetch_inventory_and_revenue_growth(self, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0) -> Optional[dict]:
        """
        Gets Inventory and Revenue Data to compare YoY growth.
        """
        bs = self.fetch_balance_sheet(stock_id, start_date, end_date, timeout)
        inc = self.fetch_financial_statements(stock_id, start_date, end_date, timeout)
        
        if bs.empty or inc.empty:
            return None
            
        def _get_last_quarter_val(df_bs, types):
            for t in types:
                sub = df_bs[df_bs["type"] == t].drop_duplicates(subset=["date"], keep="last")
                if not sub.empty:
                    ts = pd.Series(sub["value"].values, index=pd.to_datetime(sub["date"])).sort_index()
                    return ts
            return pd.Series(dtype=float)

        inv_ts = _get_last_quarter_val(bs, ["Inventories", "Inventory", "InventoriesNet"])
        rev_ts = _get_last_quarter_val(inc, ["OperatingRevenue", "Revenue", "GrossSales", "NetSales"])
        
        if len(inv_ts) >= 5 and len(rev_ts) >= 5:
            inv_latest = inv_ts.iloc[-1]
            inv_last_yr = inv_ts.tail(5).iloc[0] # Roughly YoY for quarters
            
            rev_latest = rev_ts.iloc[-1]
            rev_last_yr = rev_ts.tail(5).iloc[0]
            
            if inv_last_yr > 0 and rev_last_yr > 0:
                inv_yoy = ((inv_latest / inv_last_yr) - 1) * 100
                rev_yoy = ((rev_latest / rev_last_yr) - 1) * 100
                return {"inv_yoy": inv_yoy, "rev_yoy": rev_yoy}
                
        return None

    def fetch_cash_flows_statement(self, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0) -> pd.DataFrame:
        """Fetch quarterly cash flow statement (FinMind dataset: TaiwanStockCashFlowsStatement).

        Returns DataFrame with columns: date (quarter end), type, value.
        Useful types include:
        - NetCashInflowFromOperatingActivities
        - CashFlowsFromOperatingActivities
        - PropertyAndPlantAndEquipment
        """

        data = self._get_dataset(
            dataset="TaiwanStockCashFlowsStatement",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
            timeout=timeout,
        )
        df = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame(columns=["date", "type", "value"])

        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["value"] = pd.to_numeric(df.get("value"), errors="coerce")
        df["type"] = df.get("type").astype(str)

        df = df.dropna(subset=["date", "value"])
        return df[["date", "type", "value"]].sort_values("date")

    def fetch_quarterly_ni(
        self, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0
    ) -> pd.Series:
        """Single-quarter net income (NT$元) indexed by quarter-end Timestamp.

        Uses ``IncomeAfterTaxes`` (本期淨利) from TaiwanStockFinancialStatements.
        FinMind already returns per-quarter values — no de-accumulation needed.
        """
        df = self.fetch_income_statement(stock_id, start_date, end_date, timeout)
        sub = df[df["type"] == "IncomeAfterTaxes"].drop_duplicates(subset=["date"], keep="last")
        return pd.Series(sub["value"].values, index=pd.to_datetime(sub["date"]), dtype=float).sort_index()

    def fetch_quarterly_bs_for_roe(
        self, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0
    ) -> tuple[pd.Series, pd.Series]:
        """Quarter-end equity and total-assets (NT$元) indexed by quarter-end Timestamp.

        equity → ``EquityAttributableToOwnersOfParent`` (歸屬於母公司業主之權益合計)
        assets → ``TotalAssets``
        """
        df = self.fetch_balance_sheet(stock_id, start_date, end_date, timeout)

        def _series(type_name: str) -> pd.Series:
            sub = df[df["type"] == type_name].drop_duplicates(subset=["date"], keep="last")
            return pd.Series(sub["value"].values, index=pd.to_datetime(sub["date"]), dtype=float).sort_index()

        equity = _series("EquityAttributableToOwnersOfParent")
        # Some stocks only have consolidated Equity without the parent-only breakdown
        if equity.empty:
            equity = _series("Equity")
        assets = _series("TotalAssets")
        return equity, assets

    def fetch_dividend_payout_ratio(self, stock_id: str, start_date: date, end_date: date, years: int = 5, timeout: float = 30.0) -> Optional[float]:
        """
        計算股利配息率 = 最近 N 年現金股息 / 最近 N 年年度 EPS（4季度累加）
        
        Returns:
            average_payout_ratio: 5年平均配息率（範圍 0-1），若無法計算則返回 None
        """
        try:
            # 獲取最近的現金股息
            dividend_df = self.fetch_stock_dividend(stock_id, start_date, end_date, timeout)
            if dividend_df.empty:
                return None
            
            # 獲取 EPS 資料
            fin_df = self.fetch_financial_statements(stock_id, start_date, end_date, timeout)
            if fin_df.empty:
                return None
            
            # 提取 EPS（季度數據）
            eps_df = fin_df[fin_df["type"] == "EPS"].copy()
            if eps_df.empty:
                return None
            
            eps_df["date"] = pd.to_datetime(eps_df["date"])
            eps_df = eps_df.sort_values("date").drop_duplicates("date", keep="last")
            eps_df["year"] = eps_df["date"].dt.year
            
            # 計算年度 EPS（4 季度累加），僅採用完整年度（至少 4 季）
            eps_yearly = (
                eps_df.groupby("year")
                .agg(annual_eps=("value", "sum"), quarter_count=("value", "count"))
                .reset_index()
            )
            eps_df_grouped = eps_yearly[eps_yearly["quarter_count"] >= 4][["year", "annual_eps"]].copy()
            if eps_df_grouped.empty:
                return None
            
            # 配對股息與年度 EPS（按年度）
            dividend_df["year"] = pd.to_numeric(dividend_df["year"], errors="coerce").astype(int)
            
            # 只保留最近 N 年資料
            recent_year = max(dividend_df["year"].max(), eps_df_grouped["year"].max())
            start_year = recent_year - years + 1
            
            dividend_recent = dividend_df[dividend_df["year"] >= start_year].copy()
            eps_recent = eps_df_grouped[eps_df_grouped["year"] >= start_year].copy()
            
            if dividend_recent.empty or eps_recent.empty:
                return None
            
            # 按年彙總股息
            dividend_by_year = dividend_recent.groupby("year")["cash_dividend"].sum()
            
            # 計算當年配息率
            common_years = sorted(set(dividend_by_year.index) & set(eps_recent["year"]))
            if not common_years:
                return None
            
            payout_ratios = []
            for year in common_years:
                dividend = float(dividend_by_year[year])
                eps_row = eps_recent[eps_recent["year"] == year]
                if not eps_row.empty:
                    eps = float(eps_row.iloc[0]["annual_eps"])
                    if eps > 0:
                        ratio = dividend / eps
                        if 0 <= ratio <= 3:  # 過濾異常值
                            payout_ratios.append(ratio)
            
            if not payout_ratios:
                return None
            
            # 返回平均配息率
            avg_payout = np.mean(payout_ratios)
            return float(avg_payout)
            
        except Exception:
            return None

    def fetch_annual_eps(self, stock_id: str, start_date: date, end_date: date, years: int = 5, timeout: float = 30.0) -> Optional[float]:
        """
        獲取最近 N 年完整年度 EPS 的平均值（每年 4 季度累加）
        
        Returns:
            annual_eps: 最近 N 年平均年度 EPS，若無法計算則返回 None
        """
        try:
            fin_df = self.fetch_financial_statements(stock_id, start_date, end_date, timeout)
            if fin_df.empty:
                return None
            
            # 提取 EPS（季度數據）
            eps_df = fin_df[fin_df["type"] == "EPS"].copy()
            if eps_df.empty:
                return None
            
            eps_df["date"] = pd.to_datetime(eps_df["date"])
            eps_df = eps_df.sort_values("date").drop_duplicates("date", keep="last")
            eps_df["year"] = eps_df["date"].dt.year
            
            # 計算年度 EPS（4 季度累加），僅採用完整年度（至少 4 季）
            eps_yearly = (
                eps_df.groupby("year")
                .agg(annual_eps=("value", "sum"), quarter_count=("value", "count"))
                .reset_index()
            )
            eps_full_year = eps_yearly[eps_yearly["quarter_count"] >= 4].copy()
            if eps_full_year.empty:
                return None

            recent_eps = eps_full_year.sort_values("year").tail(years)["annual_eps"]
            if recent_eps.empty:
                return None
            return float(recent_eps.mean())
            
        except Exception:
            return None

    # ------------------------------------------------------------------
    # New: Debt Ratio, FCF, Director Shareholding
    # ------------------------------------------------------------------

    def fetch_quarterly_bs_liabilities_assets(
        self, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0
    ) -> tuple[pd.Series, pd.Series]:
        """Quarter-end TotalLiabilities and TotalAssets for debt ratio.

        Returns (liabilities_series, assets_series) indexed by quarter-end Timestamp.
        """
        df = self.fetch_balance_sheet(stock_id, start_date, end_date, timeout)
        if df.empty:
            return pd.Series(dtype=float), pd.Series(dtype=float)

        def _series(type_name: str) -> pd.Series:
            sub = df[df["type"] == type_name].drop_duplicates(subset=["date"], keep="last")
            return pd.Series(sub["value"].values, index=pd.to_datetime(sub["date"]), dtype=float).sort_index()

        liabilities = _series("TotalLiabilities")
        if liabilities.empty:
            liabilities = _series("Liabilities")
        assets = _series("TotalAssets")
        return liabilities, assets

    def fetch_annual_fcf_data(
        self, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0
    ) -> tuple[pd.DataFrame, Optional[float]]:
        """Annual Free Cash Flow = Operating CF - |CapEx|.

        Taiwan cash-flow statements are cumulative YTD. For each year we pick the
        *latest available* quarter-end so the in-progress current year stays
        visible (marked is_full_year=False); fully reported years use Q4.

        Returns:
            (df, share_capital)
            df columns: [year, operating_cf, capex, fcf, quarter_label, is_full_year]
            share_capital: IssuedCapital from balance sheet (NT$千) or None
        """
        cf = self.fetch_cash_flows_statement(stock_id, start_date, end_date, timeout)
        bs = self.fetch_balance_sheet(stock_id, start_date, end_date, timeout)

        empty_df = pd.DataFrame(columns=["year", "operating_cf", "capex", "fcf", "quarter_label", "is_full_year"])

        if cf.empty:
            return empty_df, None

        opcf_series, capex_series = self._extract_cf_series(cf)
        if opcf_series is None:
            return empty_df, None

        rows = []
        years_seen = sorted({int(dt.year) for dt in opcf_series.index})
        for year in years_seen:
            year_opcf = opcf_series[opcf_series.index.year == year]
            if year_opcf.empty:
                continue
            dt = max(year_opcf.index)  # latest available quarter-end in this year
            opcf_val = year_opcf[dt]
            if pd.isna(opcf_val):
                continue

            capex_val = capex_series.get(dt) if capex_series is not None and dt in capex_series.index else None
            capex_abs = abs(float(capex_val)) if capex_val is not None and not pd.isna(capex_val) else 0.0
            fcf = float(opcf_val) - capex_abs

            q_num = (dt.month - 1) // 3 + 1
            rows.append(
                {
                    "year": year,
                    "operating_cf": float(opcf_val),
                    "capex": capex_abs if capex_val is not None else None,
                    "fcf": fcf,
                    "quarter_label": f"{year} Q{q_num}",
                    "is_full_year": dt.month == 12,
                }
            )

        result_df = pd.DataFrame(rows).sort_values("year") if rows else empty_df

        # ── Share capital ───────────────────────────────────────────────
        share_capital: Optional[float] = None
        if not bs.empty:
            for cap_type in ["IssuedCapital", "OrdinaryShares", "CommonStock", "CapitalStock"]:
                sub = bs[bs["type"] == cap_type].drop_duplicates(subset=["date"], keep="last")
                if not sub.empty:
                    share_capital = float(sub.iloc[-1]["value"])
                    break

        return result_df, share_capital

    @staticmethod
    def _extract_cf_series(cf: pd.DataFrame) -> tuple["pd.Series | None", "pd.Series | None"]:
        """Pull operating-CF and CapEx series (NT$元, cumulative YTD) from a cash-flow DataFrame.

        FinMind relabels the same line item across years (e.g. operating CF is
        ``CashFlowsFromOperatingActivities`` pre-2022 and
        ``NetCashInflowFromOperatingActivities`` from 2022 on). Picking only the first
        non-empty label silently caps history at whichever label is most recent — so
        we merge all candidate labels into one continuous series.
        """
        opcf_types = [
            "NetCashInflowFromOperatingActivities",
            "CashFlowsFromOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivities",
            "OperatingActivities",
        ]
        capex_types = [
            "AcquisitionOfPropertyPlantAndEquipment",
            "CashOutflowForAcquisitionOfPropertyPlantAndEquipment",
            "PaymentsForAcquisitionOfPropertyPlantAndEquipment",
            "PropertyAndPlantAndEquipment",
            "PurchaseOfPropertyPlantAndEquipmentIntangibleAssetsAndOtherLongTermAssets",
        ]

        def _merge(types: list[str]) -> "pd.Series | None":
            merged: pd.Series | None = None
            for t in types:
                sub = cf[cf["type"] == t].drop_duplicates(subset=["date"], keep="last")
                if sub.empty:
                    continue
                s = pd.Series(sub["value"].values, index=pd.to_datetime(sub["date"]), dtype=float).sort_index()
                merged = s if merged is None else merged.combine_first(s)
            return merged.sort_index() if merged is not None else None

        return _merge(opcf_types), _merge(capex_types)

    def fetch_quarterly_fcf_data(
        self, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0
    ) -> pd.DataFrame:
        """Quarterly **cumulative (YTD)** Free Cash Flow = Operating CF − |CapEx|.

        Taiwan cash-flow statements are reported cumulatively within each fiscal
        year (Q1, H1, 9M, FY); each quarter's value is the year-to-date running
        total. Surfaces every filed quarter — many more data points than the
        annual (Q4-only) view.

        Returns DataFrame with columns:
          quarter (str date), quarter_label (e.g. '2025 Q3'),
          operating_cf, capex, fcf  (all NT$元, cumulative within the fiscal year)
        """
        cols = ["quarter", "quarter_label", "operating_cf", "capex", "fcf"]
        cf = self.fetch_cash_flows_statement(stock_id, start_date, end_date, timeout)
        if cf.empty:
            return pd.DataFrame(columns=cols)

        opcf_series, capex_series = self._extract_cf_series(cf)
        if opcf_series is None:
            return pd.DataFrame(columns=cols)

        quarter_dates = sorted(
            dt for dt in opcf_series.index if not pd.isna(dt) and dt.month in {3, 6, 9, 12}
        )

        rows = []
        for dt in quarter_dates:
            opcf_val = opcf_series.get(dt)
            if opcf_val is None or pd.isna(opcf_val):
                continue
            capex_val = capex_series.get(dt) if capex_series is not None and dt in capex_series.index else None
            capex_abs = abs(float(capex_val)) if capex_val is not None and not pd.isna(capex_val) else 0.0
            q_num = (dt.month - 1) // 3 + 1
            rows.append({
                "quarter": str(dt.date()),
                "quarter_label": f"{dt.year} Q{q_num}",
                "operating_cf": float(opcf_val),
                "capex": capex_abs if capex_val is not None else None,
                "fcf": float(opcf_val) - capex_abs,
            })

        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=cols)

    def fetch_director_shareholding_latest(
        self, stock_id: str, timeout: float = 30.0
    ) -> pd.DataFrame:
        """Director shareholding is not available in FinMind.

        ``TaiwanStockDirectorShareholding`` does not exist in FinMind's dataset
        catalogue. Returns empty DataFrame immediately so the caller can use its
        MOPS/TWSE/TPEx fallback without wasting an API token request.
        """
        return pd.DataFrame()

    def fetch_user_info(self, timeout: float = 10.0) -> dict[str, Any]:
        """Fetch user info including API token usage."""
        url = "https://api.web.finmindtrade.com/v2/user_info"
        token = (self.api_key or "").strip()

        # FinMind user_info expects POST on current endpoint; keep a GET fallback
        # for compatibility if upstream behavior changes again.
        r = self.session.post(url, data={"token": token}, timeout=timeout)
        if r.status_code == 405:
            params = {"token": token} if token else {}
            r = self.session.get(url, params=params, timeout=timeout)

        if r.status_code >= 400:
            raise FinMindError(f"FinMind user_info request failed: HTTP {r.status_code} {r.text[:200]}")

        try:
            payload = r.json()
        except Exception as exc:
            raise FinMindError("FinMind user_info response is not valid JSON") from exc

        if payload.get("status") != 200:
            raise FinMindError(f"FinMind user_info error: {payload}")
        return payload

    # ------------------------------------------------------------------
    # Turnover days (DSO / DIO / DPO / CCC) — quarterly
    # ------------------------------------------------------------------

    def fetch_turnover_days_data(
        self,
        stock_id: str,
        start_date: date,
        end_date: date,
        timeout: float = 30.0,
    ) -> pd.DataFrame:
        """Compute quarterly operating turnover days.

        DSO  = AccountsReceivable / (OperatingRevenue / 91)     — 應收款天數
        DIO  = Inventories         / (OperatingCosts   / 91)     — 存貨天數
        DPO  = AccountsPayable     / (OperatingCosts   / 91)     — 應付款天數
        CCC  = DSO + DIO - DPO                                    — 現金轉換循環

        Returns DataFrame with columns:
          quarter, quarter_label, dso, dio, dpo, ccc
        All values in days (float, rounded to 1 decimal).
        Returns empty DataFrame when data is insufficient.
        """
        # Fetch balance sheet and income statement for the period (+1 year warmup)
        bs = self.fetch_balance_sheet(stock_id, start_date, end_date, timeout)
        inc = self.fetch_financial_statements(stock_id, start_date, end_date, timeout)

        if bs.empty or inc.empty:
            return pd.DataFrame(columns=["quarter", "quarter_label", "dso", "dio", "dpo", "ccc"])

        def _series(df: pd.DataFrame, *type_names: str) -> pd.Series:
            """Try type names in order, returning the first non-empty series."""
            for t in type_names:
                sub = df[df["type"] == t].drop_duplicates(subset=["date"], keep="last")
                if not sub.empty:
                    return pd.Series(
                        sub["value"].values,
                        index=pd.to_datetime(sub["date"]),
                        dtype=float,
                    ).sort_index()
            return pd.Series(dtype=float)

        # Balance sheet items
        ar = _series(
            bs,
            "AccountsReceivableNet",          # actual FinMind IFRS type
            "NotesReceivableNet",
            "AccountsAndNotesReceivable",
            "AccountsReceivable",
            "ReceivablesNet",
            "NotesAndAccountsReceivable",
            "TradeReceivables",
        )
        inv = _series(
            bs,
            "Inventories",
            "Inventory",
            "InventoriesNet",
        )
        ap = _series(
            bs,
            "AccountsAndNotesPayable",
            "AccountsPayable",
            "NotesAndAccountsPayable",
            "TradePayables",
        )

        # Income statement items (quarterly; FinMind reports single-quarter values)
        rev = _series(
            inc,
            "OperatingRevenue",
            "Revenue",
            "NetRevenue",
            "SalesOfGoods",
            "TotalRevenue",
        )
        cogs = _series(
            inc,
            "OperatingCosts",
            "CostOfGoodsSold",
            "CostOfRevenue",
            "CostOfSales",
        )

        # Revenue fallback: derive from GrossProfit + CostOfGoodsSold if direct lookup failed
        if rev.empty and not cogs.empty:
            gross = _series(inc, "GrossProfit", "GrossProfitLoss")
            if not gross.empty:
                common_idx = gross.index.intersection(cogs.index)
                if len(common_idx):
                    rev = (gross[common_idx] + cogs[common_idx]).sort_index()

        if rev.empty and cogs.empty:
            return pd.DataFrame(columns=["quarter", "quarter_label", "dso", "dio", "dpo", "ccc"])

        # Build quarterly date list from balance sheet
        quarter_dates = sorted(
            dt for dt in bs["date"].unique()
            if not pd.isna(dt) and pd.Timestamp(dt).month in {3, 6, 9, 12}
        )

        rows = []
        days_in_q = 91.0  # approximate days per quarter

        for dt in quarter_dates:
            ts = pd.Timestamp(dt)
            rev_val = rev.get(ts)
            cogs_val = cogs.get(ts)
            ar_val = ar.get(ts)
            inv_val = inv.get(ts)
            ap_val = ap.get(ts)

            # DSO
            dso: float | None = None
            if ar_val is not None and not pd.isna(ar_val) and rev_val is not None and not pd.isna(rev_val) and float(rev_val) > 0:
                dso = round(float(ar_val) / (float(rev_val) / days_in_q), 1)

            # DIO
            dio: float | None = None
            if inv_val is not None and not pd.isna(inv_val) and cogs_val is not None and not pd.isna(cogs_val) and float(cogs_val) > 0:
                dio = round(float(inv_val) / (float(cogs_val) / days_in_q), 1)

            # DPO
            dpo: float | None = None
            if ap_val is not None and not pd.isna(ap_val) and cogs_val is not None and not pd.isna(cogs_val) and float(cogs_val) > 0:
                dpo = round(float(ap_val) / (float(cogs_val) / days_in_q), 1)

            # CCC
            ccc: float | None = None
            if dso is not None and dpo is not None:
                # DIO optional — treat as 0 if missing
                ccc = round(dso + (dio or 0.0) - dpo, 1)

            # Only include quarters where at least DSO or CCC can be computed
            if dso is None and ccc is None:
                continue

            q_num = (ts.month - 1) // 3 + 1
            rows.append({
                "quarter": str(ts.date()),
                "quarter_label": f"{ts.year} Q{q_num}",
                "dso": dso,
                "dio": dio,
                "dpo": dpo,
                "ccc": ccc,
            })

        return pd.DataFrame(rows) if rows else pd.DataFrame(
            columns=["quarter", "quarter_label", "dso", "dio", "dpo", "ccc"]
        )

    # ------------------------------------------------------------------
    # P/E River chart data — historical PER + percentile bands
    # ------------------------------------------------------------------

    def fetch_pe_river_data(
        self,
        stock_id: str,
        start_date: date,
        end_date: date,
        timeout: float = 30.0,
    ) -> dict:
        """Fetch data for P/E river chart.

        Returns dict with:
          per_rows    : list of {date, per, price}
          bands       : {p5, p25, p50, p75, p95}  — percentile levels of PER
          price_rows  : list of {date, price}       — monthly close for band price conversion
          eps_ttm_rows: list of {quarter, quarter_label, eps_ttm}  — TTM EPS per quarter
        """
        import logging

        # ── Daily PER history ──────────────────────────────────────────────────
        df_per = self.fetch_stock_per(stock_id, start_date, end_date, timeout)

        per_rows: list[dict] = []
        valid_per_values: list[float] = []

        if not df_per.empty and "PER" in df_per.columns:
            for _, row in df_per.iterrows():
                per_val = row.get("PER")
                if per_val is None or pd.isna(per_val) or float(per_val) <= 0:
                    continue
                per_rows.append({
                    "date": str(pd.Timestamp(row["date"]).date()),
                    "per": round(float(per_val), 2),
                })
                valid_per_values.append(float(per_val))

        # ── Percentile bands ──────────────────────────────────────────────────
        bands: dict = {}
        if valid_per_values:
            arr = np.array(valid_per_values)
            bands = {
                "p5":  round(float(np.percentile(arr, 5)), 2),
                "p25": round(float(np.percentile(arr, 25)), 2),
                "p50": round(float(np.percentile(arr, 50)), 2),
                "p75": round(float(np.percentile(arr, 75)), 2),
                "p95": round(float(np.percentile(arr, 95)), 2),
            }

        # ── TTM EPS per quarter ────────────────────────────────────────────────
        eps_ttm_rows: list[dict] = []
        try:
            ni_q = self.fetch_quarterly_ni(stock_id, start_date, end_date, timeout)
            quarter_dates = sorted(
                dt for dt in ni_q.index if not pd.isna(dt) and dt.month in {3, 6, 9, 12}
            )
            for i, dt in enumerate(quarter_dates):
                if i < 3:
                    continue
                last4 = quarter_dates[i - 3: i + 1]
                ni_vals = [ni_q.get(d) for d in last4]
                if any(v is None or pd.isna(v) for v in ni_vals):
                    continue
                ttm_ni = sum(float(v) for v in ni_vals)  # type: ignore[arg-type]

                # Get share count from balance sheet to convert to per-share
                bs = self.fetch_balance_sheet(stock_id, start_date, end_date, timeout)
                share_data = bs[bs["type"].isin(["IssuedCapital", "OrdinaryShares", "CommonStock", "CapitalStock"])]
                share_late = share_data[share_data["date"] <= dt]
                if not share_late.empty:
                    # IssuedCapital is in NT$千; divide by 10 to get shares (千股/10 = 萬股)
                    # EPS formula: NI (元) / shares (千股 × 1000 / 1000) = NI / (capital / 1000)
                    capital_k = float(share_late.iloc[-1]["value"])
                    # capital_k is NT$千 → number of shares = capital_k * 1000 / 10 = capital_k * 100
                    shares = capital_k * 1000  # shares in units
                    eps_ttm = round(ttm_ni / shares, 2) if shares > 0 else None
                else:
                    eps_ttm = None

                q_num = (dt.month - 1) // 3 + 1
                eps_ttm_rows.append({
                    "quarter": str(dt.date()),
                    "quarter_label": f"{dt.year} Q{q_num}",
                    "eps_ttm": eps_ttm,
                })
        except Exception as exc:
            logging.warning("fetch_pe_river_data: EPS TTM failed: %s", exc)

        return {
            "per_rows": per_rows,
            "bands": bands,
            "eps_ttm_rows": eps_ttm_rows,
        }

    # ------------------------------------------------------------------
    # === NEW: Margin Ratios (毛利率 / 營業利益率 / 淨利率) ===
    # ------------------------------------------------------------------

    def fetch_margin_ratios(
        self,
        stock_id: str,
        start_date: date,
        end_date: date,
        timeout: float = 30.0,
    ) -> pd.DataFrame:
        """Quarterly gross / operating / net margin (%).

        Returns DataFrame with columns:
          quarter (str), quarter_label (str),
          gross_margin (%), operating_margin (%), net_margin (%)
        All values 0-100 scale; None when data unavailable.
        """
        inc = self.fetch_financial_statements(stock_id, start_date, end_date, timeout)
        if inc.empty:
            return pd.DataFrame(columns=["quarter", "quarter_label", "gross_margin", "operating_margin", "net_margin"])

        def _s(*type_names: str) -> pd.Series:
            for t in type_names:
                sub = inc[inc["type"] == t].drop_duplicates(subset=["date"], keep="last")
                if not sub.empty:
                    return pd.Series(sub["value"].values, index=pd.to_datetime(sub["date"]), dtype=float).sort_index()
            return pd.Series(dtype=float)

        rev = _s("OperatingRevenue", "Revenue", "NetRevenue", "TotalRevenue", "SalesOfGoods")
        gross = _s("GrossProfit", "GrossProfitLoss")
        op_income = _s(
            "OperatingIncome", "OperatingProfit",
            "IncomeFromOperations", "ProfitFromOperations",
            "OperatingProfitLoss",
        )
        net = _s("IncomeAfterTaxes", "NetIncome", "ProfitLoss",
                 "ProfitLossAttributableToOwnersOfParent")

        # Derive revenue from gross+cogs if missing
        if rev.empty and not gross.empty:
            cogs = _s("OperatingCosts", "CostOfGoodsSold", "CostOfRevenue")
            if not cogs.empty:
                common = gross.index.intersection(cogs.index)
                if len(common):
                    rev = (gross[common] + cogs[common]).sort_index()

        quarter_dates = sorted(
            dt for dt in inc["date"].unique()
            if not pd.isna(dt) and pd.Timestamp(dt).month in {3, 6, 9, 12}
        )

        rows = []
        for dt in quarter_dates:
            ts = pd.Timestamp(dt)
            r_val = rev.get(ts)
            g_val = gross.get(ts)
            o_val = op_income.get(ts)
            n_val = net.get(ts)

            def pct(num, den):
                if num is None or den is None:
                    return None
                if pd.isna(num) or pd.isna(den) or float(den) == 0:
                    return None
                return round(float(num) / float(den) * 100, 2)

            q_num = (ts.month - 1) // 3 + 1
            rows.append({
                "quarter": str(ts.date()),
                "quarter_label": f"{ts.year} Q{q_num}",
                "gross_margin": pct(g_val, r_val),
                "operating_margin": pct(o_val, r_val),
                "net_margin": pct(n_val, r_val),
            })

        return pd.DataFrame(rows) if rows else pd.DataFrame(
            columns=["quarter", "quarter_label", "gross_margin", "operating_margin", "net_margin"]
        )

    # ------------------------------------------------------------------
    # === NEW: EPS Trend + YoY Growth ===
    # ------------------------------------------------------------------

    def fetch_eps_trend(
        self,
        stock_id: str,
        start_date: date,
        end_date: date,
        timeout: float = 30.0,
    ) -> pd.DataFrame:
        """Quarterly EPS with YoY growth rate (same-quarter comparison).

        Returns DataFrame:
          quarter, quarter_label, eps (float), eps_yoy (% or None)
        """
        fin = self.fetch_financial_statements(stock_id, start_date, end_date, timeout)
        if fin.empty:
            return pd.DataFrame(columns=["quarter", "quarter_label", "eps", "eps_yoy"])

        eps_df = fin[fin["type"] == "EPS"].copy()
        if eps_df.empty:
            return pd.DataFrame(columns=["quarter", "quarter_label", "eps", "eps_yoy"])

        eps_df["date"] = pd.to_datetime(eps_df["date"])
        eps_df = eps_df.sort_values("date").drop_duplicates("date", keep="last")
        eps_df["year"] = eps_df["date"].dt.year
        eps_df["qnum"] = ((eps_df["date"].dt.month - 1) // 3 + 1)

        rows = []
        for _, r in eps_df.iterrows():
            ts = r["date"]
            eps_val = float(r["value"]) if not pd.isna(r["value"]) else None
            # YoY: same quarter last year
            same_q_last_year = eps_df[
                (eps_df["year"] == r["year"] - 1) & (eps_df["qnum"] == r["qnum"])
            ]
            eps_yoy = None
            if not same_q_last_year.empty and eps_val is not None:
                prev_eps = float(same_q_last_year.iloc[0]["value"])
                if not pd.isna(prev_eps) and abs(prev_eps) > 1e-9:
                    eps_yoy = round((eps_val - prev_eps) / abs(prev_eps) * 100, 1)
            q_num = int(r["qnum"])
            rows.append({
                "quarter": str(ts.date()),
                "quarter_label": f"{r['year']} Q{q_num}",
                "eps": eps_val,
                "eps_yoy": eps_yoy,
            })

        return pd.DataFrame(rows)

    # ------------------------------------------------------------------
    # === NEW: Liquidity Ratios + BVPS ===
    # ------------------------------------------------------------------

    def fetch_liquidity_ratios(
        self,
        stock_id: str,
        start_date: date,
        end_date: date,
        timeout: float = 30.0,
    ) -> pd.DataFrame:
        """Quarterly current ratio, quick ratio, and BVPS.

        Returns DataFrame:
          quarter, quarter_label,
          current_ratio, quick_ratio, bvps (all float or None)
        """
        bs = self.fetch_balance_sheet(stock_id, start_date, end_date, timeout)
        if bs.empty:
            return pd.DataFrame(columns=["quarter", "quarter_label", "current_ratio", "quick_ratio", "bvps"])

        def _s(*type_names: str) -> pd.Series:
            for t in type_names:
                sub = bs[bs["type"] == t].drop_duplicates(subset=["date"], keep="last")
                if not sub.empty:
                    return pd.Series(sub["value"].values, index=pd.to_datetime(sub["date"]), dtype=float).sort_index()
            return pd.Series(dtype=float)

        cur_assets = _s("CurrentAssets", "TotalCurrentAssets")
        cur_liab = _s("CurrentLiabilities", "TotalCurrentLiabilities")
        inv = _s("Inventories", "Inventory", "InventoriesNet")
        equity = _s("EquityAttributableToOwnersOfParent", "Equity", "StockholdersEquity")
        capital = _s("IssuedCapital", "OrdinaryShares", "CommonStock", "CapitalStock")

        quarter_dates = sorted(
            dt for dt in bs["date"].unique()
            if not pd.isna(dt) and pd.Timestamp(dt).month in {3, 6, 9, 12}
        )

        rows = []
        for dt in quarter_dates:
            ts = pd.Timestamp(dt)
            ca = cur_assets.get(ts)
            cl = cur_liab.get(ts)
            iv = inv.get(ts)
            eq = equity.get(ts)
            cap = capital.get(ts)

            def safe_ratio(n, d):
                if n is None or d is None or pd.isna(n) or pd.isna(d) or float(d) == 0:
                    return None
                return round(float(n) / float(d), 2)

            current_ratio = safe_ratio(ca, cl)
            quick_assets = (float(ca) - float(iv if iv is not None and not pd.isna(iv) else 0)) if ca is not None and not pd.isna(ca) else None
            quick_ratio = safe_ratio(quick_assets, cl)

            bvps = None
            if eq is not None and cap is not None and not pd.isna(eq) and not pd.isna(cap) and float(cap) > 0:
                # eq and cap are both in NT$ thousands from FinMind
                # total_shares = cap(NT$k) * 1000 / 10 = cap * 100  (shares)
                # bvps = eq(NT$k) * 1000 / total_shares = eq * 1000 / (cap * 100) = eq * 10 / cap
                total_shares = float(cap) * 100
                if total_shares > 0:
                    bvps = round(float(eq) * 1000 / total_shares, 2)

            q_num = (ts.month - 1) // 3 + 1
            rows.append({
                "quarter": str(ts.date()),
                "quarter_label": f"{ts.year} Q{q_num}",
                "current_ratio": current_ratio,
                "quick_ratio": quick_ratio,
                "bvps": bvps,
            })

        return pd.DataFrame(rows) if rows else pd.DataFrame(
            columns=["quarter", "quarter_label", "current_ratio", "quick_ratio", "bvps"]
        )

    # ------------------------------------------------------------------


