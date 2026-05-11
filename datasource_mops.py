from __future__ import annotations

import re
import random
import time
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import requests


MOPS_FORM_URL = "https://mops.twse.com.tw/mops/web/t21sc03"
MOPS_AJAX_URL = "https://mops.twse.com.tw/mops/web/ajax_t21sc03"


class MopsBlockedError(RuntimeError):
    pass


class MopsNoDataError(RuntimeError):
    pass


def ad_year_to_roc(ad_year: int) -> int:
    return ad_year - 1911


def parse_amount(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in {"", "--", "-"}:
        return None
    text = text.replace(",", "")
    text = re.sub(r"\s+", "", text)
    # sometimes values like (123)
    if re.fullmatch(r"\(\d+(?:\.\d+)?\)", text):
        text = "-" + text.strip("()")
    try:
        return float(text)
    except ValueError:
        return None


def is_blocked_html(html: str) -> bool:
    needles = [
        "FOR SECURITY REASONS",
        "THIS PAGE CAN NOT BE ACCESSED",
        "異常",
        "請勿",
        "驗證碼",
    ]
    upper = html.upper()
    return any(n.upper() in upper for n in needles)


def _find_column(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    def norm_col(c: object) -> str:
        if isinstance(c, tuple):
            return " ".join(str(x).strip() for x in c if str(x).strip()).strip()
        return str(c).strip()

    cols = [norm_col(c) for c in df.columns]
    for cand in candidates:
        for c in cols:
            if cand == c:
                return c
    # fuzzy
    for cand in candidates:
        for c in cols:
            if cand in c:
                return c
    return None


@dataclass
class MopsClient:
    session: requests.Session
    throttle_seconds: float = 0.6
    _primed: bool = False

    @classmethod
    def create(cls, throttle_seconds: float = 0.6) -> "MopsClient":
        s = requests.Session()
        s.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
                "Origin": "https://mops.twse.com.tw",
            }
        )
        return cls(session=s, throttle_seconds=throttle_seconds)

    def _prime_cookies(self, timeout: float = 20.0) -> None:
        if self._primed:
            return
        r = self.session.get(MOPS_FORM_URL, headers={"Referer": "https://mops.twse.com.tw/mops/web/index"}, timeout=timeout)
        r.raise_for_status()
        self._primed = True

    def _sleep_throttle(self) -> None:
        # add a small jitter to look less like a bot; keep it simple
        jitter = random.uniform(0, min(0.25, self.throttle_seconds / 3))
        time.sleep(self.throttle_seconds + jitter)

    def fetch_month_table(
        self,
        typek: str,
        roc_year: int,
        month: int,
        co_id: Optional[str] = None,
        timeout: float = 30.0,
    ) -> pd.DataFrame:
        self._prime_cookies(timeout=timeout)
        self._sleep_throttle()

        payload = {
            "encodeURIComponent": "1",
            "step": "1",
            "firstin": "1",
            "off": "1",
            "TYPEK": typek,
            "year": str(roc_year),
            "month": str(int(month)),
        }
        if co_id:
            payload["co_id"] = str(co_id).strip()
        headers = {"Referer": MOPS_FORM_URL}

        r = self.session.post(MOPS_AJAX_URL, data=payload, headers=headers, timeout=timeout)
        r.raise_for_status()
        html = r.text

        if is_blocked_html(html):
            raise MopsBlockedError("MOPS blocked the request (security/WAF).")

        # MOPS returns an HTML document with a table
        try:
            tables = pd.read_html(html)
        except ValueError:
            raise MopsNoDataError("No HTML tables found in MOPS response.")

        if not tables:
            raise MopsNoDataError("Empty table list from MOPS response.")

        # heuristic: first big table is usually the data table
        df = max(tables, key=lambda t: t.shape[0] * t.shape[1]).copy()

        # flatten multi-level columns
        df.columns = [
            " ".join(str(x).strip() for x in c if str(x).strip()).strip() if isinstance(c, tuple) else str(c).strip()
            for c in df.columns
        ]
        return df

    def fetch_single_stock_month_revenue(
        self,
        stock_id: str,
        typek: str,
        ad_year: int,
        month: int,
        timeout: float = 30.0,
    ) -> float | None:
        roc_year = ad_year_to_roc(ad_year)
        df = self.fetch_month_table(typek=typek, roc_year=roc_year, month=month, co_id=stock_id, timeout=timeout)

        id_col = _find_column(df, ["公司代號", "公司代碼", "代號"])
        revenue_col = _find_column(df, ["當月營收", "本月營收", "營業收入-當月營收", "營業收入(當月)"])

        if id_col is None:
            # sometimes the first column is the company id
            id_col = str(df.columns[0])
        if revenue_col is None:
            # try find any column containing '當月' and '營收'
            for c in df.columns:
                s = str(c)
                if ("當月" in s or "本月" in s) and "營收" in s:
                    revenue_col = str(c)
                    break

        stock_id_norm = str(stock_id).strip()
        work = df.copy()
        work[id_col] = work[id_col].astype(str).str.strip()

        row = work.loc[work[id_col] == stock_id_norm]
        if row.empty:
            # some tables have id with leading zeros stripped; try int compare
            try:
                sid_int = int(stock_id_norm)
                row = work.loc[pd.to_numeric(work[id_col], errors="coerce") == sid_int]
            except Exception:
                row = pd.DataFrame()

        if row.empty or revenue_col is None:
            return None

        value = row.iloc[0][revenue_col]
        return parse_amount(value)


# ---------------------------------------------------------------------------
# Director / Supervisor shareholding from MOPS (公開資訊觀測站 t51sb01)
# ---------------------------------------------------------------------------

MOPS_SHAREHOLDING_URL = "https://mops.twse.com.tw/mops/web/ajax_t51sb01"
MOPS_SHAREHOLDING_FORM_URL = "https://mops.twse.com.tw/mops/web/t51sb01"

# TWSE open-data JSON endpoint for director/supervisor shareholding (上市)
TWSE_DIRECTOR_URL = "https://www.twse.com.tw/rwd/zh/fund/TWT51U01"
# TPEx open-data JSON endpoint (上櫃)
TPEX_DIRECTOR_URL = "https://www.tpex.org.tw/web/stock/fund/director_holder/director_holder_result.php"


class MopsShareholdingError(RuntimeError):
    pass


def _twse_director_shareholding(stock_id: str, timeout: float = 30.0) -> pd.DataFrame:
    """Fetch director/supervisor shareholding via TWSE open JSON API (上市 stocks).

    Endpoint: /rwd/zh/fund/TWT51U01
    Returns DataFrame with {date, person_name, person_type, shares, ratio} or empty on failure.
    """
    import logging

    try:
        resp = requests.get(
            TWSE_DIRECTOR_URL,
            params={"date": "", "stockNo": str(stock_id).strip(), "response": "json"},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.twse.com.tw/",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        logging.warning("TWSE director shareholding: request error for %s: %s", stock_id, exc)
        return pd.DataFrame()

    # TWSE returns {"stat": "OK", "date": "...", "title": "...", "fields": [...], "data": [[...],..]}
    if payload.get("stat") not in {"OK", "ok"} or not payload.get("data"):
        return pd.DataFrame()

    fields = payload.get("fields", [])
    rows_raw = payload["data"]

    # Extract report date from title or outer "date" field, e.g. "1140101" → 2025-01-01
    report_date: str | None = None
    date_str = payload.get("date", "")
    m = re.match(r"(\d{3})(\d{2})(\d{2})", str(date_str))
    if m:
        roc_y, mo, dy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        report_date = f"{roc_y + 1911}-{mo:02d}-{dy:02d}"

    # Identify column positions by keyword matching
    def _col_idx(keywords: list[str]) -> int | None:
        for i, f in enumerate(fields):
            if any(kw in str(f) for kw in keywords):
                return i
        return None

    name_idx = _col_idx(["姓名", "機構名稱", "代理人姓名"])
    type_idx = _col_idx(["職稱", "身分", "職務"])
    shares_idx = _col_idx(["持有股數", "持股數", "持股張數", "持股"])
    ratio_idx = _col_idx(["持股比例", "%", "比例", "持股率"])

    if name_idx is None and len(fields) > 0:
        name_idx = 0  # fallback: first column

    rows = []
    for row_cells in rows_raw:
        if not row_cells:
            continue
        name_val = str(row_cells[name_idx]).strip() if name_idx is not None and name_idx < len(row_cells) else ""
        if not name_val or name_val in {"nan", "-", ""}:
            continue

        type_val: str | None = None
        if type_idx is not None and type_idx < len(row_cells):
            tv = str(row_cells[type_idx]).strip()
            type_val = tv if tv not in {"nan", "", "-"} else None

        shares_val: int | None = None
        if shares_idx is not None and shares_idx < len(row_cells):
            try:
                cleaned = str(row_cells[shares_idx]).replace(",", "").strip()
                shares_val = int(float(cleaned)) if cleaned not in {"", "nan", "-", "--"} else None
            except (ValueError, TypeError):
                pass

        ratio_val: float | None = None
        if ratio_idx is not None and ratio_idx < len(row_cells):
            try:
                cleaned = str(row_cells[ratio_idx]).replace(",", "").replace("%", "").strip()
                ratio_val = float(cleaned) if cleaned not in {"", "nan", "-", "--"} else None
            except (ValueError, TypeError):
                pass

        rows.append({
            "date": pd.Timestamp(report_date) if report_date else pd.NaT,
            "person_name": name_val,
            "person_type": type_val,
            "shares": shares_val,
            "ratio": ratio_val,
        })

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _tpex_director_shareholding(stock_id: str, timeout: float = 30.0) -> pd.DataFrame:
    """Fetch director/supervisor shareholding via TPEx JSON API (上櫃 stocks).

    Returns DataFrame with {date, person_name, person_type, shares, ratio} or empty on failure.
    """
    import logging
    import datetime

    today = datetime.date.today()
    roc_date = f"{today.year - 1911}/{today.month:02d}/{today.day:02d}"

    try:
        resp = requests.get(
            TPEX_DIRECTOR_URL,
            params={
                "l": "zh-tw", "o": "json",
                "d": roc_date,
                "s": str(stock_id).strip(),
            },
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.tpex.org.tw/",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        logging.warning("TPEx director shareholding: request error for %s: %s", stock_id, exc)
        return pd.DataFrame()

    # TPEx returns {"reportDate": "...", "aaData": [[...],...]}
    aa_data = payload.get("aaData") or payload.get("data") or []
    if not aa_data:
        return pd.DataFrame()

    report_date: str | None = None
    rdate = str(payload.get("reportDate", "")).strip()
    m = re.match(r"(\d{3})/(\d{2})/(\d{2})", rdate)
    if m:
        report_date = f"{int(m.group(1)) + 1911}-{m.group(2)}-{m.group(3)}"

    rows = []
    for row_cells in aa_data:
        if len(row_cells) < 2:
            continue
        # TPEx format: [姓名, 職稱, 持股數, ...]
        name_val = str(row_cells[0]).strip()
        if not name_val or name_val in {"nan", "-"}:
            continue
        type_val = str(row_cells[1]).strip() if len(row_cells) > 1 else None
        if type_val in {"nan", "", "-"}:
            type_val = None

        shares_val: int | None = None
        if len(row_cells) > 2:
            try:
                cleaned = str(row_cells[2]).replace(",", "").strip()
                shares_val = int(float(cleaned)) if cleaned not in {"", "nan", "-", "--"} else None
            except (ValueError, TypeError):
                pass

        ratio_val: float | None = None
        if len(row_cells) > 3:
            try:
                cleaned = str(row_cells[3]).replace(",", "").replace("%", "").strip()
                ratio_val = float(cleaned) if cleaned not in {"", "nan", "-", "--"} else None
            except (ValueError, TypeError):
                pass

        rows.append({
            "date": pd.Timestamp(report_date) if report_date else pd.NaT,
            "person_name": name_val,
            "person_type": type_val,
            "shares": shares_val,
            "ratio": ratio_val,
        })

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def fetch_director_shareholding_mops(
    stock_id: str,
    timeout: float = 30.0,
) -> pd.DataFrame:
    """Fetch director/supervisor shareholding using a multi-strategy approach.

    Strategy order (stops at first non-empty result):
      1. TWSE open JSON API (上市 stocks — most reliable, no WAF)
      2. TPEx open JSON API (上櫃 stocks — most reliable, no WAF)
      3. MOPS ajax_t51sb01 form POST with TYPEK="sii" (上市 fallback)
      4. MOPS ajax_t51sb01 form POST with TYPEK="otc" (上櫃 fallback)

    Returns DataFrame with columns: date, person_name, person_type, shares, ratio.
    Returns empty DataFrame when all strategies fail.
    """
    import logging

    # ── Strategy 1: TWSE JSON (上市) ─────────────────────────────────────
    df = _twse_director_shareholding(stock_id, timeout)
    if not df.empty:
        return df

    # ── Strategy 2: TPEx JSON (上櫃) ─────────────────────────────────────
    df = _tpex_director_shareholding(stock_id, timeout)
    if not df.empty:
        return df

    # ── Strategy 3 & 4: MOPS form POST (sii then otc) ────────────────────
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": MOPS_SHAREHOLDING_FORM_URL,
        "Origin": "https://mops.twse.com.tw",
    })

    # Prime cookies (best-effort)
    try:
        session.get(MOPS_SHAREHOLDING_FORM_URL, timeout=timeout)
    except Exception as exc:
        logging.warning("MOPS shareholding: failed to prime cookies: %s", exc)

    for typek in ("sii", "otc", "pub"):
        payload = {
            "encodeURIComponent": "1",
            "step": "1",
            "firstin": "1",
            "off": "1",
            "TYPEK": typek,
            "co_id": str(stock_id).strip(),
            "queryName": "co_id",
            "inpuType": "co_id",
            "isQuery": "Y",
        }

        try:
            r = session.post(MOPS_SHAREHOLDING_URL, data=payload, timeout=timeout)
            r.raise_for_status()
        except Exception as exc:
            logging.warning("MOPS shareholding [%s]: HTTP error: %s", typek, exc)
            continue

        html = r.text
        if is_blocked_html(html):
            logging.warning("MOPS shareholding [%s]: blocked by WAF", typek)
            continue

        df = _parse_mops_shareholding_html(html)
        if not df.empty:
            return df

    return pd.DataFrame()


def _parse_mops_shareholding_html(html: str) -> pd.DataFrame:
    """Parse an MOPS ajax_t51sb01 HTML response into a director shareholding DataFrame."""
    import logging

    try:
        tables = pd.read_html(html, flavor="lxml")
    except Exception:
        try:
            tables = pd.read_html(html)
        except Exception as exc:
            logging.warning("MOPS shareholding: parse error: %s", exc)
            return pd.DataFrame()

    if not tables:
        return pd.DataFrame()

    # Find the most relevant table — look for one with named columns about 姓名 / 持股
    target_df: pd.DataFrame | None = None
    for tbl in tables:
        tbl.columns = pd.Index([
            " ".join(str(x).strip() for x in c if str(x).strip()).strip() if isinstance(c, tuple) else str(c).strip()
            for c in tbl.columns
        ])
        col_strs = " ".join(str(c) for c in tbl.columns)
        if ("姓名" in col_strs or "機構名稱" in col_strs) and ("持股" in col_strs or "股數" in col_strs):
            target_df = tbl
            break

    if target_df is None:
        if not tables:
            return pd.DataFrame()
        target_df = max(tables, key=lambda t: len(t))
        target_df.columns = pd.Index([
            " ".join(str(x).strip() for x in c if str(x).strip()).strip() if isinstance(c, tuple) else str(c).strip()
            for c in target_df.columns
        ])

    def _find(candidates: list[str]) -> str | None:
        col_strs_map = {str(c): c for c in target_df.columns}
        for cand in candidates:
            for cs, orig in col_strs_map.items():
                if cand in cs:
                    return str(orig)
        return None

    name_col = _find(["姓名", "機構名稱", "名稱"])
    type_col = _find(["職稱", "身分", "職務", "類別"])
    shares_col = _find(["持有股數", "持股數", "持股張數", "持股"])
    ratio_col = _find(["持股比例", "持股%", "比例", "持股率"])

    if name_col is None:
        logging.warning("MOPS shareholding: cannot identify name column in %s", list(target_df.columns))
        return pd.DataFrame()

    # Extract report date from HTML (ROC date pattern)
    report_date: str | None = None
    date_match = re.search(r"(\d{3,4})\s*年\s*(\d{1,2})\s*月", html)
    if date_match:
        roc_y = int(date_match.group(1))
        mo = int(date_match.group(2))
        ad_y = roc_y + 1911 if roc_y < 1000 else roc_y
        report_date = f"{ad_y}-{mo:02d}-01"

    rows = []
    for _, row in target_df.iterrows():
        name_val = str(row.get(name_col, "")).strip() if name_col else ""
        if not name_val or name_val in {"nan", "姓名", "機構名稱", "名稱"}:
            continue

        type_val = str(row.get(type_col, "")).strip() if type_col else ""
        if type_val in {"nan", ""}:
            type_val = None

        shares_raw = row.get(shares_col) if shares_col else None
        ratio_raw = row.get(ratio_col) if ratio_col else None

        shares_val: int | None = None
        if shares_raw is not None:
            try:
                cleaned = str(shares_raw).replace(",", "").strip()
                shares_val = int(float(cleaned)) if cleaned not in {"", "nan", "-", "--"} else None
            except (ValueError, TypeError):
                shares_val = None

        ratio_val: float | None = None
        if ratio_raw is not None:
            try:
                cleaned = str(ratio_raw).replace(",", "").replace("%", "").strip()
                ratio_val = float(cleaned) if cleaned not in {"", "nan", "-", "--"} else None
            except (ValueError, TypeError):
                ratio_val = None

        rows.append({
            "date": pd.Timestamp(report_date) if report_date else pd.NaT,
            "person_name": name_val,
            "person_type": type_val,
            "shares": shares_val,
            "ratio": ratio_val,
        })

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)
