"""TWSE 全市場當日快照（免 token、不佔 FinMind 配額）。

每個來源一次回傳全市場，所以整包快取一小時，再依代號篩出單檔。只涵蓋上市股；
上櫃股查無資料時回傳空 DataFrame，由呼叫端退回 FinMind。

來源：STOCK_DAY_ALL 走 OpenAPI。T86 不在 OpenAPI 上，而 OpenAPI 的 MI_MARGN 沒有
資料日期，所以這兩個走 www.twse.com.tw 的 rwd JSON（同樣一次全市場）。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

import pandas as pd
import requests

STOCK_DAY_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86?response=json&selectType=ALLBUT0999"
MI_MARGN_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?response=json&selectType=ALL"

_USER_AGENT = "Mozilla/5.0 (compatible; StockAnalysisDashBoard/1.0)"
_TIMEOUT_SECONDS = 10.0
_SNAPSHOT_TTL_SECONDS = 3600.0
# A failed download is remembered briefly so an outage can't add a full timeout
# to every /latest request.
_FAILURE_TTL_SECONDS = 300.0

PRICE_COLUMNS = ["date", "open", "max", "min", "close", "spread",
                 "Trading_Volume", "Trading_money", "Trading_turnover"]
INSTITUTIONAL_COLUMNS = ["date", "name", "buy", "sell", "net"]
MARGIN_COLUMNS = ["date", "margin_balance", "short_balance", "short_margin_ratio"]

# FinMind investor name → T86 column names for (buy, sell, net).
_T86_INVESTORS = {
    "Foreign_Investor": ("外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)", "外陸資買賣超股數(不含外資自營商)"),
    "Foreign_Dealer_Self": ("外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數"),
    "Investment_Trust": ("投信買進股數", "投信賣出股數", "投信買賣超股數"),
    "Dealer_self": ("自營商買進股數(自行買賣)", "自營商賣出股數(自行買賣)", "自營商買賣超股數(自行買賣)"),
    "Dealer_Hedging": ("自營商買進股數(避險)", "自營商賣出股數(避險)", "自營商買賣超股數(避險)"),
}

# url → (expires_at, payload)
_snapshots: dict[str, tuple[float, Any]] = {}
_snapshot_lock = threading.Lock()


def _get_json(url: str, is_valid=lambda payload: True) -> Any:
    """Return a cached market snapshot, or None when it can't be fetched."""
    with _snapshot_lock:
        entry = _snapshots.get(url)
        if entry and time.time() < entry[0]:
            return entry[1]
        try:
            response = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=_TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json()
            if not is_valid(payload):
                payload = None
        except (requests.RequestException, ValueError):
            payload = None
        ttl = _SNAPSHOT_TTL_SECONDS if payload is not None else _FAILURE_TTL_SECONDS
        _snapshots[url] = (time.time() + ttl, payload)
        return payload


def _number(value: Any) -> Optional[float]:
    """Parse a TWSE numeric string; blanks and '--' mean no value."""
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _roc_to_iso(value: str) -> Optional[str]:
    """Convert a ROC date such as '1151030' to '2026-10-30'."""
    text = str(value).strip()
    if len(text) != 7 or not text.isdigit():
        return None
    return f"{int(text[:3]) + 1911}-{text[3:5]}-{text[5:7]}"


def _yyyymmdd_to_iso(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def fetch_stock_day_all(stock_id: str) -> pd.DataFrame:
    """Latest daily price, with FinMind's TaiwanStockPrice column names."""
    payload = _get_json(STOCK_DAY_ALL_URL, lambda p: isinstance(p, list))
    row = next((r for r in payload or [] if r.get("Code") == stock_id), None)
    if row is None:
        return pd.DataFrame(columns=PRICE_COLUMNS)

    day = _roc_to_iso(row.get("Date", ""))
    close = _number(row.get("ClosingPrice"))
    if day is None or close is None:  # listed, but did not trade that day
        return pd.DataFrame(columns=PRICE_COLUMNS)

    return pd.DataFrame([{
        "date": day,
        "open": _number(row.get("OpeningPrice")),
        "max": _number(row.get("HighestPrice")),
        "min": _number(row.get("LowestPrice")),
        "close": close,
        "spread": _number(row.get("Change")),
        "Trading_Volume": _number(row.get("TradeVolume")),
        "Trading_money": _number(row.get("TradeValue")),
        "Trading_turnover": _number(row.get("Transaction")),
    }], columns=PRICE_COLUMNS)


def fetch_institutional_t86(stock_id: str) -> pd.DataFrame:
    """Latest institutional buy/sell, one row per investor type."""
    payload = _get_json(T86_URL, lambda p: isinstance(p, dict) and p.get("stat") == "OK")
    empty = pd.DataFrame(columns=INSTITUTIONAL_COLUMNS)
    if not payload:
        return empty

    day = _yyyymmdd_to_iso(payload.get("date"))
    row = next((r for r in payload.get("data") or [] if str(r[0]).strip() == stock_id), None)
    if day is None or row is None:
        return empty

    fields = payload.get("fields") or []
    rows = []
    for name, columns in _T86_INVESTORS.items():
        try:
            buy, sell, net = (_number(row[fields.index(column)]) for column in columns)
        except (ValueError, IndexError):  # column renamed upstream: skip that investor
            continue
        rows.append({"date": day, "name": name, "buy": buy or 0, "sell": sell or 0, "net": net or 0})
    return pd.DataFrame(rows, columns=INSTITUTIONAL_COLUMNS)


def fetch_margin_margn(stock_id: str) -> pd.DataFrame:
    """Latest margin and short balances (張) with 券資比 in percent."""
    payload = _get_json(MI_MARGN_URL, lambda p: isinstance(p, dict) and p.get("stat") == "OK")
    empty = pd.DataFrame(columns=MARGIN_COLUMNS)
    if not payload:
        return empty

    day = _yyyymmdd_to_iso(payload.get("date"))
    table = next((t for t in payload.get("tables") or [] if (t.get("fields") or [""])[0] == "代號"), None)
    if day is None or table is None:
        return empty
    row = next((r for r in table.get("data") or [] if str(r[0]).strip() == stock_id), None)
    if row is None:
        return empty

    # 「今日餘額」appears twice: the 融資 block first, then the 融券 block.
    balances = [i for i, field in enumerate(table["fields"]) if field == "今日餘額"]
    if len(balances) != 2:
        return empty
    margin = _number(row[balances[0]])
    short = _number(row[balances[1]])
    ratio = round(short / margin * 100, 2) if margin and short is not None else None
    return pd.DataFrame(
        [{"date": day, "margin_balance": margin, "short_balance": short, "short_margin_ratio": ratio}],
        columns=MARGIN_COLUMNS,
    )
