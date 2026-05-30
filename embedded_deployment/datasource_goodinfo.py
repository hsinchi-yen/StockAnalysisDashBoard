"""datasource_goodinfo.py – Goodinfo.tw scraper for quarterly financials.

Fetches quarterly income statement (cumulative YTD) and balance sheet data
needed to compute TTM ROE / ROA.

Data units:  百萬元 (NT$ millions).  Since ROE/ROA are ratios, units cancel
out and no explicit conversion is required — as long as IS and BS pages share
the same unit, which Goodinfo guarantees.
"""
from __future__ import annotations

import io
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd
import requests

GOODINFO_FIN_URL = "https://goodinfo.tw/tw/StockFinDetail.asp"
GOODINFO_SHAREHOLD_URL = "https://goodinfo.tw/tw/StockDirectorSharehold.asp"
logger = logging.getLogger(__name__)

_UA_POOL: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
]

_GOODINFO_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL_30D = 30 * 24 * 3600


def _gc_get(key: str) -> Any:
    entry = _GOODINFO_CACHE.get(key)
    if entry is None:
        return None
    ts, val = entry
    if time.time() - ts > _CACHE_TTL_30D:
        del _GOODINFO_CACHE[key]
        return None
    return val


def _gc_set(key: str, val: Any) -> None:
    _GOODINFO_CACHE[key] = (time.time(), val)


class GoodinfoError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _roc_qtr_to_ts(text: str) -> Optional[pd.Timestamp]:
    """Convert a quarter label to a Gregorian quarter-end Timestamp.

    Recognised formats:
      '2025Q4'       → 2025-12-31  (Gregorian, exact — as used by Goodinfo)
      '114Q4'        → 2025-12-31  (ROC year)
      '114/Q4'       → 2025-12-31
      '114年Q4'      → 2025-12-31
      '114年 第4季'  → 2025-12-31
      '1144'         → 2025-12-31  (4-digit YYYQ compact form)
    """
    s = str(text).strip()

    # Gregorian format (strict full-match so "2025Q4.1" is rejected):
    m = re.fullmatch(r"(20[12]\d)[Qq]([1-4])", s)
    if m:
        try:
            ad_year = int(m.group(1))
            quarter = int(m.group(2))
            month_end = quarter * 3
            return pd.Timestamp(ad_year, month_end, 1) + pd.offsets.MonthEnd(0)
        except Exception:
            return None

    # ROC format: "114Q4", "114年 Q4", "114年 第4季", "1144"
    m = re.search(r"(1[01]\d)[^\d]*[Qq]([1-4])", s)
    if not m:
        m = re.search(r"(1[01]\d)\D+([1-4])\D*季", s)
    if not m:
        m = re.fullmatch(r"(1[01]\d)([1-4])", s)
    if not m:
        return None

    try:
        roc_year = int(m.group(1))
        quarter  = int(m.group(2))
        if not (1 <= quarter <= 4):
            return None
        ad_year   = roc_year + 1911
        month_end = quarter * 3
        return pd.Timestamp(ad_year, month_end, 1) + pd.offsets.MonthEnd(0)
    except Exception:
        return None


def _parse_val(s: object) -> Optional[float]:
    """Parse a financial cell value such as '272,122' or '--'."""
    if s is None:
        return None
    text = str(s).strip()
    if text in {"", "--", "-", "N/A", "nan", "None", "NaN"}:
        return None
    text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _flatten_col(c: object) -> str:
    """Flatten a potentially tuple/MultiIndex column to a plain string."""
    if isinstance(c, tuple):
        parts = [str(x).strip() for x in c if str(x).strip() not in {"", "nan"}]
        return " ".join(parts).strip()
    return str(c).strip()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

@dataclass
class GoodinfoClient:
    throttle_seconds: float = 1.5
    _session: requests.Session = field(
        default_factory=requests.Session, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        self._session.headers.update(
            {
                "User-Agent": random.choice(_UA_POOL),
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
                "Referer": "https://goodinfo.tw/",
            }
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_client_key(static_part: str) -> tuple[str, float]:
        """Simulate Goodinfo's JavaScript CLIENT_KEY cookie computation.

        Goodinfo sets:
          CLIENT_KEY = '2.5|{c1}|{c2}|' + GetTimezoneOffset() + '|' + ExcelDate + '|' + ExcelDate
        where their custom GetTimezoneOffset() = JS_getTimezoneOffset - 25569*1440
        and ExcelDate = Date.now()/86400000 - GetTimezoneOffset()/1440
        (effectively: Excel serial number of the current local datetime).
        """
        import datetime as _dt
        now_utc = _dt.datetime.now(_dt.timezone.utc)
        unix_days = now_utc.timestamp() / 86400
        # Local timezone offset east of UTC in minutes
        local_offset_min = _dt.datetime.now(_dt.timezone.utc).astimezone().utcoffset().total_seconds() / 60
        # Goodinfo's GetTimezoneOffset(): JS getTimezoneOffset() is negative east, minus 25569*1440
        js_gto = -local_offset_min  # JS convention: minutes WEST of UTC
        goodinfo_gto = js_gto - 25569 * 1440
        # Excel date (days since Dec 30 1899, local time)
        excel_days = unix_days - goodinfo_gto / 1440
        cookie_val = f"{static_part}|{goodinfo_gto}|{excel_days}|{excel_days}"
        return cookie_val, excel_days

    def _fetch_html(self, stock_id: str, rpt_cat: str, timeout: float = 30.0) -> str:
        max_retries = 3
        delay = 1.0
        params = {"RPT_CAT": rpt_cat, "STOCK_ID": str(stock_id).strip()}
        last_exc: Exception = GoodinfoError("no attempt made")
        for attempt in range(max_retries):
            self._session.headers["User-Agent"] = random.choice(_UA_POOL)
            jitter = random.uniform(0.0, 0.5)
            time.sleep(self.throttle_seconds + jitter)
            try:
                r = self._session.get(GOODINFO_FIN_URL, params=params, timeout=timeout)
            except Exception as exc:
                last_exc = exc
                time.sleep(delay + random.uniform(0.0, 0.5))
                delay *= 2
                continue
            if r.status_code in (429, 403):
                last_exc = GoodinfoError(f"HTTP {r.status_code}")
                time.sleep(delay + random.uniform(0.0, 0.5))
                delay *= 2
                continue
            r.raise_for_status()
            r.encoding = "utf-8"
            html = r.text
            break
        else:
            raise last_exc

        # Detect Goodinfo JS challenge (tiny page with setCookie + redirect)
        if len(html) < 5000 and "CLIENT_KEY" in html:
            logger.debug("Goodinfo: JS challenge detected for %s/%s — solving cookie", stock_id, rpt_cat)
            # Extract static cookie prefix (e.g., '2.5|41052...|46607...')
            m_cookie = re.search(r"setCookie\s*\(\s*'CLIENT_KEY'\s*,\s*'([^']+)'", html)
            static_part = m_cookie.group(1) if m_cookie else "2.5"
            cookie_val, excel_days = self._compute_client_key(static_part)
            self._session.cookies.set("CLIENT_KEY", cookie_val, domain="goodinfo.tw", path="/")

            # Extract redirect path from window.location.replace('...')
            m_redir = re.search(r"window\.location\.replace\('([^']+)'", html)
            if m_redir:
                redirect_rel = m_redir.group(1)
                # Build absolute URL from relative path
                from urllib.parse import urljoin
                base = f"{r.url.split('?')[0].rsplit('/', 1)[0]}/"
                redirect_url = urljoin(base, redirect_rel)
            else:
                # Fallback: re-request with our computed REINIT
                redirect_url = None

            time.sleep(0.6)  # simulate 500ms JS timeout

            if redirect_url:
                r2 = self._session.get(redirect_url, timeout=timeout)
            else:
                r2 = self._session.get(GOODINFO_FIN_URL, params=dict(params, REINIT=excel_days), timeout=timeout)
            r2.raise_for_status()
            r2.encoding = "utf-8"
            html = r2.text
            logger.debug("Goodinfo: post-challenge page size: %d chars", len(html))

        return html

    def _find_financial_table(self, html: str, min_qtrs: int = 4) -> pd.DataFrame:
        """Parse Goodinfo HTML and return the DataFrame that contains quarterly data.

        Tries multi-level header ([0, 1]) first (most common for Goodinfo), then
        single-level (header=0).  Falls back through lxml→html.parser parsers.
        """
        last_exc: Exception = GoodinfoError("No HTML tables found.")

        for header_arg in ([0, 1], 0):
            for flavor in ("lxml", None):
                try:
                    tables = pd.read_html(
                        io.StringIO(html),
                        header=header_arg,
                        thousands=",",
                        flavor=flavor,
                    )
                except Exception as exc:
                    last_exc = exc
                    continue

                best: Optional[pd.DataFrame] = None
                best_count = 0
                for tbl in tables:
                    flat = [_flatten_col(c) for c in tbl.columns]
                    count = sum(1 for c in flat if _roc_qtr_to_ts(c) is not None)
                    if count > best_count:
                        best_count = count
                        best = tbl

                if best is not None and best_count >= min_qtrs:
                    best = best.copy()
                    best.columns = [_flatten_col(c) for c in best.columns]
                    return best

        raise GoodinfoError(
            f"No financial table with ≥{min_qtrs} quarter columns found. "
            f"Last parse error: {last_exc}"
        )

    def _extract_row(
        self, tbl: pd.DataFrame, keywords: list[str]
    ) -> pd.Series:
        """Find the first row whose label contains any keyword and return it as a Series.

        Returns: pd.Series {quarter_end_Timestamp → float}
        """
        qtr_map: dict[str, pd.Timestamp] = {}
        for col in tbl.columns:
            ts = _roc_qtr_to_ts(col)
            if ts is not None:
                qtr_map[col] = ts

        if not qtr_map:
            raise GoodinfoError("Table has no recognisable quarter columns.")

        sample_labels: list[str] = []
        for _, row in tbl.iterrows():
            label = str(row.iloc[0]).strip()
            sample_labels.append(label)
            if any(kw in label for kw in keywords):
                data: dict[pd.Timestamp, float] = {}
                for col, ts in qtr_map.items():
                    v = _parse_val(row.get(col))
                    if v is not None:
                        data[ts] = v
                if data:
                    return pd.Series(data).sort_index()

        raise GoodinfoError(
            f"Row matching {keywords} not found in table. "
            f"First 30 row labels: {sample_labels[:30]}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_quarterly_income_acc(self, stock_id: str) -> pd.Series:
        """Fetch quarterly **cumulative YTD** net income (百萬元) from IS_M_QUAR_ACC.

        IMPORTANT: Values are YTD-cumulative within each fiscal year.
        Call ``_deaccumulate_income()`` (in api.py) to convert to single-quarter.
        """
        cache_key = f"goodinfo_ni_acc|{stock_id}"
        cached = _gc_get(cache_key)
        if cached is not None:
            return pd.Series(cached, dtype=float)
        html = self._fetch_html(stock_id, "IS_M_QUAR_ACC")
        tbl  = self._find_financial_table(html)
        result = self._extract_row(tbl, ["稅後淨利", "本期淨利", "稅後純益"])
        _gc_set(cache_key, {str(k): v for k, v in result.items()})
        return result

    def fetch_quarterly_balance(
        self, stock_id: str
    ) -> tuple[pd.Series, pd.Series]:
        """Fetch quarterly balance-sheet equity and total-assets (百萬元) from BS_M_QUAR.

        Both series are point-in-time snapshots (no de-accumulation needed).
        Returns: (equity_series, assets_series)
        """
        cache_key = f"goodinfo_bs_quar|{stock_id}"
        cached = _gc_get(cache_key)
        if cached is not None:
            eq = pd.Series({pd.Timestamp(k): v for k, v in cached["equity"].items()}, dtype=float)
            as_ = pd.Series({pd.Timestamp(k): v for k, v in cached["assets"].items()}, dtype=float)
            return eq, as_
        html   = self._fetch_html(stock_id, "BS_M_QUAR")
        tbl    = self._find_financial_table(html)
        # Use specific labels to avoid hitting sub-totals (e.g. "其他權益合計"
        # or "流動資產合計") before the desired total rows.
        equity = self._extract_row(
            tbl, ["歸屬於母公司業主之權益合計", "股東權益總額", "股東權益合計"]
        )
        assets = self._extract_row(tbl, ["資產總額", "資產總計"])
        _gc_set(cache_key, {
            "equity": {str(k): v for k, v in equity.items()},
            "assets": {str(k): v for k, v in assets.items()},
        })
        return equity, assets

    # ------------------------------------------------------------------
    # Shareholding history (董監事持股 / 外資持股 月度趨勢)
    # ------------------------------------------------------------------

    def _fetch_sharehold_html(self, stock_id: str, timeout: float = 30.0) -> str:
        """Fetch the Goodinfo shareholding page HTML, handling JS cookie challenge."""
        import datetime as _dt

        max_retries = 3
        delay = 1.0
        params = {"STOCK_ID": str(stock_id).strip()}
        last_exc: Exception = GoodinfoError("no attempt made")
        for attempt in range(max_retries):
            self._session.headers["User-Agent"] = random.choice(_UA_POOL)
            jitter = random.uniform(0.0, 0.5)
            time.sleep(self.throttle_seconds + jitter)
            try:
                r = self._session.get(GOODINFO_SHAREHOLD_URL, params=params, timeout=timeout)
            except Exception as exc:
                last_exc = exc
                time.sleep(delay + random.uniform(0.0, 0.5))
                delay *= 2
                continue
            if r.status_code in (429, 403):
                last_exc = GoodinfoError(f"HTTP {r.status_code}")
                time.sleep(delay + random.uniform(0.0, 0.5))
                delay *= 2
                continue
            r.raise_for_status()
            break
        else:
            raise last_exc
        r.encoding = "utf-8"
        html = r.text

        # Handle JS challenge (same pattern as financial pages)
        if len(html) < 5000 and "CLIENT_KEY" in html:
            logger.debug("Goodinfo sharehold: JS challenge for %s", stock_id)
            m_cookie = re.search(r"setCookie\s*\(\s*'CLIENT_KEY'\s*,\s*'([^']+)'", html)
            static_part = m_cookie.group(1) if m_cookie else "2.5"
            cookie_val, excel_days = self._compute_client_key(static_part)
            self._session.cookies.set("CLIENT_KEY", cookie_val, domain="goodinfo.tw", path="/")

            m_redir = re.search(r"window\.location\.replace\('([^']+)'", html)
            from urllib.parse import urljoin
            time.sleep(0.6)
            if m_redir:
                base = f"{r.url.split('?')[0].rsplit('/', 1)[0]}/"
                redirect_url = urljoin(base, m_redir.group(1))
                r2 = self._session.get(redirect_url, timeout=timeout)
            else:
                r2 = self._session.get(
                    GOODINFO_SHAREHOLD_URL,
                    params=dict(params, REINIT=excel_days),
                    timeout=timeout,
                )
            r2.raise_for_status()
            r2.encoding = "utf-8"
            html = r2.text

        return html

    @staticmethod
    def _parse_date_ym(text: str) -> Optional[pd.Timestamp]:
        """Parse Goodinfo date strings: '114/01', '114年01月', '2025/01', '2025-01'."""
        s = str(text).strip()
        # Gregorian: 2025/01 or 2025-01
        m = re.match(r"(20[12]\d)[/\-](\d{1,2})$", s)
        if m:
            try:
                return pd.Timestamp(int(m.group(1)), int(m.group(2)), 1)
            except Exception:
                return None
        # ROC: 114/01 or 114年01月
        m = re.match(r"(1[01]\d)[/年](\d{1,2})", s)
        if m:
            try:
                return pd.Timestamp(int(m.group(1)) + 1911, int(m.group(2)), 1)
            except Exception:
                return None
        return None

    def fetch_shareholding_history(
        self, stock_id: str, timeout: float = 30.0
    ) -> pd.DataFrame:
        """Scrape Goodinfo director/foreign shareholding history (monthly).

        Returns DataFrame with columns:
          date, non_indep_shares, non_indep_pledged, non_indep_ratio,
          indep_shares, indep_pledged, indep_ratio,
          total_dir_shares, total_dir_pledged, total_dir_ratio,
          foreign_shares, foreign_ratio, total_issued_shares

        All *_shares columns are in 千股 (lots of 1000 shares on Goodinfo).
        Ratio columns are in percent (%).
        Returns empty DataFrame on failure.
        """
        cache_key = f"goodinfo_sharehold|{stock_id}"
        cached_records = _gc_get(cache_key)
        if cached_records is not None:
            try:
                df = pd.DataFrame(cached_records)
                if not df.empty:
                    df["date"] = pd.to_datetime(df["date"])
                    return df
            except Exception:
                pass

        try:
            html = self._fetch_sharehold_html(stock_id=stock_id, timeout=timeout)
        except Exception as exc:
            logger.warning("Goodinfo sharehold: HTTP error for %s: %s", stock_id, exc)
            return pd.DataFrame()

        # ── Try to parse tables ───────────────────────────────────────────
        # Goodinfo shareholding page layout:
        #   Dates run across columns (left=recent, right=old) as 'YYY/MM' ROC
        #   Each row is a metric.  We identify rows by their label text.
        tables: list[pd.DataFrame] = []
        for flavor in ("lxml", None):
            try:
                tables = pd.read_html(
                    io.StringIO(html),
                    thousands=",",
                    flavor=flavor,
                )
                break
            except Exception:
                continue

        if not tables:
            logger.warning("Goodinfo sharehold: no tables found for %s", stock_id)
            return pd.DataFrame()

        # Find the best table: most date-parseable column headers
        def _count_dates(tbl: pd.DataFrame) -> int:
            return sum(1 for c in tbl.columns if self._parse_date_ym(str(c)) is not None)

        # Flatten multi-level column headers
        def _flat_cols(tbl: pd.DataFrame) -> pd.DataFrame:
            tbl = tbl.copy()
            tbl.columns = pd.Index([_flatten_col(c) for c in tbl.columns])
            return tbl

        # Try transposed orientation: dates as cols
        best_tbl: Optional[pd.DataFrame] = None
        best_count = 0
        for tbl in tables:
            tbl = _flat_cols(tbl)
            n = _count_dates(tbl)
            if n > best_count:
                best_count = n
                best_tbl = tbl

        # Try transposed (dates as rows, metrics as columns)
        transposed_candidates: list[tuple[int, pd.DataFrame]] = []
        for tbl in tables:
            tbl = _flat_cols(tbl)
            # If first column could be a date column
            if tbl.shape[0] > 0:
                n_date_rows = sum(
                    1 for v in tbl.iloc[:, 0] if self._parse_date_ym(str(v)) is not None
                )
                if n_date_rows > 5:
                    transposed_candidates.append((n_date_rows, tbl))

        result_df: pd.DataFrame = pd.DataFrame()
        if best_count < 3 and transposed_candidates:
            transposed_candidates.sort(reverse=True, key=lambda x: x[0])
            raw_tbl = transposed_candidates[0][1]
            result_df = self._parse_sharehold_row_oriented(raw_tbl)
        elif best_tbl is not None and best_count >= 3:
            result_df = self._parse_sharehold_col_oriented(best_tbl)
        else:
            logger.warning("Goodinfo sharehold: no parseable table for %s", stock_id)
            return pd.DataFrame()

        if not result_df.empty:
            try:
                _gc_set(cache_key, result_df.assign(date=result_df["date"].astype(str)).to_dict("records"))
            except Exception:
                pass
        return result_df

    # ── internal parsers ──────────────────────────────────────────────────

    # Row = metric, Column = date  (most common Goodinfo layout)
    def _parse_sharehold_col_oriented(self, tbl: pd.DataFrame) -> pd.DataFrame:
        """Parse a table where columns are dates and rows are metric labels."""
        # Build date→column mapping
        date_cols: dict[pd.Timestamp, str] = {}
        for col in tbl.columns:
            dt = self._parse_date_ym(str(col))
            if dt is not None:
                date_cols[dt] = col

        if not date_cols:
            return pd.DataFrame()

        # Identify label column (first non-date column)
        label_col = tbl.columns[0]

        # Keyword → field name mapping
        _METRIC_MAP = [
            (["非獨立董監持股張數", "非獨立董監 持股"], "non_indep_shares"),
            (["非獨立董監質押張數", "非獨立董監 質押"], "non_indep_pledged"),
            (["非獨立董監持股比率", "非獨立董監比率", "非獨立董監 比率", "非獨立持股比率"], "non_indep_ratio"),
            (["獨立董監持股張數", "獨立董監 持股"], "indep_shares"),
            (["獨立董監質押張數", "獨立董監 質押"], "indep_pledged"),
            (["獨立董監持股比率", "獨立董監比率", "獨立董監 比率", "獨立持股比率"], "indep_ratio"),
            (["全體董監持股張數", "全體董監 持股"], "total_dir_shares"),
            (["全體董監質押張數", "全體董監 質押"], "total_dir_pledged"),
            (["全體董監持股比率", "全體董監比率", "全體董監 比率", "全體持股比率", "董監持股比率"], "total_dir_ratio"),
            (["外資持股張數", "外資 持股張數"], "foreign_shares"),
            (["外資持股比率", "外資比率", "外資 比率"], "foreign_ratio"),
            (["發行張數", "總發行張數", "上市股數"], "total_issued_shares"),
        ]

        # Extract per-metric series
        extracted: dict[str, dict[pd.Timestamp, float]] = {}
        for _, row in tbl.iterrows():
            label = str(row.get(label_col, "")).strip()
            for keywords, field in _METRIC_MAP:
                if field in extracted:
                    continue
                if any(re.search(kw, label) for kw in keywords):
                    data: dict[pd.Timestamp, float] = {}
                    for dt, col in date_cols.items():
                        v = _parse_val(row.get(col))
                        if v is not None:
                            data[dt] = v
                    if data:
                        extracted[field] = data
                    break

        if not extracted:
            return pd.DataFrame()

        all_dates = sorted({dt for vals in extracted.values() for dt in vals})
        rows = []
        for dt in all_dates:
            row_dict: dict = {"date": dt}
            for field in [f for _, f in _METRIC_MAP]:
                row_dict[field] = extracted.get(field, {}).get(dt)
            rows.append(row_dict)

        return pd.DataFrame(rows)

    # Row = date, Column = metric  (transposed layout)
    def _parse_sharehold_row_oriented(self, tbl: pd.DataFrame) -> pd.DataFrame:
        """Parse a table where rows are dates and columns are metric labels."""
        _METRIC_MAP = [
            ([r"非獨立.*持股.*張"], "non_indep_shares"),
            ([r"非獨立.*質押.*張"], "non_indep_pledged"),
            ([r"非獨立.*持股.*%"], "non_indep_ratio"),
            ([r"(?<!非)獨立.*持股.*張"], "indep_shares"),
            ([r"(?<!非)獨立.*質押.*張"], "indep_pledged"),
            ([r"(?<!非)獨立.*持股.*%"], "indep_ratio"),
            ([r"全體.*持股.*張"], "total_dir_shares"),
            ([r"全體.*質押.*張"], "total_dir_pledged"),
            ([r"全體.*持股.*%"], "total_dir_ratio"),
            ([r"外資.*張數"], "foreign_shares"),
            ([r"外資.*持股.*%"], "foreign_ratio"),
            ([r"發行.*張數"], "total_issued_shares"),
        ]

        # Find the date column
        date_col = tbl.columns[0]
        dates = [self._parse_date_ym(str(v)) for v in tbl.iloc[:, 0]]

        # Map column headers to fields
        col_map: dict[str, str] = {}
        for col in tbl.columns[1:]:
            col_str = str(col).strip()
            for keywords, field in _METRIC_MAP:
                if field in col_map.values():
                    continue
                if any(re.search(kw, col_str) for kw in keywords):
                    col_map[col] = field
                    break

        if not col_map:
            return pd.DataFrame()

        rows = []
        for i, dt in enumerate(dates):
            if dt is None:
                continue
            row_src = tbl.iloc[i]
            row_dict: dict = {"date": dt}
            for col, field in col_map.items():
                row_dict[field] = _parse_val(row_src.get(col))
            rows.append(row_dict)

        return pd.DataFrame(rows)
