"""公開資訊觀測站重大訊息：偵測更換會計師、財報延遲申報。

用 MOPS 的 JSON API，`month: "all"` 一次回傳整年，查近 12 個月最多兩次請求。
抓不到時拋 MopsNewsError 而不是回空 list：「沒查到」不等於「沒有雷」。
"""
from __future__ import annotations

import random
import re
import time
from datetime import date, timedelta
from typing import Any, Optional

import requests

MOPS_NEWS_API = "https://mops.twse.com.tw/mops/api/t05st01"

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]
_TIMEOUT_SECONDS = 10.0
_MAX_RETRIES = 3
_THROTTLE_SECONDS = 0.5
_CACHE_TTL_SECONDS = 7 * 24 * 3600.0
_LOOKBACK_DAYS = 365

_CODE_OK = 200
_CODE_NO_DATA = 406  # 查無相符資料: the company simply has no filings that period.

_AUDITOR_CHANGE = re.compile(r"(更換|變更|異動|解任|委任).{0,12}會計師|會計師.{0,8}(更換|異動)")
_FILING_DELAY = re.compile(r"延後公告.{0,8}財務報告|無法於期限內|財務報告.{0,12}(延後|延遲|遲延)|(延遲|遲延).{0,8}(申報|公告).{0,8}財務報告")

# (stock_id, roc_year) → (saved_at, rows)
_year_cache: dict[tuple[str, int], tuple[float, list[dict[str, str]]]] = {}


class MopsNewsError(RuntimeError):
    """MOPS could not be read, so the result is unknown rather than clean."""


def _roc_date_to_iso(value: Any) -> Optional[str]:
    match = re.fullmatch(r"(\d{2,3})/(\d{2})/(\d{2})", str(value).strip())
    if not match:
        return None
    return f"{int(match.group(1)) + 1911}-{match.group(2)}-{match.group(3)}"


def _post_year(stock_id: str, roc_year: int) -> dict[str, Any]:
    body = {"companyId": stock_id, "year": str(roc_year), "month": "all", "firstDay": "", "lastDay": ""}
    last_error = "no attempt made"
    for attempt in range(_MAX_RETRIES):
        if attempt:
            time.sleep((2 ** (attempt - 1)) + random.uniform(0.0, 0.5))
        try:
            response = requests.post(
                MOPS_NEWS_API, json=body, timeout=_TIMEOUT_SECONDS,
                headers={"User-Agent": random.choice(_USER_AGENTS), "Referer": "https://mops.twse.com.tw/"},
            )
            response.raise_for_status()
            # A block page is HTML, so it fails here rather than looking like no data.
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = type(exc).__name__
    raise MopsNewsError(f"MOPS request failed: {last_error}")


def _fetch_year(stock_id: str, roc_year: int) -> list[dict[str, str]]:
    cached = _year_cache.get((stock_id, roc_year))
    if cached and time.time() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    payload = _post_year(stock_id, roc_year)
    code = payload.get("code")
    if code == _CODE_NO_DATA:
        rows: list[dict[str, str]] = []
    elif code == _CODE_OK:
        rows = []
        for row in (payload.get("result") or {}).get("data") or []:
            day = _roc_date_to_iso(row[2]) if len(row) > 4 else None
            if day:
                rows.append({"date": day, "title": " ".join(str(row[4]).split())})
    else:
        raise MopsNewsError(f"MOPS error: {payload.get('message')}")

    _year_cache[(stock_id, roc_year)] = (time.time(), rows)
    return rows


def fetch_major_news(stock_id: str, today: Optional[date] = None) -> list[dict[str, str]]:
    """Material announcements from the last 12 months, newest first."""
    today = today or date.today()
    since = (today - timedelta(days=_LOOKBACK_DAYS)).isoformat()

    rows: list[dict[str, str]] = []
    for index, roc_year in enumerate(range(today.year - 1911 - 1, today.year - 1911 + 1)):
        if index and (stock_id, roc_year) not in _year_cache:
            time.sleep(_THROTTLE_SECONDS + random.uniform(0.0, 0.3))
        rows.extend(_fetch_year(stock_id, roc_year))
    recent = [row for row in rows if row["date"] >= since]
    return sorted(recent, key=lambda row: row["date"], reverse=True)


def detect_auditor_change(news: list[dict[str, str]]) -> list[dict[str, str]]:
    return [item for item in news if _AUDITOR_CHANGE.search(item["title"])]


def detect_filing_delay(news: list[dict[str, str]]) -> list[dict[str, str]]:
    return [item for item in news if _FILING_DELAY.search(item["title"])]
