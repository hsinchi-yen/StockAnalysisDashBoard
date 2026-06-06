from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from cache import CacheStore, build_cache_key
from datasource_finmind import FinMindClient, FinMindError
from datasource_moneydj import fetch_capital_formation_finmind, fetch_capital_formation_moneydj
from datasource_tdcc import TDCCClient, TDCCError
from series_builder import add_mas, build_continuous_month_index, compute_sloan_ratio, reindex_to_continuous_months

try:
    from datasource_mops import fetch_director_shareholding_mops
    MOPS_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover
    fetch_director_shareholding_mops = None  # type: ignore[assignment]
    MOPS_IMPORT_ERROR = exc

try:
    from datasource_goodinfo import GoodinfoClient, GoodinfoError
    GOODINFO_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - import failure depends on runtime packaging
    GoodinfoClient = None

    class GoodinfoError(RuntimeError):
        pass

    GOODINFO_IMPORT_ERROR = exc


APP_DIR = Path(__file__).parent
STATIC_DIR = Path(os.environ.get("MICROECO_STATIC_DIR", APP_DIR / "static")).expanduser().resolve()
CACHE_DIR = Path(os.environ.get("MICROECO_CACHE_DIR", APP_DIR / ".cache")).expanduser().resolve()

app = FastAPI(title="MicroEconomicDashBoard API", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

cache = CacheStore(base_dir=CACHE_DIR)


def _compute_month_range(years: int) -> tuple[pd.Timestamp, pd.Timestamp, date]:
    today = date.today()
    end = pd.Timestamp(today.year, today.month, 1)
    start = (end - pd.DateOffset(years=int(years))).normalize()
    start = pd.Timestamp(start.year, start.month, 1)
    return start, end, today


def _resolve_token(token: str | None, header_token: str | None) -> str | None:
    if token and token.strip():
        return token.strip()
    if header_token and header_token.strip():
        return header_token.strip()
    env_key = os.environ.get("FINMIND_API_KEY", "").strip()
    if env_key:
        return env_key
    return None


def _require_token(token: str | None, header_token: str | None) -> str:
    token_resolved = _resolve_token(token, header_token)
    if token_resolved:
        return token_resolved
    raise HTTPException(
        status_code=401,
        detail="FinMind API key is required. Please provide token query param or X-FinMind-Token header.",
    )


def _to_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _extract_token_usage(payload: dict[str, Any]) -> dict[str, int]:
    if not isinstance(payload, dict):
        raise FinMindError("FinMind user_info payload is not a JSON object")

    candidates: list[dict[str, Any]] = []
    nested = payload.get("data")
    if isinstance(nested, dict):
        candidates.append(nested)
    candidates.append(payload)

    for obj in candidates:
        if "user_count" in obj or "api_request_limit" in obj:
            used = max(0, _to_int(obj.get("user_count"), 0))
            limit = max(1, _to_int(obj.get("api_request_limit"), 600))
            remaining = max(0, limit - used)
            return {
                "user_count": used,
                "api_request_limit": limit,
                "remaining": remaining,
            }

    raise FinMindError(f"FinMind user_info payload missing usage fields: {payload}")


def _fetch_token_usage_from_finmind(token: str, timeout: float = 10.0) -> dict[str, int]:
    url = "https://api.web.finmindtrade.com/v2/user_info"
    attempts = [
        ("post", {"data": {"token": token}}),
        ("post", {"json": {"token": token}}),
        ("get", {"params": {"token": token}}),
    ]
    errors: list[str] = []

    with requests.Session() as session:
        for method, kwargs in attempts:
            try:
                resp = getattr(session, method)(url, timeout=timeout, **kwargs)
            except Exception as exc:
                errors.append(f"{method}: network error: {exc}")
                continue

            if resp.status_code == 405:
                errors.append(f"{method}: HTTP 405")
                continue
            if resp.status_code >= 400:
                errors.append(f"{method}: HTTP {resp.status_code}")
                continue

            try:
                payload = resp.json()
            except Exception:
                errors.append(f"{method}: non-JSON response")
                continue

            status = payload.get("status") if isinstance(payload, dict) else None
            if status not in (None, 200, "200"):
                errors.append(f"{method}: payload status={status}")
                continue

            try:
                return _extract_token_usage(payload)
            except FinMindError as exc:
                errors.append(f"{method}: {exc}")
                continue

    raise FinMindError("FinMind token usage query failed: " + " | ".join(errors[:4]))


# ---------------------------------------------------------------------------
# ROE / ROA helpers
# ---------------------------------------------------------------------------

def _nearest_value(series: pd.Series, dt: pd.Timestamp, max_days: int = 92) -> float | None:
    """Return the value at `dt`, or the nearest available value within `max_days`."""
    if dt in series.index:
        v = series[dt]
        return None if pd.isna(v) else float(v)
    diffs = (series.index - dt).days.abs() if hasattr((series.index - dt), "days") else None
    if diffs is None:
        try:
            diffs = abs((series.index - dt) / pd.Timedelta("1D"))
        except Exception:
            return None
    close = series.index[diffs <= max_days]
    if len(close) == 0:
        return None
    best = close[diffs[diffs <= max_days].argmin()]
    v = series[best]
    return None if pd.isna(v) else float(v)


def _deaccumulate_income(ni_ytd: pd.Series) -> pd.Series:
    """Convert Taiwan cumulative YTD income series to single-quarter values.

    Taiwan companies file cumulative figures each quarter:
      Q1 (Mar) = Q1 only (already single-quarter)
      Q2 (Jun) = Q1+Q2  → single Q2 = ytd_Q2 - ytd_Q1
      Q3 (Sep) = Q1+Q2+Q3     → single Q3 = ytd_Q3 - ytd_Q2
      Q4 (Dec) = full year     → single Q4 = ytd_Q4 - ytd_Q3
    Resets at each calendar-year boundary.
    """
    if ni_ytd.empty:
        return ni_ytd

    result: dict[pd.Timestamp, float] = {}
    by_year: dict[int, list[tuple[pd.Timestamp, float]]] = {}
    for dt, val in ni_ytd.items():
        if pd.isna(val):
            continue
        by_year.setdefault(dt.year, []).append((dt, float(val)))

    for entries in by_year.values():
        entries.sort(key=lambda x: x[0])
        prev = 0.0
        for dt, val in entries:
            result[dt] = val - prev
            prev = val

    return pd.Series(result).sort_index()


def _compute_roe_roa_ttm(
    ni_q: pd.Series,
    equity: pd.Series,
    assets: pd.Series,
    start_cutoff: pd.Timestamp,
) -> list[dict[str, Any]]:
    """Compute near-four-quarter (TTM) ROE / ROA — matches 財報狗 '近四季 ROE/ROA'.

    For each quarter-end date Q (from 4th available quarter onwards):
      TTM Net Income  = NI_Q + NI_Q-1 + NI_Q-2 + NI_Q-3  (single-quarter values)
      Avg Equity      = mean(equity at Q, Q-1, Q-2, Q-3)
      Avg Assets      = mean(assets at Q, Q-1, Q-2, Q-3)
      ROE = TTM NI / Avg Equity x 100
      ROA = TTM NI / Avg Assets x 100
    """
    import logging

    # only standard Taiwan quarter-end months
    quarter_dates = sorted(
        dt for dt in ni_q.index if not pd.isna(dt) and dt.month in {3, 6, 9, 12}
    )

    rows: list[dict[str, Any]] = []

    for i, dt in enumerate(quarter_dates):
        if i < 3:
            continue  # need 4 quarters for TTM
        if dt < start_cutoff:
            continue

        last4 = quarter_dates[i - 3 : i + 1]

        # Guard against data gaps > ~13 months
        if (last4[-1] - last4[0]).days > 400:
            logging.warning("ROE/ROA: skipping %s — gap in quarterly data: %s", dt.date(), last4)
            continue

        # TTM net income
        ni_vals = [ni_q.get(d) for d in last4]
        if any(v is None or pd.isna(v) for v in ni_vals):
            continue
        ttm_ni = sum(float(v) for v in ni_vals)  # type: ignore[arg-type]

        # Average equity / assets over the 4 quarter-ends
        valid_eq = [float(v) for v in (equity.get(d) for d in last4) if v is not None and not pd.isna(v)]
        valid_as = [float(v) for v in (assets.get(d) for d in last4) if v is not None and not pd.isna(v)]

        if not valid_eq or not valid_as:
            continue

        avg_eq = sum(valid_eq) / len(valid_eq)
        avg_as = sum(valid_as) / len(valid_as)

        roe = round(ttm_ni / avg_eq * 100, 2) if avg_eq != 0 else None
        roa = round(ttm_ni / avg_as * 100, 2) if avg_as != 0 else None

        q_num = (dt.month - 1) // 3 + 1
        rows.append({
            "quarter": str(dt.date()),
            "quarter_label": f"{dt.year} Q{q_num}",
            "roe": roe,
            "roa": roa,
        })

    return rows


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "ts": datetime.now(timezone.utc).isoformat(),
        "goodinfo_available": GOODINFO_IMPORT_ERROR is None,
    }


@app.get("/api/stocks/{stock_id}/latest")
def latest(
    stock_id: str,
    token: str | None = Query(default=None, description="FinMind API token (optional). Prefer header X-FinMind-Token."),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")

    token_resolved = _require_token(token, x_finmind_token)
    today = date.today()

    name_cache_key = build_cache_key("api_stock_name", stock_id=sid)
    cached_name = cache.get(name_cache_key)
    if cached_name and isinstance(cached_name.get("name"), str) and cached_name.get("name"):
        stock_name: str | None = str(cached_name["name"]).strip() or None
    else:
        stock_name = None
        try:
            client = FinMindClient(api_key=token_resolved)
            stock_name = client.fetch_stock_name(sid)
        except Exception:
            stock_name = None
        if stock_name:
            cache.set(name_cache_key, {"ts": time.time(), "name": stock_name})

    price_cache_key = build_cache_key("api_latest_price", stock_id=sid, asof=today.isoformat())
    cached_price = cache.get(price_cache_key)

    if cached_price and isinstance(cached_price.get("row"), dict):
        price_row = cached_price["row"]
    else:
        try:
            client = FinMindClient(api_key=token_resolved)
            start_ts = pd.Timestamp(today.year, today.month, 1) - pd.DateOffset(months=2)
            start_date = date(int(start_ts.year), int(start_ts.month), 1)
            df_price = client.fetch_stock_price(stock_id=sid, start_date=start_date, end_date=today)
        except FinMindError as e:
            raise HTTPException(status_code=502, detail=str(e))

        if df_price.empty:
            raise HTTPException(status_code=404, detail="no price data")

        last = df_price.iloc[-1]
        price_row = {
            "date": str(pd.Timestamp(last["date"]).date()),
            "open": None if pd.isna(last.get("open")) else float(last.get("open")),
            "high": None if pd.isna(last.get("max")) else float(last.get("max")),
            "low": None if pd.isna(last.get("min")) else float(last.get("min")),
            "close": None if pd.isna(last.get("close")) else float(last.get("close")),
            "spread": None if pd.isna(last.get("spread")) else float(last.get("spread")),
            "volume": None if pd.isna(last.get("Trading_Volume")) else int(float(last.get("Trading_Volume"))),
            "money": None if pd.isna(last.get("Trading_money")) else int(float(last.get("Trading_money"))),
            "turnover": None if pd.isna(last.get("Trading_turnover")) else int(float(last.get("Trading_turnover"))),
        }
        cache.set(price_cache_key, {"ts": time.time(), "row": price_row})

    latest_date = price_row.get("date")
    if not latest_date:
        raise HTTPException(status_code=404, detail="no price date")

    # Task 1.4: query a 5-trading-day window (≈ 10 calendar days) and pick the most
    # recent date with institutional data. T86 data is published after market close
    # (~15:30) and FinMind sync may lag a few more minutes; a single-day window
    # routinely returned empty during that gap.
    inst_cache_key = build_cache_key("api_institutional", stock_id=sid, date=latest_date)
    cached_inst = cache.get(inst_cache_key)

    if (
        cached_inst
        and isinstance(cached_inst.get("rows"), list)
        and cached_inst.get("status") in ("fresh", "stale")
    ):
        inst_rows = cached_inst["rows"]
        inst_as_of = cached_inst.get("as_of")
        inst_status = cached_inst.get("status")
        inst_lag_days = int(cached_inst.get("lag_days") or 0)
    else:
        try:
            client = FinMindClient(api_key=token_resolved)
            end_d = date.fromisoformat(latest_date)
            start_d = end_d - timedelta(days=10)
            df_inst = client.fetch_institutional_investors_buy_sell(
                stock_id=sid, start_date=start_d, end_date=end_d
            )
        except FinMindError as e:
            raise HTTPException(status_code=502, detail=str(e))

        name_map = {
            "Foreign_Investor": "外資",
            "Investment_Trust": "投信",
            "Dealer_self": "自營商(自行買賣)",
            "Dealer_Hedging": "自營商(避險)",
            "Foreign_Dealer_Self": "外資自營商",
        }

        inst_rows: list[dict[str, Any]] = []
        inst_as_of: str | None = None
        inst_status = "unavailable"
        inst_lag_days = 0

        if not df_inst.empty:
            df_inst = df_inst.dropna(subset=["date"])
        if not df_inst.empty:
            most_recent = pd.Timestamp(df_inst["date"].max()).date()
            inst_as_of = most_recent.isoformat()
            df_latest = df_inst[pd.to_datetime(df_inst["date"]).dt.date == most_recent]
            for _, r in df_latest.iterrows():
                name = str(r.get("name"))
                inst_rows.append(
                    {
                        "category": name_map.get(name, name),
                        "buy": int(r.get("buy", 0) or 0),
                        "sell": int(r.get("sell", 0) or 0),
                        "net": int(r.get("net", 0) or 0),
                    }
                )
            if most_recent == end_d:
                inst_status = "fresh"
                inst_lag_days = 0
            else:
                inst_status = "stale"
                # business-day delta gives a meaningful "N trading days behind"
                inst_lag_days = int(
                    len(pd.bdate_range(most_recent, end_d)) - 1
                )

        # Only cache results that contain data. Caching "unavailable" would trap
        # users in the 15:30-16:30 window when T86 publishes mid-session.
        if inst_status in ("fresh", "stale"):
            cache.set(
                inst_cache_key,
                {
                    "ts": time.time(),
                    "rows": inst_rows,
                    "as_of": inst_as_of,
                    "status": inst_status,
                    "lag_days": inst_lag_days,
                },
            )

    return {
        "stock_id": sid,
        "stock_name": stock_name,
        "price": price_row,
        "institutional": inst_rows,
        "institutional_as_of": inst_as_of,
        "institutional_status": inst_status,
        "institutional_lag_days": inst_lag_days,
    }


@app.get("/api/stocks/{stock_id}/revenue")
def revenue(
    stock_id: str,
    years: int = Query(default=5, ge=1, le=10),
    token: str | None = Query(default=None, description="FinMind API token (optional). Prefer header X-FinMind-Token."),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")

    token_resolved = _require_token(token, x_finmind_token)

    start, end, _today = _compute_month_range(years)

    cache_key = build_cache_key(
        "api_month_revenue_v2",  # v2: month derived from revenue_year/month, not announce date
        stock_id=sid,
        start=f"{start:%Y-%m}",
        end=f"{end:%Y-%m}",
        years=str(years),
    )

    cached = cache.get(cache_key)
    if cached and isinstance(cached.get("rows"), list):
        df = pd.DataFrame(cached["rows"])
        df["month"] = pd.to_datetime(df["month"], errors="coerce")
        for col in ["revenue", "ma_3", "ma_6", "ma_12"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
    else:
        try:
            client = FinMindClient(api_key=token_resolved)
            df_raw = client.fetch_month_revenue(
                stock_id=sid,
                start_date=date(start.year, start.month, 1),
                end_date=date(end.year, end.month, 1),
            )
        except FinMindError as e:
            raise HTTPException(status_code=502, detail=str(e))

        df = df_raw.copy()
        if df.empty:
            df = pd.DataFrame(columns=["month", "revenue"])

        idx = build_continuous_month_index(start, end)
        df = reindex_to_continuous_months(df, idx)
        df = add_mas(df, windows=[3, 6, 12])

        cache.set(
            cache_key,
            {
                "ts": time.time(),
                "rows": [
                    {
                        "month": str(pd.Timestamp(m).date()),
                        "revenue": (None if pd.isna(v) else float(v)),
                        "ma_3": (None if pd.isna(ma3) else float(ma3)),
                        "ma_6": (None if pd.isna(ma6) else float(ma6)),
                        "ma_12": (None if pd.isna(ma12) else float(ma12)),
                    }
                    for m, v, ma3, ma6, ma12 in zip(
                        df["month"],
                        df["revenue"],
                        df.get("ma_3"),
                        df.get("ma_6"),
                        df.get("ma_12"),
                        strict=False,
                    )
                ],
            },
        )

    rows_out = []
    for _, r in df.iterrows():
        rows_out.append(
            {
                "month": str(pd.Timestamp(r["month"]).date()) if not pd.isna(r["month"]) else None,
                "revenue": None if pd.isna(r.get("revenue")) else float(r.get("revenue")),
                "ma_3": None if pd.isna(r.get("ma_3")) else float(r.get("ma_3")),
                "ma_6": None if pd.isna(r.get("ma_6")) else float(r.get("ma_6")),
                "ma_12": None if pd.isna(r.get("ma_12")) else float(r.get("ma_12")),
            }
        )

    return {
        "stock_id": sid,
        "start_month": f"{start:%Y-%m}",
        "end_month": f"{end:%Y-%m}",
        "rows": rows_out,
    }


@app.get("/api/stocks/{stock_id}/price_history")
def price_history(
    stock_id: str,
    years: int = Query(default=5, ge=1, le=10),
    token: str | None = Query(default=None, description="FinMind API token (optional). Prefer header X-FinMind-Token."),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")

    token_resolved = _require_token(token, x_finmind_token)
    start, end, today = _compute_month_range(years)

    cache_key = build_cache_key(
        "api_price_history_monthly",
        stock_id=sid,
        start=f"{start:%Y-%m}",
        end=f"{end:%Y-%m}",
        years=str(years),
    )

    cached = cache.get(cache_key)
    if cached and isinstance(cached.get("rows"), list):
        return {
            "stock_id": sid,
            "start_month": f"{start:%Y-%m}",
            "end_month": f"{end:%Y-%m}",
            "rows": cached["rows"],
        }

    try:
        client = FinMindClient(api_key=token_resolved)
        df_daily = client.fetch_stock_price(
            stock_id=sid,
            start_date=date(start.year, start.month, 1),
            end_date=today,
        )
    except FinMindError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if df_daily.empty:
        df_month = pd.DataFrame(columns=["month", "close"])
    else:
        df_daily = df_daily.copy()
        df_daily["date"] = pd.to_datetime(df_daily["date"], errors="coerce")
        df_daily["close"] = pd.to_numeric(df_daily.get("close"), errors="coerce")
        df_daily = df_daily.dropna(subset=["date"]).sort_values("date")
        df_daily["month"] = df_daily["date"].dt.to_period("M").dt.to_timestamp(how="start")
        df_month = df_daily.groupby("month", as_index=False).last()
        df_month = df_month[["month", "close"]]

    idx = build_continuous_month_index(start, end)
    df_month = reindex_to_continuous_months(df_month, idx)

    rows = [
        {
            "month": str(pd.Timestamp(m).date()) if not pd.isna(m) else None,
            "close": None if pd.isna(v) else float(v),
        }
        for m, v in zip(df_month["month"], df_month.get("close"), strict=False)
    ]

    cache.set(cache_key, {"ts": time.time(), "rows": rows})

    return {
        "stock_id": sid,
        "start_month": f"{start:%Y-%m}",
        "end_month": f"{end:%Y-%m}",
        "rows": rows,
    }


@app.get("/api/stocks/{stock_id}/dividend_yield")
def dividend_yield(
    stock_id: str,
    years: int = Query(default=5, ge=1, le=10),
    token: str | None = Query(default=None, description="FinMind API token (optional). Prefer header X-FinMind-Token."),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")

    token_resolved = _require_token(token, x_finmind_token)
    start, end, today = _compute_month_range(years)

    cache_key = build_cache_key(
        "api_dividend_yield_monthly",
        stock_id=sid,
        start=f"{start:%Y-%m}",
        end=f"{end:%Y-%m}",
        years=str(years),
    )

    cached = cache.get(cache_key)
    if cached and isinstance(cached.get("rows"), list):
        return {
            "stock_id": sid,
            "start_month": f"{start:%Y-%m}",
            "end_month": f"{end:%Y-%m}",
            "rows": cached["rows"],
        }

    try:
        client = FinMindClient(api_key=token_resolved)
        df_daily = client.fetch_stock_per(
            stock_id=sid,
            start_date=date(start.year, start.month, 1),
            end_date=today,
        )
    except FinMindError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if df_daily.empty:
        df_month = pd.DataFrame(columns=["month", "dividend_yield"])
    else:
        df_daily = df_daily.copy()
        df_daily["date"] = pd.to_datetime(df_daily["date"], errors="coerce")
        df_daily["dividend_yield"] = pd.to_numeric(df_daily.get("dividend_yield"), errors="coerce")
        df_daily = df_daily.dropna(subset=["date"]).sort_values("date")
        df_daily["month"] = df_daily["date"].dt.to_period("M").dt.to_timestamp(how="start")
        df_month = df_daily.groupby("month", as_index=False).last()
        df_month = df_month[["month", "dividend_yield"]]

    idx = build_continuous_month_index(start, end)
    df_month = reindex_to_continuous_months(df_month, idx)

    rows = [
        {
            "month": str(pd.Timestamp(m).date()) if not pd.isna(m) else None,
            "dividend_yield": None if pd.isna(v) else float(v),
        }
        for m, v in zip(df_month["month"], df_month.get("dividend_yield"), strict=False)
    ]

    cache.set(cache_key, {"ts": time.time(), "rows": rows})

    return {
        "stock_id": sid,
        "start_month": f"{start:%Y-%m}",
        "end_month": f"{end:%Y-%m}",
        "rows": rows,
    }


@app.get("/api/stocks/{stock_id}/dividends_cash")
def dividends_cash(
    stock_id: str,
    years: int = Query(default=5, ge=1, le=10),
    token: str | None = Query(default=None, description="FinMind API token (optional). Prefer header X-FinMind-Token."),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")

    token_resolved = _require_token(token, x_finmind_token)
    start, end, today = _compute_month_range(years)

    cache_key = build_cache_key(
        "api_dividends_cash_monthly_v2",
        stock_id=sid,
        start=f"{start:%Y-%m}",
        end=f"{end:%Y-%m}",
        years=str(years),
    )

    cached = cache.get(cache_key)
    if cached and isinstance(cached.get("rows"), list):
        return {
            "stock_id": sid,
            "start_month": f"{start:%Y-%m}",
            "end_month": f"{end:%Y-%m}",
            "rows": cached["rows"],
        }

    try:
        client = FinMindClient(api_key=token_resolved)
        df_raw = client.fetch_stock_dividend(
            stock_id=sid,
            start_date=date(start.year, start.month, 1),
            end_date=today,
        )
    except FinMindError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if df_raw.empty:
        df_month = pd.DataFrame(columns=["month", "cash_dividend"])
    else:
        df = df_raw.copy()
        df["ex_dividend_date"] = pd.to_datetime(df.get("ex_dividend_date"), errors="coerce")
        df["cash_dividend"] = pd.to_numeric(df.get("cash_dividend"), errors="coerce")
        df["year"] = pd.to_numeric(df.get("year"), errors="coerce")
        df = df.dropna(subset=["year"])
        df["year"] = df["year"].astype(int)

        # aggregate per year (there could be multiple distributions per year)
        df_year = (
            df.groupby("year", as_index=False)
            .agg(
                cash_dividend=("cash_dividend", "sum"),
                ex_dividend_date=("ex_dividend_date", "max"),
            )
            .sort_values("year")
        )

        # place the yearly dividend on the ex-dividend month; keep month index aligned to revenue/yield
        df_year["month"] = df_year["ex_dividend_date"].dt.to_period("M").dt.to_timestamp(how="start")
        df_month = df_year[["month", "cash_dividend"]]

    idx = build_continuous_month_index(start, end)
    df_month = reindex_to_continuous_months(df_month, idx)

    rows = [
        {
            "month": str(pd.Timestamp(m).date()) if not pd.isna(m) else None,
            "cash_dividend": None if pd.isna(v) else float(v),
        }
        for m, v in zip(df_month["month"], df_month.get("cash_dividend"), strict=False)
    ]

    cache.set(cache_key, {"ts": time.time(), "rows": rows})

    return {
        "stock_id": sid,
        "start_month": f"{start:%Y-%m}",
        "end_month": f"{end:%Y-%m}",
        "rows": rows,
    }


@app.get("/api/stocks/{stock_id}/volume_turnover_recent")
def volume_turnover_recent(
    stock_id: str,
    token: str | None = Query(default=None, description="FinMind API token (optional). Prefer header X-FinMind-Token."),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    """近 3 個月每日成交量 / 成交筆數。"""

    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")

    token_resolved = _require_token(token, x_finmind_token)
    today = date.today()
    start_ts = pd.Timestamp(today) - pd.DateOffset(months=3)
    start_date = date(int(start_ts.year), int(start_ts.month), int(start_ts.day))

    cache_key = build_cache_key(
        "api_volume_turnover_recent_3m",
        stock_id=sid,
        start=start_date.isoformat(),
        end=today.isoformat(),
    )

    cached = cache.get(cache_key)
    if cached and isinstance(cached.get("rows"), list):
        return {
            "stock_id": sid,
            "start_date": start_date.isoformat(),
            "end_date": today.isoformat(),
            "rows": cached["rows"],
        }

    try:
        client = FinMindClient(api_key=token_resolved)
        df = client.fetch_stock_price(stock_id=sid, start_date=start_date, end_date=today)
    except FinMindError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if df.empty:
        rows: list[dict[str, Any]] = []
    else:
        df = df.copy()
        df["date"] = pd.to_datetime(df.get("date"), errors="coerce")
        df["Trading_Volume"] = pd.to_numeric(df.get("Trading_Volume"), errors="coerce")
        df["Trading_turnover"] = pd.to_numeric(df.get("Trading_turnover"), errors="coerce")
        df = df.dropna(subset=["date"]).drop_duplicates(subset=["date"], keep="last").sort_values("date")

        rows = [
            {
                "date": str(pd.Timestamp(d).date()) if not pd.isna(d) else None,
                "volume": None if pd.isna(v) else int(float(v)),
                "turnover": None if pd.isna(t) else int(float(t)),
            }
            for d, v, t in zip(df["date"], df.get("Trading_Volume"), df.get("Trading_turnover"), strict=False)
        ]

    cache.set(cache_key, {"ts": time.time(), "rows": rows})

    return {
        "stock_id": sid,
        "start_date": start_date.isoformat(),
        "end_date": today.isoformat(),
        "rows": rows,
    }


@app.get("/api/stocks/{stock_id}/roe_roa")
def roe_roa(
    stock_id: str,
    years: int = Query(default=5, ge=1, le=20),
    token: str | None = Query(default=None, description="FinMind API token (optional)."),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    """季度近四季 TTM ROE / ROA（與財報狗「近四季」一致）。

    計算方式：
      資料來源：FinMind（TaiwanStockFinancialStatements + TaiwanStockBalanceSheet）
               備援：Goodinfo.tw 爬蟲（FinMind 無資料時）
      NI    = 單季稅後淨利 (IncomeAfterTaxes，NT$元，FinMind 已為單季值)
      Equity = 歸屬於母公司業主之權益合計 (EquityAttributableToOwnersOfParent，NT$元)
      Assets = 資產總計 (TotalAssets，NT$元)
      ROE = 近四季 NI 合計 / 近四季平均 Equity × 100
      ROA = 近四季 NI 合計 / 近四季平均 Assets × 100
    """
    import logging

    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")

    token_resolved = _require_token(token, x_finmind_token)
    start, end, today = _compute_month_range(years)
    # Fetch 1 extra year so the first TTM window is fully warmed-up
    start_ext = start - pd.DateOffset(years=1)
    start_ext_date = date(int(start_ext.year), int(start_ext.month), 1)

    cache_key = build_cache_key(
        "api_roe_roa_v5",
        stock_id=sid,
        start=f"{start:%Y-%m}",
        end=f"{end:%Y-%m}",
        years=str(years),
    )

    cached = cache.get(cache_key)
    if cached and isinstance(cached.get("rows"), list):
        return {
            "stock_id": sid,
            "start_month": f"{start:%Y-%m}",
            "end_month": f"{end:%Y-%m}",
            "rows": cached["rows"],
        }

    ni_q: pd.Series | None = None
    equity: pd.Series | None = None
    assets: pd.Series | None = None
    source_used = "unknown"

    # ── Primary: FinMind ──────────────────────────────────────────────────────
    try:
        fm = FinMindClient(api_key=token_resolved)
        ni_q_raw = fm.fetch_quarterly_ni(sid, start_ext_date, today)
        equity_raw, assets_raw = fm.fetch_quarterly_bs_for_roe(sid, start_ext_date, today)

        if ni_q_raw.empty or equity_raw.empty or assets_raw.empty:
            raise FinMindError("FinMind returned no data for one or more series.")

        ni_q = ni_q_raw
        equity = equity_raw
        assets = assets_raw
        source_used = "finmind"
    except FinMindError as exc:
        logging.warning("ROE/ROA: FinMind failed (%s) — falling back to Goodinfo", exc)

    # ── Fallback: Goodinfo ────────────────────────────────────────────────────
    if ni_q is None:
        if GoodinfoClient is None:
            detail = "Data fetch failed (FinMind) and Goodinfo fallback is unavailable in this runtime."
            if GOODINFO_IMPORT_ERROR is not None:
                detail = f"{detail} Import error: {GOODINFO_IMPORT_ERROR}"
            raise HTTPException(status_code=502, detail=detail)
        try:
            gi = GoodinfoClient()
            ni_acc = gi.fetch_quarterly_income_acc(stock_id=sid)
            equity_gi, assets_gi = gi.fetch_quarterly_balance(stock_id=sid)
            ni_q = _deaccumulate_income(ni_acc)
            equity = equity_gi
            assets = assets_gi
            source_used = "goodinfo"
        except GoodinfoError as exc:
            raise HTTPException(status_code=502, detail=f"Data fetch failed (FinMind + Goodinfo): {exc}")
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Data fetch error: {exc}")

    rows = _compute_roe_roa_ttm(ni_q, equity, assets, start)  # type: ignore[arg-type]

    cache.set(cache_key, {"ts": time.time(), "rows": rows, "source": source_used})

    return {
        "stock_id": sid,
        "start_month": f"{start:%Y-%m}",
        "end_month": f"{end:%Y-%m}",
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# DCF valuation
# ---------------------------------------------------------------------------

@app.get("/api/stocks/{stock_id}/dcf")
def dcf_valuation(
    stock_id: str,
    years: int = Query(default=5, ge=1, le=10),
    growth_rate: float = Query(default=0.04, ge=0.0, le=0.2, description="Long-term earnings growth rate (e.g. 0.04 = 4%)"),
    discount_rate: float = Query(default=0.08, ge=0.01, le=0.3, description="Required rate of return (e.g. 0.08 = 8%)"),
    margin_of_safety: float = Query(default=0.30, ge=0.0, le=0.8, description="Margin of safety (e.g. 0.30 = 30%)"),
    token: str | None = Query(default=None),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    """Gordon Growth Model DCF: FairValue = EPS × Payout × (1+g) / (r − g)

    Returns fair_value, entry_price (with margin-of-safety), current_price, and
    EPS / payout_ratio used.  Returns 404 when insufficient data.
    """
    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")
    if discount_rate <= growth_rate:
        raise HTTPException(status_code=400, detail="discount_rate must be greater than growth_rate")

    token_resolved = _require_token(token, x_finmind_token)
    today = date.today()
    start, _end, _ = _compute_month_range(years)

    cache_key = build_cache_key(
        "api_dcf_v2",
        stock_id=sid,
        years=str(years),
        g=f"{growth_rate:.3f}",
        r=f"{discount_rate:.3f}",
        mos=f"{margin_of_safety:.2f}",
        asof=today.isoformat(),
    )
    cached = cache.get(cache_key)
    if cached and "fair_value" in cached:
        return {k: v for k, v in cached.items() if k != "ts"}

    start_date = date(start.year, start.month, 1)
    try:
        client = FinMindClient(api_key=token_resolved)
        eps_avg = client.fetch_annual_eps(stock_id=sid, start_date=start_date, end_date=today, years=years)
        payout_ratio = client.fetch_dividend_payout_ratio(
            stock_id=sid, start_date=start_date, end_date=today, years=years
        )
    except FinMindError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if eps_avg is None or payout_ratio is None or eps_avg <= 0:
        raise HTTPException(status_code=404, detail="Insufficient EPS or payout data to compute DCF")

    dps = eps_avg * payout_ratio
    fair_value = round(dps * (1 + growth_rate) / (discount_rate - growth_rate), 2)
    entry_price = round(fair_value * (1 - margin_of_safety), 2)

    # Fetch current price
    current_price: float | None = None
    try:
        cp_start = pd.Timestamp(today) - pd.DateOffset(months=1)
        df_cp = client.fetch_stock_price(
            stock_id=sid,
            start_date=date(int(cp_start.year), int(cp_start.month), 1),
            end_date=today,
        )
        if not df_cp.empty:
            current_price = float(df_cp.iloc[-1]["close"])
    except Exception:
        pass

    mos_vs_fair = (
        round((fair_value - current_price) / fair_value * 100, 2)
        if current_price is not None and fair_value > 0
        else None
    )

    result: dict[str, Any] = {
        "stock_id": sid,
        "fair_value": fair_value,
        "entry_price": entry_price,
        "current_price": current_price,
        "margin_of_safety_pct": mos_vs_fair,
        "eps_avg": round(float(eps_avg), 2),
        "payout_ratio": round(float(payout_ratio), 4),
        "params": {
            "growth_rate": growth_rate,
            "discount_rate": discount_rate,
            "margin_of_safety": margin_of_safety,
            "years_for_avg": years,
        },
    }
    cache.set(cache_key, {"ts": time.time(), **result})
    return result


# ---------------------------------------------------------------------------
# Historical debt ratio
# ---------------------------------------------------------------------------

@app.get("/api/stocks/{stock_id}/debt_ratio")
def debt_ratio(
    stock_id: str,
    years: int = Query(default=5, ge=1, le=20),
    token: str | None = Query(default=None),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    """Quarterly historical debt ratio = TotalLiabilities / TotalAssets × 100 (%)."""
    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")

    token_resolved = _require_token(token, x_finmind_token)
    start, _end, today = _compute_month_range(years)
    start_ext = start - pd.DateOffset(years=1)
    start_ext_date = date(int(start_ext.year), int(start_ext.month), 1)

    cache_key = build_cache_key(
        "api_debt_ratio_v1",
        stock_id=sid,
        start=f"{start:%Y-%m}",
        years=str(years),
        asof=today.isoformat(),
    )
    cached = cache.get(cache_key)
    if cached and isinstance(cached.get("rows"), list):
        return {
            "stock_id": sid,
            "start_month": f"{start:%Y-%m}",
            "end_month": f"{_end:%Y-%m}",
            "rows": cached["rows"],
        }

    try:
        client = FinMindClient(api_key=token_resolved)
        liabilities, assets = client.fetch_quarterly_bs_liabilities_assets(sid, start_ext_date, today)
    except FinMindError as e:
        raise HTTPException(status_code=502, detail=str(e))

    quarter_dates = sorted(
        dt for dt in assets.index if dt.month in {3, 6, 9, 12} and dt >= start
    )

    rows = []
    for dt in quarter_dates:
        asset_val = assets.get(dt)
        liab_val = liabilities.get(dt)
        if asset_val is None or pd.isna(asset_val) or float(asset_val) == 0:
            continue
        if liab_val is None or pd.isna(liab_val):
            continue
        ratio = round(float(liab_val) / float(asset_val) * 100, 2)
        q_num = (dt.month - 1) // 3 + 1
        rows.append({
            "quarter": str(dt.date()),
            "quarter_label": f"{dt.year} Q{q_num}",
            "debt_ratio": ratio,
            "liabilities": float(liab_val),
            "assets": float(asset_val),
        })

    cache.set(cache_key, {"ts": time.time(), "rows": rows})
    return {
        "stock_id": sid,
        "start_month": f"{start:%Y-%m}",
        "end_month": f"{_end:%Y-%m}",
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Annual Free Cash Flow
# ---------------------------------------------------------------------------

@app.get("/api/stocks/{stock_id}/free_cash_flow")
def free_cash_flow(
    stock_id: str,
    years: int = Query(default=5, ge=1, le=20),
    token: str | None = Query(default=None),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    """Annual Free Cash Flow = Operating CF − CapEx.

    Uses Q4 (Dec) cumulative annual values from TaiwanStockCashFlowsStatement.
    Also returns FCF as % of share capital and multi-year averages.
    """
    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")

    token_resolved = _require_token(token, x_finmind_token)
    start, _end, today = _compute_month_range(years)
    start_ext = start - pd.DateOffset(years=1)
    start_ext_date = date(int(start_ext.year), int(start_ext.month), 1)

    cache_key = build_cache_key(
        "api_fcf_v2",
        stock_id=sid,
        start=f"{start:%Y-%m}",
        years=str(years),
        asof=today.isoformat(),
    )
    cached = cache.get(cache_key)
    if cached and isinstance(cached.get("rows"), list):
        return {
            "stock_id": sid,
            "rows": cached["rows"],
            "share_capital": cached.get("share_capital"),
            "fcf_avg": cached.get("fcf_avg"),
            "fcf_avg_pct_capital": cached.get("fcf_avg_pct_capital"),
        }

    try:
        client = FinMindClient(api_key=token_resolved)
        df_fcf, share_capital = client.fetch_annual_fcf_data(sid, start_ext_date, today)
    except FinMindError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Filter to requested year range
    start_year = start.year
    if not df_fcf.empty:
        df_fcf = df_fcf[df_fcf["year"] >= start_year]

    rows: list[dict[str, Any]] = []
    for _, r in df_fcf.iterrows():
        fcf_val = r.get("fcf")
        capex_val = r.get("capex")
        opcf_val = r.get("operating_cf")
        fcf_pct: float | None = None
        if fcf_val is not None and share_capital and share_capital != 0:
            fcf_pct = round(float(fcf_val) / share_capital * 100, 2)
        is_full_year = bool(r["is_full_year"]) if "is_full_year" in r and not pd.isna(r["is_full_year"]) else True
        rows.append({
            "year": int(r["year"]),
            "operating_cf": None if opcf_val is None or pd.isna(opcf_val) else round(float(opcf_val), 0),
            "capex": None if capex_val is None or pd.isna(capex_val) else round(float(capex_val), 0),
            "fcf": None if fcf_val is None or pd.isna(fcf_val) else round(float(fcf_val), 0),
            "fcf_pct_capital": fcf_pct,
            "quarter_label": str(r["quarter_label"]) if "quarter_label" in r and not pd.isna(r["quarter_label"]) else f"{int(r['year'])} Q4",
            "is_full_year": is_full_year,
        })

    # Averages — full fiscal years only, so a partial in-progress year doesn't skew it
    valid_fcf = [float(r["fcf"]) for r in rows if r["fcf"] is not None and r["is_full_year"]]
    fcf_avg = round(sum(valid_fcf) / len(valid_fcf), 0) if valid_fcf else None
    valid_pct = [float(r["fcf_pct_capital"]) for r in rows if r["fcf_pct_capital"] is not None and r["is_full_year"]]
    fcf_avg_pct = round(sum(valid_pct) / len(valid_pct), 2) if valid_pct else None

    cache.set(
        cache_key,
        {
            "ts": time.time(),
            "rows": rows,
            "share_capital": share_capital,
            "fcf_avg": fcf_avg,
            "fcf_avg_pct_capital": fcf_avg_pct,
        },
    )
    return {
        "stock_id": sid,
        "rows": rows,
        "share_capital": share_capital,
        "fcf_avg": fcf_avg,
        "fcf_avg_pct_capital": fcf_avg_pct,
    }


@app.get("/api/stocks/{stock_id}/free_cash_flow_quarterly")
def free_cash_flow_quarterly(
    stock_id: str,
    years: int = Query(default=5, ge=1, le=20),
    token: str | None = Query(default=None),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    """Quarterly cumulative (YTD) Free Cash Flow = Operating CF − CapEx.

    Surfaces every filed quarter within the selected year range — many more data
    points than the annual (Q4-only) view.
    """
    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")

    token_resolved = _require_token(token, x_finmind_token)
    start, _end, today = _compute_month_range(years)
    start_ext = start - pd.DateOffset(years=1)
    start_ext_date = date(int(start_ext.year), int(start_ext.month), 1)

    cache_key = build_cache_key(
        "api_fcf_quarterly_v1",
        stock_id=sid,
        start=f"{start:%Y-%m}",
        years=str(years),
        asof=today.isoformat(),
    )
    cached = cache.get(cache_key)
    if cached and isinstance(cached.get("rows"), list):
        return {"stock_id": sid, "rows": cached["rows"]}

    try:
        client = FinMindClient(api_key=token_resolved)
        df = client.fetch_quarterly_fcf_data(sid, start_ext_date, today)
    except FinMindError as e:
        raise HTTPException(status_code=502, detail=str(e))

    rows: list[dict[str, Any]] = []
    if not df.empty:
        df = df[pd.to_datetime(df["quarter"], errors="coerce") >= start]
        for _, r in df.iterrows():
            opcf_val = r.get("operating_cf")
            capex_val = r.get("capex")
            fcf_val = r.get("fcf")
            rows.append({
                "quarter": str(r["quarter"]),
                "quarter_label": str(r["quarter_label"]),
                "operating_cf": None if opcf_val is None or pd.isna(opcf_val) else round(float(opcf_val), 0),
                "capex": None if capex_val is None or pd.isna(capex_val) else round(float(capex_val), 0),
                "fcf": None if fcf_val is None or pd.isna(fcf_val) else round(float(fcf_val), 0),
            })

    cache.set(cache_key, {"ts": time.time(), "rows": rows})
    return {"stock_id": sid, "rows": rows}


# ---------------------------------------------------------------------------
# Director / supervisor shareholding
# ---------------------------------------------------------------------------

@app.get("/api/stocks/{stock_id}/shareholding")
def shareholding(
    stock_id: str,
    token: str | None = Query(default=None),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    """Monthly director / foreign shareholding history from Goodinfo.tw.

    Returns time-series with columns: date, non_indep_shares, non_indep_pledged,
    non_indep_ratio, indep_shares, indep_pledged, indep_ratio,
    total_dir_shares, total_dir_pledged, total_dir_ratio,
    foreign_shares, foreign_ratio, total_issued_shares.
    Token is accepted but not required (Goodinfo does not need one).
    """
    import logging

    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")

    today = date.today()
    _token = _resolve_token(token, x_finmind_token)  # accepted but not mandatory

    cache_key = build_cache_key(
        "api_shareholding_history_v1",
        stock_id=sid,
        asof=f"{today.year}-{today.month:02d}",   # monthly cache bucket
    )
    cached = cache.get(cache_key)
    if cached and isinstance(cached.get("rows"), list):
        return {
            "stock_id": sid,
            "source": cached.get("source", "goodinfo"),
            "rows": cached["rows"],
        }

    rows: list[dict[str, Any]] = []
    source_used = "none"

    # ── Primary: Goodinfo time-series ────────────────────────────────────
    if GoodinfoClient is not None:
        try:
            gi = GoodinfoClient()
            df_gi = gi.fetch_shareholding_history(stock_id=sid)
            if not df_gi.empty:
                source_used = "goodinfo"
                for _, r in df_gi.iterrows():
                    dt_val = r.get("date")
                    date_str: str | None = (
                        str(pd.Timestamp(dt_val).date()) if dt_val is not None and not pd.isna(dt_val) else None
                    )
                    def _f(col: str) -> float | None:
                        v = r.get(col)
                        return None if v is None or (isinstance(v, float) and pd.isna(v)) else float(v)

                    rows.append({
                        "date": date_str,
                        "non_indep_shares":   _f("non_indep_shares"),
                        "non_indep_pledged":  _f("non_indep_pledged"),
                        "non_indep_ratio":    _f("non_indep_ratio"),
                        "indep_shares":       _f("indep_shares"),
                        "indep_pledged":      _f("indep_pledged"),
                        "indep_ratio":        _f("indep_ratio"),
                        "total_dir_shares":   _f("total_dir_shares"),
                        "total_dir_pledged":  _f("total_dir_pledged"),
                        "total_dir_ratio":    _f("total_dir_ratio"),
                        "foreign_shares":     _f("foreign_shares"),
                        "foreign_ratio":      _f("foreign_ratio"),
                        "total_issued_shares": _f("total_issued_shares"),
                    })
        except Exception as exc:
            logging.warning("shareholding: Goodinfo failed: %s", exc)

    # ── Fallback: MOPS/TWSE latest single-point ───────────────────────────
    if not rows and fetch_director_shareholding_mops is not None:
        logging.info("shareholding: Goodinfo empty — trying MOPS/TWSE fallback for %s", sid)
        try:
            df_mops = fetch_director_shareholding_mops(stock_id=sid)
        except Exception as exc:
            logging.warning("shareholding: MOPS fallback failed: %s", exc)
            df_mops = pd.DataFrame()

        if not df_mops.empty:
            source_used = "mops"
            # MOPS data is single-date; emit as list of individual persons
            latest_dt = df_mops["date"].max() if "date" in df_mops.columns else None
            date_str_mops = (
                str(pd.Timestamp(latest_dt).date())
                if latest_dt is not None and not pd.isna(latest_dt)
                else None
            )
            total_shares = 0.0
            total_ratio = 0.0
            for _, r in df_mops.iterrows():
                shares_raw = r.get("shares")
                ratio_raw = r.get("ratio")
                shares_val = float(shares_raw) if shares_raw is not None and not pd.isna(shares_raw) else 0.0
                ratio_val = float(ratio_raw) if ratio_raw is not None and not pd.isna(ratio_raw) else 0.0
                total_shares += shares_val
                total_ratio += ratio_val
            rows.append({
                "date": date_str_mops,
                "non_indep_shares": None,
                "non_indep_pledged": None,
                "non_indep_ratio": None,
                "indep_shares": None,
                "indep_pledged": None,
                "indep_ratio": None,
                "total_dir_shares": total_shares or None,
                "total_dir_pledged": None,
                "total_dir_ratio": round(total_ratio, 4) if total_ratio else None,
                "foreign_shares": None,
                "foreign_ratio": None,
                "total_issued_shares": None,
                "_persons": [
                    {
                        "person_name": str(r.get("person_name", "")).strip() or None,
                        "person_type": str(r.get("person_type", "")).strip() or None,
                        "shares": None if pd.isna(r.get("shares", float("nan"))) else int(float(r.get("shares"))),
                        "ratio": None if pd.isna(r.get("ratio", float("nan"))) else round(float(r.get("ratio")), 4),
                    }
                    for _, r in df_mops.iterrows()
                ],
            })

    cache.set(cache_key, {"ts": time.time(), "source": source_used, "rows": rows})
    return {"stock_id": sid, "source": source_used, "rows": rows}


# ---------------------------------------------------------------------------
# Shareholder structure distribution — 集保股權分散表 (weekly, by lot level)
# ---------------------------------------------------------------------------

# FinMind TaiwanStockHoldingSharesPer level → human label (15 lot brackets).
_SPREAD_LEVEL_LABELS: dict[str, str] = {
    "1": "1–999 股",
    "2": "1,000–5,000 股",
    "3": "5,001–10,000 股",
    "4": "10,001–15,000 股",
    "5": "15,001–20,000 股",
    "6": "20,001–30,000 股",
    "7": "30,001–40,000 股",
    "8": "40,001–50,000 股",
    "9": "50,001–100,000 股",
    "10": "100,001–200,000 股",
    "11": "200,001–400,000 股",
    "12": "400,001–600,000 股",
    "13": "600,001–800,000 股",
    "14": "800,001–1,000,000 股",
    "15": "1,000,001 股以上（大戶）",
}


@app.get("/api/stocks/{stock_id}/shareholding_spread")
def shareholding_spread(
    stock_id: str,
    years: int = Query(default=3, ge=1, le=10),
    token: str | None = Query(default=None),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    """股東持股結構分佈 — 集保戶股權分散表 (FinMind TaiwanStockHoldingSharesPer).

    Weekly data, broken down into 15 lot-size brackets (1 = 散戶 small lots,
    15 = 大戶 > 1,000,000 shares). Returns each bracket's percent share over time
    so the frontend can draw a stacked area chart plus a per-week pie chart.
    """
    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")

    token_resolved = _require_token(token, x_finmind_token)

    start, _end, today = _compute_month_range(years)
    start_d = date(start.year, start.month, 1)

    levels_meta = [{"level": lv, "label": lbl} for lv, lbl in _SPREAD_LEVEL_LABELS.items()]

    cache_key = build_cache_key(
        "api_shareholding_spread_v3",
        stock_id=sid,
        years=str(years),
        asof=today.isoformat(),
    )
    cached = cache.get(cache_key)
    if cached and "dates" in cached:
        return {
            "stock_id": sid,
            "levels": levels_meta,
            "dates": cached["dates"],
            "percent": cached["percent"],
            "error": cached.get("error"),
        }

    df: pd.DataFrame | None = None
    data_source = "FinMind"

    # Try FinMind first; fall back to TDCC scraper if plan restriction or empty.
    try:
        client = FinMindClient(api_key=token_resolved)
        df = client.fetch_shareholding_spread(sid, start_d, today)
    except FinMindError:
        df = pd.DataFrame()  # trigger TDCC fallback below

    if df is None or df.empty:
        # FinMind unavailable or plan-restricted — scrape TDCC directly (free, ~1 year history).
        data_source = "TDCC"
        try:
            tdcc = TDCCClient()
            df = tdcc.fetch_shareholding_spread(sid, start_d, today)
        except TDCCError as exc:
            payload = {"dates": [], "percent": {}, "error": f"集保資料暫時無法取得：{str(exc)[:120]}"}
            cache.set(cache_key, {"ts": time.time(), **payload})
            return {"stock_id": sid, "levels": levels_meta, **payload}

    if df is None or df.empty:
        payload = {
            "dates": [],
            "percent": {},
            "error": "查無集保股權分散資料（FinMind 方案限制，TDCC 亦查無資料）。",
        }
        cache.set(cache_key, {"ts": time.time(), **payload})
        return {"stock_id": sid, "levels": levels_meta, **payload}

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    # Keep only the 15 known lot brackets; drop FinMind's 合計/差異數 rows (16/17).
    df = df[df["HoldingSharesLevel"].isin(_SPREAD_LEVEL_LABELS.keys())]
    df["percent"] = pd.to_numeric(df["percent"], errors="coerce")

    # Pivot to date × level matrix of percents, ascending by date.
    pivot = (
        df.pivot_table(index="date", columns="HoldingSharesLevel", values="percent", aggfunc="last")
        .sort_index()
    )
    dates = [str(d.date()) for d in pivot.index]
    percent: dict[str, list[float | None]] = {}
    for lv in _SPREAD_LEVEL_LABELS:
        if lv in pivot.columns:
            percent[lv] = [None if pd.isna(v) else round(float(v), 4) for v in pivot[lv]]
        else:
            percent[lv] = [None] * len(dates)

    note = None if data_source == "FinMind" else "資料來源：集保結算所（TDCC），最多顯示近 1 年週度資料"
    payload = {"dates": dates, "percent": percent, "error": note}
    cache.set(cache_key, {"ts": time.time(), **payload})
    return {"stock_id": sid, "levels": levels_meta, **payload}


# ---------------------------------------------------------------------------
# Capital formation 股本形成
# ---------------------------------------------------------------------------

@app.get("/api/stocks/{stock_id}/capital_formation")
def capital_formation(
    stock_id: str,
    token: str | None = Query(default=None),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    """Cumulative 股本形成 breakdown (現金增資 / 盈餘轉增資 / 其他) in 億元.

    Primary source: MoneyDJ HTML scraper (works without login for a small set of
    popular stocks such as 2330).
    Fallback: reconstructed from FinMind balance-sheet + dividend data; data
    starts from ~2012, so pre-2012 capital is lumped into 其他.
    """
    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")

    token_resolved = _resolve_token(token, x_finmind_token)

    cache_key = build_cache_key(
        "api_capital_formation_v1",
        stock_id=sid,
        asof=date.today().isoformat(),
    )
    cached = cache.get(cache_key)
    if cached and isinstance(cached.get("rows"), list):
        return {
            "stock_id": sid,
            "rows": cached["rows"],
            "source": cached.get("source", "unknown"),
            "note": cached.get("note"),
        }

    # ── Primary: MoneyDJ ─────────────────────────────────────────────────────
    rows = fetch_capital_formation_moneydj(sid)
    if rows:
        source = "MoneyDJ"
        note = None
    else:
        # ── Fallback: FinMind reconstruction ─────────────────────────────────
        if not token_resolved:
            raise HTTPException(
                status_code=401,
                detail="MoneyDJ資料需要登入，FinMind重建需要API金鑰。請提供token參數或X-FinMind-Token header。",
            )
        rows = fetch_capital_formation_finmind(sid, token_resolved)
        source = "FinMind"
        note = "資料來源：FinMind財務報表重建，2012年以前資本列入「其他」，僅供參考"

    if not rows:
        raise HTTPException(status_code=404, detail="查無股本形成資料")

    result = {
        "stock_id": sid,
        "rows": rows,
        "source": source,
        "note": note,
    }
    cache.set(cache_key, {"ts": time.time(), **result})
    return result


# ---------------------------------------------------------------------------
# Operating turnover days (DSO / DIO / DPO / CCC)
# ---------------------------------------------------------------------------

@app.get("/api/stocks/{stock_id}/turnover_days")
def turnover_days(
    stock_id: str,
    years: int = Query(default=5, ge=1, le=20),
    token: str | None = Query(default=None),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    """Quarterly operating turnover days.

    DSO  = AccountsReceivable / (OperatingRevenue / 91)
    DIO  = Inventories         / (OperatingCosts   / 91)
    DPO  = AccountsPayable     / (OperatingCosts   / 91)
    CCC  = DSO + DIO − DPO
    """
    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")

    token_resolved = _require_token(token, x_finmind_token)
    start, _end, today = _compute_month_range(years)
    # +1 year warmup for first quarter
    start_ext = start - pd.DateOffset(years=1)
    start_ext_date = date(int(start_ext.year), int(start_ext.month), 1)

    cache_key = build_cache_key(
        "api_turnover_days_v1",
        stock_id=sid,
        start=f"{start:%Y-%m}",
        years=str(years),
        asof=today.isoformat(),
    )
    cached = cache.get(cache_key)
    if cached and isinstance(cached.get("rows"), list):
        return {
            "stock_id": sid,
            "start_month": f"{start:%Y-%m}",
            "end_month": f"{_end:%Y-%m}",
            "rows": cached["rows"],
        }

    try:
        client = FinMindClient(api_key=token_resolved)
        df = client.fetch_turnover_days_data(sid, start_ext_date, today)
    except FinMindError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Filter to requested date range
    rows: list[dict[str, Any]] = []
    if not df.empty:
        df = df[pd.to_datetime(df["quarter"], errors="coerce") >= start]
        for _, r in df.iterrows():
            rows.append({
                "quarter": str(r["quarter"]),
                "quarter_label": str(r["quarter_label"]),
                "dso": None if r["dso"] is None or pd.isna(r["dso"]) else float(r["dso"]),
                "dio": None if r["dio"] is None or pd.isna(r["dio"]) else float(r["dio"]),
                "dpo": None if r["dpo"] is None or pd.isna(r["dpo"]) else float(r["dpo"]),
                "ccc": None if r["ccc"] is None or pd.isna(r["ccc"]) else float(r["ccc"]),
            })

    cache.set(cache_key, {"ts": time.time(), "rows": rows})
    return {
        "stock_id": sid,
        "start_month": f"{start:%Y-%m}",
        "end_month": f"{_end:%Y-%m}",
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# P/E River chart
# ---------------------------------------------------------------------------

@app.get("/api/stocks/{stock_id}/pe_river")
def pe_river(
    stock_id: str,
    years: int = Query(default=5, ge=1, le=20),
    token: str | None = Query(default=None),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    """Historical PER with percentile river bands + quarterly TTM EPS.

    Returns:
      per_rows     : [{date, per}]
      bands        : {p5, p25, p50, p75, p95}
      eps_ttm_rows : [{quarter, quarter_label, eps_ttm}]
    """
    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")

    token_resolved = _require_token(token, x_finmind_token)
    start, _end, today = _compute_month_range(years)
    start_ext = start - pd.DateOffset(years=1)
    start_ext_date = date(int(start_ext.year), int(start_ext.month), 1)

    cache_key = build_cache_key(
        "api_pe_river_v1",
        stock_id=sid,
        start=f"{start:%Y-%m}",
        years=str(years),
        asof=today.isoformat(),
    )
    cached = cache.get(cache_key)
    if cached and isinstance(cached.get("per_rows"), list):
        return {
            "stock_id": sid,
            "start_month": f"{start:%Y-%m}",
            "end_month": f"{_end:%Y-%m}",
            "per_rows": cached["per_rows"],
            "bands": cached.get("bands", {}),
            "eps_ttm_rows": cached.get("eps_ttm_rows", []),
        }

    try:
        client = FinMindClient(api_key=token_resolved)
        result = client.fetch_pe_river_data(sid, start_ext_date, today)
    except FinMindError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Filter per_rows to requested range
    start_str = f"{start:%Y-%m-%d}"
    per_rows = [r for r in result["per_rows"] if r["date"] >= start_str]

    payload = {
        "stock_id": sid,
        "start_month": f"{start:%Y-%m}",
        "end_month": f"{_end:%Y-%m}",
        "per_rows": per_rows,
        "bands": result["bands"],
        "eps_ttm_rows": result["eps_ttm_rows"],
    }
    cache.set(cache_key, {"ts": time.time(), **payload})
    return payload


@app.get("/api/token_usage")
def token_usage(
    token: str | None = Query(default=None, description="FinMind API token."),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    token_resolved = _require_token(token, x_finmind_token)
    try:
        return _fetch_token_usage_from_finmind(token_resolved)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ---------------------------------------------------------------------------
# === NEW: Margin Ratios (毛利率 / 營業利益率 / 淨利率) ===
# ---------------------------------------------------------------------------

@app.get("/api/stocks/{stock_id}/margins")
def margins(
    stock_id: str,
    years: int = Query(default=5, ge=1, le=20),
    token: str | None = Query(default=None),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")

    token_resolved = _require_token(token, x_finmind_token)
    start, _end, today = _compute_month_range(years)
    start_ext = start - pd.DateOffset(years=1)
    start_ext_date = date(int(start_ext.year), int(start_ext.month), 1)

    cache_key = build_cache_key("api_margins_v1", stock_id=sid, start=f"{start:%Y-%m}", years=str(years), asof=today.isoformat())
    cached = cache.get(cache_key)
    if cached and isinstance(cached.get("rows"), list):
        return {"stock_id": sid, "rows": cached["rows"]}

    try:
        client = FinMindClient(api_key=token_resolved)
        df = client.fetch_margin_ratios(sid, start_ext_date, today)
    except FinMindError as e:
        raise HTTPException(status_code=502, detail=str(e))

    rows = []
    if not df.empty:
        df_filt = df[pd.to_datetime(df["quarter"], errors="coerce") >= start]
        for _, r in df_filt.iterrows():
            rows.append({
                "quarter": str(r["quarter"]),
                "quarter_label": str(r["quarter_label"]),
                "gross_margin": None if r["gross_margin"] is None or pd.isna(r["gross_margin"]) else float(r["gross_margin"]),
                "operating_margin": None if r["operating_margin"] is None or pd.isna(r["operating_margin"]) else float(r["operating_margin"]),
                "net_margin": None if r["net_margin"] is None or pd.isna(r["net_margin"]) else float(r["net_margin"]),
            })

    cache.set(cache_key, {"ts": time.time(), "rows": rows})
    return {"stock_id": sid, "rows": rows}


# ---------------------------------------------------------------------------
# === NEW: EPS Trend + YoY Growth ===
# ---------------------------------------------------------------------------

@app.get("/api/stocks/{stock_id}/eps_trend")
def eps_trend(
    stock_id: str,
    years: int = Query(default=5, ge=1, le=20),
    token: str | None = Query(default=None),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")

    token_resolved = _require_token(token, x_finmind_token)
    start, _end, today = _compute_month_range(years)
    # Extra year for YoY comparison
    start_ext = start - pd.DateOffset(years=1)
    start_ext_date = date(int(start_ext.year), int(start_ext.month), 1)

    cache_key = build_cache_key("api_eps_trend_v1", stock_id=sid, start=f"{start:%Y-%m}", years=str(years), asof=today.isoformat())
    cached = cache.get(cache_key)
    if cached and isinstance(cached.get("rows"), list):
        return {"stock_id": sid, "rows": cached["rows"]}

    try:
        client = FinMindClient(api_key=token_resolved)
        df = client.fetch_eps_trend(sid, start_ext_date, today)
    except FinMindError as e:
        raise HTTPException(status_code=502, detail=str(e))

    rows = []
    if not df.empty:
        df_filt = df[pd.to_datetime(df["quarter"], errors="coerce") >= start]
        for _, r in df_filt.iterrows():
            rows.append({
                "quarter": str(r["quarter"]),
                "quarter_label": str(r["quarter_label"]),
                "eps": None if r["eps"] is None or pd.isna(r["eps"]) else float(r["eps"]),
                "eps_yoy": None if r["eps_yoy"] is None or pd.isna(r["eps_yoy"]) else float(r["eps_yoy"]),
            })

    cache.set(cache_key, {"ts": time.time(), "rows": rows})
    return {"stock_id": sid, "rows": rows}


# ---------------------------------------------------------------------------
# === NEW: Liquidity Ratios + BVPS ===
# ---------------------------------------------------------------------------

@app.get("/api/stocks/{stock_id}/liquidity")
def liquidity(
    stock_id: str,
    years: int = Query(default=5, ge=1, le=20),
    token: str | None = Query(default=None),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")

    token_resolved = _require_token(token, x_finmind_token)
    start, _end, today = _compute_month_range(years)
    start_ext = start - pd.DateOffset(years=1)
    start_ext_date = date(int(start_ext.year), int(start_ext.month), 1)

    cache_key = build_cache_key("api_liquidity_v2", stock_id=sid, start=f"{start:%Y-%m}", years=str(years), asof=today.isoformat())
    cached = cache.get(cache_key)
    if cached and isinstance(cached.get("rows"), list):
        return {"stock_id": sid, "rows": cached["rows"]}

    try:
        client = FinMindClient(api_key=token_resolved)
        df = client.fetch_liquidity_ratios(sid, start_ext_date, today)
    except FinMindError as e:
        raise HTTPException(status_code=502, detail=str(e))

    rows = []
    if not df.empty:
        df_filt = df[pd.to_datetime(df["quarter"], errors="coerce") >= start]
        for _, r in df_filt.iterrows():
            rows.append({
                "quarter": str(r["quarter"]),
                "quarter_label": str(r["quarter_label"]),
                "current_ratio": None if r["current_ratio"] is None or pd.isna(r["current_ratio"]) else float(r["current_ratio"]),
                "quick_ratio": None if r["quick_ratio"] is None or pd.isna(r["quick_ratio"]) else float(r["quick_ratio"]),
                "bvps": None if r["bvps"] is None or pd.isna(r["bvps"]) else float(r["bvps"]),
            })

    cache.set(cache_key, {"ts": time.time(), "rows": rows})
    return {"stock_id": sid, "rows": rows}


# ---------------------------------------------------------------------------
# === NEW: Foreign Shareholding % Trend ===
# ---------------------------------------------------------------------------

@app.get("/api/stocks/{stock_id}/foreign_holding")
def foreign_holding(
    stock_id: str,
    years: int = Query(default=5, ge=1, le=20),
    token: str | None = Query(default=None),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    """外資持股比例趨勢 — sourced from Goodinfo (monthly).

    Token is still accepted but not required for this endpoint since Goodinfo
    scraping does not need a FinMind key.
    """
    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")

    # Token optional for this endpoint (Goodinfo doesn't need it)
    _resolve_token(token, x_finmind_token)

    start, _end, today = _compute_month_range(years)
    start_ts = pd.Timestamp(start.year, start.month, 1)

    cache_key = build_cache_key("api_foreign_holding_v2", stock_id=sid, years=str(years), asof=today.isoformat())
    cached = cache.get(cache_key)
    if cached and "dates" in cached:
        return {"stock_id": sid, "dates": cached["dates"], "holding_pct": cached["holding_pct"], "error": cached.get("error")}

    # Use Goodinfo as primary data source (monthly 外資持股比率)
    if GoodinfoClient is None:
        payload = {"dates": [], "holding_pct": [], "error": "Goodinfo 模組無法載入"}
        cache.set(cache_key, {"ts": time.time(), **payload})
        return {"stock_id": sid, **payload}

    try:
        gc = GoodinfoClient()
        df = gc.fetch_shareholding_history(sid)
    except Exception as exc:
        payload = {"dates": [], "holding_pct": [], "error": f"Goodinfo 載入失敗：{str(exc)[:120]}"}
        cache.set(cache_key, {"ts": time.time(), **payload})
        return {"stock_id": sid, **payload}

    if df.empty or "foreign_ratio" not in df.columns:
        payload = {"dates": [], "holding_pct": [], "error": "查無外資持股比例資料"}
        cache.set(cache_key, {"ts": time.time(), **payload})
        return {"stock_id": sid, **payload}

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "foreign_ratio"])
    df = df[df["date"] >= start_ts].sort_values("date")
    df["foreign_ratio"] = pd.to_numeric(df["foreign_ratio"], errors="coerce")
    df = df.dropna(subset=["foreign_ratio"])

    payload = {
        "dates": [str(d.date()) for d in df["date"]],
        "holding_pct": [round(float(v), 2) for v in df["foreign_ratio"]],
        "error": None,
    }
    cache.set(cache_key, {"ts": time.time(), **payload})
    return {"stock_id": sid, **payload}


# ---------------------------------------------------------------------------
# === NEW: Valuation Extra (PEG + Graham Number) ===
# ---------------------------------------------------------------------------

@app.get("/api/stocks/{stock_id}/valuation_extra")
def valuation_extra(
    stock_id: str,
    years: int = Query(default=5, ge=1, le=20),
    token: str | None = Query(default=None),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    """PEG Ratio + Graham Number.

    PEG = current PER / EPS 3-year CAGR (%)
    Graham Number = sqrt(22.5 × avg_eps × latest_bvps)
    """
    import math

    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")

    token_resolved = _require_token(token, x_finmind_token)
    start, _end, today = _compute_month_range(years)
    start_ext = start - pd.DateOffset(years=4)  # need 4 years for CAGR
    start_ext_date = date(int(start_ext.year), int(start_ext.month), 1)

    cache_key = build_cache_key("api_valuation_extra_v3", stock_id=sid, years=str(years), asof=today.isoformat())
    cached = cache.get(cache_key)
    if cached and "graham_number" in cached:
        return {"stock_id": sid, **{k: v for k, v in cached.items() if k not in ("ts",)}}

    try:
        client = FinMindClient(api_key=token_resolved)

        # --- EPS data ---
        eps_df_raw = client.fetch_financial_statements(sid, start_ext_date, today)
        avg_eps: float | None = None
        eps_cagr: float | None = None
        if not eps_df_raw.empty:
            eps_sub = eps_df_raw[eps_df_raw["type"] == "EPS"].copy()
            if not eps_sub.empty:
                eps_sub["date"] = pd.to_datetime(eps_sub["date"])
                eps_sub["year"] = eps_sub["date"].dt.year
                yearly = (
                    eps_sub.groupby("year")
                    .agg(annual_eps=("value", "sum"), cnt=("value", "count"))
                    .reset_index()
                )
                full_years = yearly[yearly["cnt"] >= 4].sort_values("year")
                if len(full_years) >= 1:
                    avg_eps = float(full_years.tail(5)["annual_eps"].mean())
                if len(full_years) >= 4:
                    oldest = float(full_years.iloc[-4]["annual_eps"])
                    newest = float(full_years.iloc[-1]["annual_eps"])
                    if oldest > 0 and newest > 0:
                        eps_cagr = round((pow(newest / oldest, 1 / 3) - 1) * 100, 2)

        # --- BVPS from latest quarter ---
        latest_bvps: float | None = None
        liq_df = client.fetch_liquidity_ratios(sid, start_ext_date, today)
        if not liq_df.empty:
            valid_bvps = liq_df[liq_df["bvps"].notna()].sort_values("quarter")
            if not valid_bvps.empty:
                latest_bvps = float(valid_bvps.iloc[-1]["bvps"])

        # --- Current PER ---
        current_per: float | None = None
        per_start = date(today.year - 1, today.month, 1)
        per_df = client.fetch_stock_per(sid, per_start, today)
        if not per_df.empty and "PER" in per_df.columns:
            valid_per = per_df[per_df["PER"].notna() & (per_df["PER"] > 0)].sort_values("date")
            if not valid_per.empty:
                current_per = float(valid_per.iloc[-1]["PER"])

        # --- Current price ---
        current_price: float | None = None
        price_start = date(today.year, today.month, 1) if today.day > 5 else date(today.year, today.month - 1 if today.month > 1 else 12, 1)
        price_start = date(today.year - 1, today.month, 1)
        pr_df = client.fetch_stock_price(sid, price_start, today)
        if not pr_df.empty and "close" in pr_df.columns:
            valid_pr = pr_df[pr_df["close"].notna()].sort_values("date")
            if not valid_pr.empty:
                current_price = float(valid_pr.iloc[-1]["close"])

        # --- Compute PEG ---
        peg: float | None = None
        if current_per is not None and eps_cagr is not None and eps_cagr > 0:
            peg = round(current_per / eps_cagr, 2)

        # --- Compute Graham Number ---
        graham_number: float | None = None
        graham_mos: float | None = None
        if avg_eps is not None and latest_bvps is not None and avg_eps > 0 and latest_bvps > 0:
            graham_number = round(math.sqrt(22.5 * avg_eps * latest_bvps), 2)
            if current_price and current_price > 0:
                graham_mos = round((graham_number - current_price) / graham_number * 100, 1)

    except FinMindError as e:
        raise HTTPException(status_code=502, detail=str(e))

    result: dict[str, Any] = {
        "peg": peg,
        "eps_cagr": eps_cagr,
        "avg_eps": round(avg_eps, 2) if avg_eps is not None else None,
        "latest_bvps": latest_bvps,
        "current_per": current_per,
        "current_price": current_price,
        "graham_number": graham_number,
        "graham_mos_pct": graham_mos,
    }
    cache.set(cache_key, {"ts": time.time(), **result})
    return {"stock_id": sid, **result}


# ---------------------------------------------------------------------------
# === Industry-aware exclusions for buy_score criteria ===
# ---------------------------------------------------------------------------

# Maps industry keyword → list of criterion IDs that are not applicable
INDUSTRY_EXCLUSIONS: dict[str, list[str]] = {
    "金融保險": ["debt_ratio", "debt_ratio_strict", "equity_ratio"],
    "金融": ["debt_ratio", "debt_ratio_strict", "equity_ratio"],
    "銀行": ["debt_ratio", "debt_ratio_strict", "equity_ratio"],
    "保險": ["debt_ratio", "debt_ratio_strict", "equity_ratio"],
    "租賃": ["debt_ratio", "debt_ratio_strict", "equity_ratio"],
    "營建": ["dio"],
}


def _get_industry_exclusions(industry: str | None) -> set[str]:
    if not industry:
        return set()
    excluded: set[str] = set()
    for kw, cids in INDUSTRY_EXCLUSIONS.items():
        if kw in industry:
            excluded.update(cids)
    return excluded


# ---------------------------------------------------------------------------
# === Buy Score (買入評分) — v2: 加權總分制 + 放鬆門檻 ===
# ---------------------------------------------------------------------------

def _criterion(
    cid: str,
    label: str,
    weight: int,
    passed: bool | None,
    value: float | str | None,
    value_label: str,
    threshold: str,
    warning: str | None = None,
    not_applicable: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": cid,
        "label": label,
        "weight": weight,
        "pass": None if not_applicable else passed,
        "value": value,
        "value_label": value_label,
        "threshold": threshold,
        "warning": warning,
    }
    if not_applicable:
        result["not_applicable"] = True
    return result


@app.get("/api/stocks/{stock_id}/buy_score")
def buy_score(
    stock_id: str,
    token: str | None = Query(default=None),
    x_finmind_token: str | None = Header(default=None, alias="X-FinMind-Token"),
) -> dict[str, Any]:
    """買入評分卡（v3，24 指標）。

    評分邏輯：
      - 回傳 24 個 criteria（每項 pass=True 計 1 分）。
      - score = 通過數量；max_score = criteria 總數（應為 24）。
      - pass_rate = 通過數量 / 可評分數量（pass 非 null）。

    建議分級：
      - pass_rate >= 75%: 建議買進
      - pass_rate >= 60%: 可分批買進
      - pass_rate >= 50%: 觀察清單
      - pass_rate < 50%: 不建議買進
    """

    sid = stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id is required")

    token_resolved = _require_token(token, x_finmind_token)
    today = date.today()

    cache_key = build_cache_key("api_buy_score_v5", stock_id=sid, asof=today.isoformat())
    cached = cache.get(cache_key)
    if cached and isinstance(cached.get("criteria"), list):
        return {k: v for k, v in cached.items() if k != "ts"}

    client = FinMindClient(api_key=token_resolved)
    warnings: list[str] = []
    criteria: list[dict[str, Any]] = []

    # ── Industry classification (for not_applicable exclusions) ──────────────
    industry: str | None = None
    industry_cache_key = build_cache_key("api_stock_industry", stock_id=sid)
    industry_cached = cache.get(industry_cache_key)
    if industry_cached and "industry" in industry_cached:
        industry = industry_cached["industry"]
    else:
        try:
            industry = client.fetch_stock_industry(sid)
            cache.set(industry_cache_key, {"industry": industry, "ts": time.time()})
        except Exception as exc:
            warnings.append(f"industry_fetch: {exc}")
    excluded_criteria = _get_industry_exclusions(industry)

    # ── Shared data fetching ──────────────────────────────────────────────────
    fetch_start_ext = date(today.year - 4, today.month, 1)

    ni_q: pd.Series = pd.Series(dtype=float)
    equity_s: pd.Series = pd.Series(dtype=float)
    assets_s: pd.Series = pd.Series(dtype=float)
    try:
        ni_q = client.fetch_quarterly_ni(sid, fetch_start_ext, today)
        equity_s, assets_s = client.fetch_quarterly_bs_for_roe(sid, fetch_start_ext, today)
    except Exception as exc:
        warnings.append(f"roe_data: {exc}")

    fcf_rows: list[dict[str, Any]] = []
    try:
        df_fcf, _ = client.fetch_annual_fcf_data(sid, fetch_start_ext, today)
        if not df_fcf.empty:
            fcf_rows = df_fcf.to_dict("records")
    except Exception as exc:
        warnings.append(f"fcf_data: {exc}")

    liabilities_s: pd.Series = pd.Series(dtype=float)
    assets_debt_s: pd.Series = pd.Series(dtype=float)
    try:
        liabilities_s, assets_debt_s = client.fetch_quarterly_bs_liabilities_assets(sid, fetch_start_ext, today)
    except Exception as exc:
        warnings.append(f"debt_data: {exc}")

    score = 0

    # ── C1: ROE (TTM) > 12%  [weight=2] ─────────────────────────────────────
    latest_roe: float | None = None
    latest_roa: float | None = None
    try:
        if not ni_q.empty and not equity_s.empty and not assets_s.empty:
            roe_rows = _compute_roe_roa_ttm(
                ni_q, equity_s, assets_s,
                start_cutoff=pd.Timestamp(fetch_start_ext),
            )
            if roe_rows:
                latest_roe = roe_rows[-1].get("roe")
                latest_roa = roe_rows[-1].get("roa")
    except Exception as exc:
        warnings.append(f"roe_calc: {exc}")

    c1_pass = latest_roe is not None and latest_roe > 12.0
    if c1_pass:
        score += 2
    criteria.append(_criterion(
        "roe", "ROE (TTM) > 12%", weight=2,
        passed=c1_pass if latest_roe is not None else None,
        value=latest_roe,
        value_label=f"{latest_roe:.1f}%" if latest_roe is not None else "無資料",
        threshold="> 12%",
        warning=None if latest_roe is not None else "ROE 資料不足",
    ))

    c1b_pass = latest_roe is not None and latest_roe > 15.0
    criteria.append(_criterion(
        "roe_strict", "ROE (TTM) > 15%", weight=1,
        passed=c1b_pass if latest_roe is not None else None,
        value=latest_roe,
        value_label=f"{latest_roe:.1f}%" if latest_roe is not None else "無資料",
        threshold="> 15%",
        warning=None if latest_roe is not None else "ROE 資料不足",
    ))

    c1c_pass = latest_roa is not None and latest_roa > 6.0
    criteria.append(_criterion(
        "roa", "ROA (TTM) > 6%", weight=1,
        passed=c1c_pass if latest_roa is not None else None,
        value=latest_roa,
        value_label=f"{latest_roa:.1f}%" if latest_roa is not None else "無資料",
        threshold="> 6%",
        warning=None if latest_roa is not None else "ROA 資料不足",
    ))

    # ── C2: 近 3 年至少 2 年 FCF > 0  [weight=2] ────────────────────────────
    fcf_years_positive: int = 0
    fcf_years_checked: int = 0
    recent_fcf: list[dict[str, Any]] = []
    try:
        start_year = today.year - 3
        recent_fcf = [r for r in fcf_rows if r.get("year", 0) >= start_year and r.get("fcf") is not None]
        fcf_years_checked = len(recent_fcf)
        fcf_years_positive = sum(1 for r in recent_fcf if float(r["fcf"]) > 0)
    except Exception as exc:
        warnings.append(f"fcf_check: {exc}")

    c2_pass = fcf_years_checked >= 2 and fcf_years_positive >= 2
    if c2_pass:
        score += 2
    criteria.append(_criterion(
        "fcf", "近3年至少2年自由現金流 > 0", weight=2,
        passed=c2_pass if fcf_years_checked > 0 else None,
        value=fcf_years_positive,
        value_label=f"{fcf_years_positive}/{fcf_years_checked} 年正值" if fcf_years_checked > 0 else "無資料",
        threshold="3年內至少2年 > 0",
        warning=None if fcf_years_checked > 0 else "FCF 資料不足",
    ))

    c2b_value: float | None = None
    c2c_value: float | None = None
    try:
        if recent_fcf:
            recent_fcf_sorted = sorted(recent_fcf, key=lambda r: int(r.get("year", 0)))
            c2b_value = float(recent_fcf_sorted[-1]["fcf"])
            if len(recent_fcf_sorted) >= 3:
                c2c_value = c2b_value - (
                    float(recent_fcf_sorted[-2]["fcf"]) + float(recent_fcf_sorted[-3]["fcf"])
                ) / 2
    except Exception as exc:
        warnings.append(f"fcf_extra: {exc}")

    criteria.append(_criterion(
        "fcf_latest", "最近一年 FCF > 0", weight=1,
        passed=(c2b_value > 0) if c2b_value is not None else None,
        value=c2b_value,
        value_label=f"{c2b_value:,.0f}" if c2b_value is not None else "無資料",
        threshold="> 0",
        warning=None if c2b_value is not None else "FCF 資料不足",
    ))
    criteria.append(_criterion(
        "fcf_trend", "最近一年 FCF 高於前2年平均", weight=1,
        passed=(c2c_value > 0) if c2c_value is not None else None,
        value=c2c_value,
        value_label=f"{c2c_value:+,.0f}" if c2c_value is not None else "無資料",
        threshold="最近年 > 前2年平均",
        warning=None if c2c_value is not None else "FCF 年度資料不足（需 3 年）",
    ))

    # ── C3: 負債比 < 60%  [weight=2] ────────────────────────────────────────
    latest_debt_ratio: float | None = None
    try:
        if not assets_debt_s.empty and not liabilities_s.empty:
            q_dates = sorted(dt for dt in assets_debt_s.index if dt.month in {3, 6, 9, 12})
            if q_dates:
                dt = q_dates[-1]
                a_val = assets_debt_s.get(dt)
                l_val = liabilities_s.get(dt)
                if a_val and not pd.isna(a_val) and float(a_val) > 0 and l_val is not None and not pd.isna(l_val):
                    latest_debt_ratio = round(float(l_val) / float(a_val) * 100, 2)
    except Exception as exc:
        warnings.append(f"debt_calc: {exc}")

    c3_pass = latest_debt_ratio is not None and latest_debt_ratio < 60.0
    criteria.append(_criterion(
        "debt_ratio", "負債比 < 60%", weight=2,
        passed=c3_pass if latest_debt_ratio is not None else None,
        value=latest_debt_ratio,
        value_label=f"{latest_debt_ratio:.1f}%" if latest_debt_ratio is not None else "無資料",
        threshold="< 60%",
        warning=None if latest_debt_ratio is not None else "負債比資料不足",
        not_applicable="debt_ratio" in excluded_criteria,
    ))

    criteria.append(_criterion(
        "debt_ratio_strict", "負債比 < 50%", weight=1,
        passed=(latest_debt_ratio < 50.0) if latest_debt_ratio is not None else None,
        value=latest_debt_ratio,
        value_label=f"{latest_debt_ratio:.1f}%" if latest_debt_ratio is not None else "無資料",
        threshold="< 50%",
        warning=None if latest_debt_ratio is not None else "負債比資料不足",
        not_applicable="debt_ratio_strict" in excluded_criteria,
    ))
    equity_ratio = (100.0 - latest_debt_ratio) if latest_debt_ratio is not None else None
    criteria.append(_criterion(
        "equity_ratio", "股東權益比 > 40%", weight=1,
        passed=(equity_ratio > 40.0) if equity_ratio is not None else None,
        value=equity_ratio,
        value_label=f"{equity_ratio:.1f}%" if equity_ratio is not None else "無資料",
        threshold="> 40%",
        warning=None if equity_ratio is not None else "資產負債資料不足",
        not_applicable="equity_ratio" in excluded_criteria,
    ))

    # ── C4: 月營收 YoY 3個月中至少2個月 > 0%  [weight=1] ───────────────────
    rev_positive: int = 0
    rev_yoy_values: list[float] = []
    try:
        fetch_rev_start = date(today.year - 2, today.month, 1)
        df_rev = client.fetch_month_revenue(sid, fetch_rev_start, today)
        if not df_rev.empty and len(df_rev) >= 15:
            df_rev = df_rev.sort_values("month").reset_index(drop=True)
            df_rev["revenue"] = pd.to_numeric(df_rev["revenue"], errors="coerce")
            df_rev["yoy"] = (df_rev["revenue"] / df_rev["revenue"].shift(12) - 1) * 100
            recent = df_rev.dropna(subset=["yoy"]).tail(3)
            rev_yoy_values = [round(float(v), 1) for v in recent["yoy"] if not pd.isna(v)]
            rev_positive = sum(1 for v in rev_yoy_values if v > 0)
    except Exception as exc:
        warnings.append(f"revenue_yoy: {exc}")

    c4_pass = rev_positive >= 2
    if c4_pass:
        score += 1
    yoy_str = " / ".join(f"{v:+.1f}%" for v in rev_yoy_values) if rev_yoy_values else "無資料"
    criteria.append(_criterion(
        "revenue_yoy", "月營收年增率 3個月中至少2個月 > 0%", weight=1,
        passed=c4_pass if rev_yoy_values else None,
        value=rev_positive,
        value_label=yoy_str,
        threshold="3個月中 ≥ 2個月 > 0%",
        warning=None if rev_yoy_values else "營收資料不足（需 15 個月以上）",
    ))

    rev_avg = round(sum(rev_yoy_values) / len(rev_yoy_values), 1) if rev_yoy_values else None
    rev_latest = rev_yoy_values[-1] if rev_yoy_values else None
    criteria.append(_criterion(
        "revenue_yoy_avg", "月營收 YoY 近3個月平均 > 5%", weight=1,
        passed=(rev_avg > 5.0) if rev_avg is not None else None,
        value=rev_avg,
        value_label=f"{rev_avg:+.1f}%" if rev_avg is not None else "無資料",
        threshold="> 5%",
        warning=None if rev_avg is not None else "營收資料不足",
    ))
    criteria.append(_criterion(
        "revenue_yoy_latest", "月營收 YoY 最新值 > 0%", weight=1,
        passed=(rev_latest > 0) if rev_latest is not None else None,
        value=rev_latest,
        value_label=f"{rev_latest:+.1f}%" if rev_latest is not None else "無資料",
        threshold="> 0%",
        warning=None if rev_latest is not None else "營收資料不足",
    ))

    # ── C5: EPS YoY 3季中至少2季 > 0%  [weight=1] ───────────────────────────
    eps_positive: int = 0
    eps_yoy_values: list[float] = []
    df_eps: pd.DataFrame = pd.DataFrame()
    try:
        df_eps = client.fetch_eps_trend(sid, fetch_start_ext, today)
        if not df_eps.empty:
            recent_eps = df_eps.dropna(subset=["eps_yoy"]).tail(3)
            eps_yoy_values = [round(float(v), 1) for v in recent_eps["eps_yoy"] if not pd.isna(v)]
            eps_positive = sum(1 for v in eps_yoy_values if v > 0)
    except Exception as exc:
        warnings.append(f"eps_yoy: {exc}")

    c5_pass = eps_positive >= 2
    if c5_pass:
        score += 1
    eps_str = " / ".join(f"{v:+.1f}%" for v in eps_yoy_values) if eps_yoy_values else "無資料"
    criteria.append(_criterion(
        "eps_yoy", "EPS YoY 3季中至少2季正成長", weight=1,
        passed=c5_pass if eps_yoy_values else None,
        value=eps_positive,
        value_label=eps_str,
        threshold="3季中 ≥ 2季 YoY > 0%",
        warning=None if eps_yoy_values else "EPS 資料不足",
    ))

    eps_avg = round(sum(eps_yoy_values) / len(eps_yoy_values), 1) if eps_yoy_values else None
    eps_latest = eps_yoy_values[-1] if eps_yoy_values else None
    criteria.append(_criterion(
        "eps_yoy_avg", "EPS YoY 近3季平均 > 5%", weight=1,
        passed=(eps_avg > 5.0) if eps_avg is not None else None,
        value=eps_avg,
        value_label=f"{eps_avg:+.1f}%" if eps_avg is not None else "無資料",
        threshold="> 5%",
        warning=None if eps_avg is not None else "EPS 資料不足",
    ))
    criteria.append(_criterion(
        "eps_yoy_latest", "EPS YoY 最新值 > 0%", weight=1,
        passed=(eps_latest > 0) if eps_latest is not None else None,
        value=eps_latest,
        value_label=f"{eps_latest:+.1f}%" if eps_latest is not None else "無資料",
        threshold="> 0%",
        warning=None if eps_latest is not None else "EPS 資料不足",
    ))

    # ── C6: |Sloan Ratio| < 0.15  [weight=1] ────────────────────────────────
    sloan: float | None = None
    try:
        if not ni_q.empty and not assets_s.empty and fcf_rows:
            latest_year = today.year - 1
            annual_ni = sum(
                float(v) for dt, v in ni_q.items()
                if dt.year == latest_year and not pd.isna(v)
            )
            opcf_rows = [r for r in fcf_rows if r.get("year") == latest_year and r.get("operating_cf") is not None]
            if opcf_rows and annual_ni != 0:
                opcf = float(opcf_rows[0]["operating_cf"])
                asset_dates = sorted(dt for dt in assets_s.index if dt.year in {latest_year - 1, latest_year} and dt.month == 12)
                if len(asset_dates) >= 2:
                    avg_assets = (float(assets_s[asset_dates[0]]) + float(assets_s[asset_dates[-1]])) / 2
                elif asset_dates:
                    avg_assets = float(assets_s[asset_dates[-1]])
                else:
                    avg_assets = 0.0
                sloan = compute_sloan_ratio(annual_ni, opcf, avg_assets)
    except Exception as exc:
        warnings.append(f"sloan_ratio: {exc}")

    c6_pass = sloan is not None and abs(sloan) < 0.15
    if c6_pass:
        score += 1
    criteria.append(_criterion(
        "sloan_ratio", "|盈餘品質 Sloan Ratio| < 0.15", weight=1,
        passed=c6_pass if sloan is not None else None,
        value=round(sloan, 4) if sloan is not None else None,
        value_label=f"{sloan:.4f}" if sloan is not None else "無資料",
        threshold="< 0.15（絕對值）",
        warning=None if sloan is not None else "現金流或資產資料不足",
    ))

    # ── C7: 毛利率近4季均值 ≥ 前4季均值  [weight=1] ─────────────────────────
    gm_recent_avg: float | None = None
    gm_prior_avg: float | None = None
    op_recent_avg: float | None = None
    op_prior_avg: float | None = None
    nm_recent_avg: float | None = None
    nm_prior_avg: float | None = None
    # Pre-initialise so R8 trend-degradation block (uses df_margins.empty) and
    # R8b (uses liabilities_s/assets_debt_s) don't UnboundLocalError when their
    # respective fetch raises and the except path skips assignment.
    df_margins = pd.DataFrame()
    try:
        df_margins = client.fetch_margin_ratios(sid, fetch_start_ext, today)
        if not df_margins.empty:
            valid_gm = df_margins.dropna(subset=["gross_margin"]).sort_values("quarter")
            if len(valid_gm) >= 8:
                recent4 = valid_gm.tail(4)["gross_margin"].tolist()
                prior4 = valid_gm.iloc[-8:-4]["gross_margin"].tolist()
                gm_recent_avg = round(sum(recent4) / len(recent4), 2)
                gm_prior_avg = round(sum(prior4) / len(prior4), 2)

            valid_op = df_margins.dropna(subset=["operating_margin"]).sort_values("quarter")
            if len(valid_op) >= 8:
                op_recent_avg = round(float(valid_op.tail(4)["operating_margin"].mean()), 2)
                op_prior_avg = round(float(valid_op.iloc[-8:-4]["operating_margin"].mean()), 2)

            valid_nm = df_margins.dropna(subset=["net_margin"]).sort_values("quarter")
            if len(valid_nm) >= 8:
                nm_recent_avg = round(float(valid_nm.tail(4)["net_margin"].mean()), 2)
                nm_prior_avg = round(float(valid_nm.iloc[-8:-4]["net_margin"].mean()), 2)
    except Exception as exc:
        warnings.append(f"gross_margin: {exc}")

    c7_pass = gm_recent_avg is not None and gm_prior_avg is not None and gm_recent_avg >= gm_prior_avg
    if c7_pass:
        score += 1
    gm_label = (
        f"近4Q {gm_recent_avg:.1f}% vs 前4Q {gm_prior_avg:.1f}%"
        if gm_recent_avg is not None and gm_prior_avg is not None
        else "無資料（需 8 季以上）"
    )
    criteria.append(_criterion(
        "gross_margin", "毛利率近4季均值 ≥ 前4季均值", weight=1,
        passed=c7_pass if gm_recent_avg is not None else None,
        value=gm_recent_avg,
        value_label=gm_label,
        threshold="近4Q avg ≥ 前4Q avg",
        warning=None if gm_recent_avg is not None else "毛利率資料不足（需 8 季）",
    ))
    criteria.append(_criterion(
        "operating_margin", "營業利益率近4季均值 ≥ 前4季均值", weight=1,
        passed=(op_recent_avg >= op_prior_avg) if op_recent_avg is not None and op_prior_avg is not None else None,
        value=op_recent_avg,
        value_label=(f"近4Q {op_recent_avg:.1f}% vs 前4Q {op_prior_avg:.1f}%" if op_recent_avg is not None and op_prior_avg is not None else "無資料"),
        threshold="近4Q avg ≥ 前4Q avg",
        warning=None if op_recent_avg is not None and op_prior_avg is not None else "營業利益率資料不足（需 8 季）",
    ))
    criteria.append(_criterion(
        "net_margin", "淨利率近4季均值 ≥ 前4季均值", weight=1,
        passed=(nm_recent_avg >= nm_prior_avg) if nm_recent_avg is not None and nm_prior_avg is not None else None,
        value=nm_recent_avg,
        value_label=(f"近4Q {nm_recent_avg:.1f}% vs 前4Q {nm_prior_avg:.1f}%" if nm_recent_avg is not None and nm_prior_avg is not None else "無資料"),
        threshold="近4Q avg ≥ 前4Q avg",
        warning=None if nm_recent_avg is not None and nm_prior_avg is not None else "淨利率資料不足（需 8 季）",
    ))

    # ── C8: 法人近10日累計買超 > 0  [weight=1] ──────────────────────────────
    inst_10d_net: float | None = None
    inst_5d_net: float | None = None
    try:
        inst_start = date(today.year, today.month, 1) - pd.DateOffset(months=1)
        inst_start_date = date(int(inst_start.year), int(inst_start.month), int(inst_start.day))
        df_inst = client.fetch_institutional_investors_buy_sell(sid, inst_start_date, today)
        if not df_inst.empty:
            relevant = df_inst[df_inst["name"].isin(["Foreign_Investor", "Investment_Trust"])]
            if not relevant.empty:
                daily_net = relevant.groupby("date")["net"].sum().sort_index()
                inst_10d_net = float(daily_net.tail(10).sum())
                inst_5d_net = float(daily_net.tail(5).sum())
    except Exception as exc:
        warnings.append(f"inst_buy: {exc}")

    c8_pass = inst_10d_net is not None and inst_10d_net > 0
    if c8_pass:
        score += 1
    criteria.append(_criterion(
        "inst_buy", "法人近10日累計買超 > 0", weight=1,
        passed=c8_pass if inst_10d_net is not None else None,
        value=inst_10d_net,
        value_label=f"{inst_10d_net:+,.0f} 張" if inst_10d_net is not None else "無資料",
        threshold="外資+投信 10日合計 > 0",
        warning=None if inst_10d_net is not None else "法人資料無法取得",
    ))
    criteria.append(_criterion(
        "inst_buy_5d", "法人近5日累計買超 > 0", weight=1,
        passed=(inst_5d_net > 0) if inst_5d_net is not None else None,
        value=inst_5d_net,
        value_label=f"{inst_5d_net:+,.0f} 張" if inst_5d_net is not None else "無資料",
        threshold="外資+投信 5日合計 > 0",
        warning=None if inst_5d_net is not None else "法人資料無法取得",
    ))

    # ── C9: 目前P/E < 歷史中位數  [weight=1] ────────────────────────────────
    current_per: float | None = None
    per_median: float | None = None
    per_p25: float | None = None
    try:
        per_start = date(today.year - 5, today.month, 1)
        df_per = client.fetch_stock_per(sid, per_start, today)
        if not df_per.empty and "PER" in df_per.columns:
            valid_per = df_per[df_per["PER"].notna() & (df_per["PER"] > 0)].sort_values("date")
            if not valid_per.empty:
                current_per = float(valid_per.iloc[-1]["PER"])
                per_median = float(valid_per["PER"].median())
                per_p25 = float(valid_per["PER"].quantile(0.25))
    except Exception as exc:
        warnings.append(f"pe_median: {exc}")

    c9_pass = current_per is not None and per_median is not None and current_per < per_median
    if c9_pass:
        score += 1
    per_label = (
        f"現值 {current_per:.1f}x vs 中位數 {per_median:.1f}x"
        if current_per is not None and per_median is not None
        else "無資料"
    )
    criteria.append(_criterion(
        "pe_median", "目前P/E < 歷史中位數（近5年）", weight=1,
        passed=c9_pass if current_per is not None else None,
        value=current_per,
        value_label=per_label,
        threshold="< p50 歷史中位數",
        warning=None if current_per is not None else "P/E 資料無法取得",
    ))
    criteria.append(_criterion(
        "pe_p25", "目前P/E < 歷史 25 分位數（近5年）", weight=1,
        passed=(current_per < per_p25) if current_per is not None and per_p25 is not None else None,
        value=current_per,
        value_label=(f"現值 {current_per:.1f}x vs p25 {per_p25:.1f}x" if current_per is not None and per_p25 is not None else "無資料"),
        threshold="< p25",
        warning=None if current_per is not None and per_p25 is not None else "P/E 資料無法取得",
    ))

    # ── C10: 外資持股3個月整體淨增  [weight=1] ───────────────────────────────
    foreign_trend: list[float] = []
    df_sh: pd.DataFrame = pd.DataFrame()
    try:
        if GoodinfoClient is not None:
            gc = GoodinfoClient()
            df_sh = gc.fetch_shareholding_history(sid)
            if not df_sh.empty and "foreign_ratio" in df_sh.columns:
                df_sh = df_sh.copy()
                df_sh["date"] = pd.to_datetime(df_sh["date"], errors="coerce")
                df_sh = df_sh.dropna(subset=["date", "foreign_ratio"]).sort_values("date")
                df_sh["foreign_ratio"] = pd.to_numeric(df_sh["foreign_ratio"], errors="coerce")
                df_sh = df_sh.dropna(subset=["foreign_ratio"])
                recent3 = df_sh.tail(3)
                foreign_trend = [round(float(v), 2) for v in recent3["foreign_ratio"]]
        else:
            warnings.append("foreign_holding: Goodinfo 模組無法載入")
    except Exception as exc:
        warnings.append(f"foreign_holding: {exc}")

    # Pass when net direction over 3 months is upward (last > first)
    c10_pass = len(foreign_trend) >= 2 and foreign_trend[-1] > foreign_trend[0]
    if c10_pass:
        score += 1
    fh_label = " → ".join(f"{v:.1f}%" for v in foreign_trend) if foreign_trend else "無資料"
    criteria.append(_criterion(
        "foreign_holding", "外資持股3個月整體淨增", weight=1,
        passed=c10_pass if foreign_trend else None,
        value=foreign_trend[-1] if foreign_trend else None,
        value_label=fh_label,
        threshold="近3個月整體上升（最新 > 最早）",
        warning=None if foreign_trend else "外資持股資料無法取得",
    ))

    # ── v4 scoring: not_applicable criteria excluded from pass_rate ───────────
    passed_count = sum(1 for c in criteria if c.get("pass") is True)
    eligible_count = sum(1 for c in criteria if c.get("pass") is not None)
    score = passed_count
    max_score = len(criteria)
    pass_rate = round((passed_count / eligible_count) * 100, 1) if eligible_count > 0 else 0.0

    if pass_rate >= 75.0:
        recommendation, recommendation_label = "recommended_buy", "建議買進"
        signal, signal_label = "strong_buy", "建議買進"
    elif pass_rate >= 60.0:
        recommendation, recommendation_label = "scale_in", "可分批買進"
        signal, signal_label = "buy", "可分批買進"
    elif pass_rate >= 50.0:
        recommendation, recommendation_label = "watchlist", "觀察清單"
        signal, signal_label = "watch", "觀察清單"
    else:
        recommendation, recommendation_label = "not_recommended", "不建議買進"
        signal, signal_label = "neutral", "不建議買進"

    # ============== NEW: Data Fetching for Big Holders & Inventory ==============
    spread_df = pd.DataFrame()
    inv_data: dict[str, float] | None = None
    try:
        spread_df = client.fetch_shareholding_spread(sid, fetch_start_ext, today)
    except Exception as exc:
        _exc_msg = str(exc).lower()
        if "level is register" not in _exc_msg and "sponsor" not in _exc_msg and "user level" not in _exc_msg:
            warnings.append(f"shareholding_spread: {exc}")
    try:
        inv_data = client.fetch_inventory_and_revenue_growth(sid, fetch_start_ext, today)
    except Exception as exc:
        warnings.append(f"inventory_growth: {exc}")

    # Fetch BVPS for R5
    liq_df_risk: pd.DataFrame = pd.DataFrame()
    try:
        liq_df_risk = client.fetch_liquidity_ratios(sid, fetch_start_ext, today)
    except Exception as exc:
        warnings.append(f"liq_ratios_risk: {exc}")

    # Fetch IS data for R6 (interest coverage)
    is_df_risk: pd.DataFrame = pd.DataFrame()
    try:
        is_df_risk = client.fetch_financial_statements(sid, fetch_start_ext, today)
    except Exception as exc:
        warnings.append(f"is_data_risk: {exc}")
    
    # ============== NEW: Risk Avoidance (排雷指標) ==============
    risk_criteria = []
    
    # [NEW] 籌碼排雷：散戶增、大戶減 (千張大戶持股流向)
    if not spread_df.empty:
        try:
            latest_date = spread_df["date"].max()
            past_date = spread_df["date"].drop_duplicates().sort_values().iloc[-4] if len(spread_df["date"].unique()) >= 4 else spread_df["date"].min()
            
            # >= Level 15 logic (Whales)
            whales = spread_df[spread_df["HoldingSharesLevel"] == "15"]
            wh_latest = whales[whales["date"] == latest_date]["percent"].sum() if not whales.empty else 0
            wh_past = whales[whales["date"] == past_date]["percent"].sum() if not whales.empty else 0
            
            # <= Level 9 logic (Retail)
            retail = spread_df[spread_df["HoldingSharesLevel"].astype(int) <= 9]
            ret_latest = retail[retail["date"] == latest_date]["percent"].sum() if not retail.empty else 0
            ret_past = retail[retail["date"] == past_date]["percent"].sum() if not retail.empty else 0
            
            # If Whales dropped by 2% AND Retail increased
            if (wh_past - wh_latest > 2.0) and (ret_latest > ret_past):
                risk_criteria.append({
                    "category": "籌碼排雷",
                    "name": "千張大戶退場",
                    "status": "warning",
                    "value_label": f"大戶 -{(wh_past - wh_latest):.1f}%",
                    "description": "大戶跑給散戶接，籌碼明顯凌亂"
                })
        except Exception:
            pass

    # [NEW] 存貨異常排雷：存貨成長率大於營收成長率 20% 以上
    if inv_data is not None:
        try:
            inv_y = inv_data.get("inv_yoy", 0)
            rev_y = inv_data.get("rev_yoy", 0)
            if inv_y > 20 and (inv_y - rev_y > 20):
                risk_criteria.append({
                    "category": "存貨排雷",
                    "name": "存貨飆升去化慢",
                    "status": "warning",
                    "value_label": f"存貨 YoY {inv_y:.1f}%",
                    "description": f"存貨成長遠大於營收({rev_y:.1f}%)，恐面臨跌價損失風險"
                })
        except Exception:
            pass

    # 1. 現金流與淨利背離 (OCF/NI)
    if c2b_value is not None and c2b_value < 0:
        risk_criteria.append({
            "category": "獲利排雷",
            "name": "自由現金流轉負",
            "status": "warning",
            "value_label": f"FCF = {c2b_value:.2f}",
            "description": "帳面有獲利但無現金流入，需注意應收帳款與存貨壓力"
        })

    # 2. 估值過高 (PE > P90)
    if per_median is not None and current_per is not None:
        try:
            # Simplistic check if current is >> avg
            if current_per > (per_median * 1.5):
                risk_criteria.append({
                    "category": "估值排雷",
                    "name": "本益比過高",
                    "status": "warning",
                    "value_label": f"PE {current_per:.1f} > Avg*1.5",
                    "description": "當前估值偏離歷史均值過大，應避免追高"
                })
        except Exception:
            pass

    # 3. 盈餘品質 (Sloan Ratio / 業外收支)
    # We already have C7 for Sloan Ratio, just add an extreme warning if it's very bad
    if sloan is not None and sloan > 0.25:
        risk_criteria.append({
            "category": "品質排雷",
            "name": "盈餘品質極度惡化",
            "status": "warning",
            "value_label": f"Sloan Ratio {sloan:.2f}",
            "description": "淨收益大幅來自非現金項目，虛盈實虧風險高"
        })

    # 4. 配息陷阱 (Payout Ratio > 100%)
    payout_ratio_pct: float | None = None
    try:
        payout_ratio_raw = client.fetch_dividend_payout_ratio(
            sid, fetch_start_ext, today, years=5
        )
        if payout_ratio_raw is not None:
            payout_ratio_pct = float(payout_ratio_raw)
            if payout_ratio_pct <= 1.0:
                payout_ratio_pct *= 100.0
    except Exception as exc:
        warnings.append(f"payout_ratio: {exc}")

    if payout_ratio_pct is not None and payout_ratio_pct > 100:
        risk_criteria.append({
            "category": "配息排雷",
            "name": "配發率超標",
            "status": "warning",
            "value_label": f"Payout {payout_ratio_pct:.1f}%",
            "description": "發放股利超過當期獲利，可能在消耗老本"
        })

    # R4: 連續虧損 — 近4季中有2季以上 EPS < 0
    if not df_eps.empty:
        try:
            recent4 = df_eps.dropna(subset=["eps"]).sort_values("quarter").tail(4)
            negative_q = sum(1 for _, row in recent4.iterrows() if row["eps"] is not None and float(row["eps"]) < 0)
            if negative_q >= 2:
                risk_criteria.append({
                    "category": "財務惡化",
                    "name": "連續虧損",
                    "status": "warning",
                    "value_label": f"近4季 {negative_q} 季虧損",
                    "description": "近4季中2季以上EPS為負，獲利能力存疑"
                })
        except Exception:
            pass

    # R5: 淨值低於票面 — 最新季 BVPS < 10
    if not liq_df_risk.empty:
        try:
            valid_bvps = liq_df_risk[liq_df_risk["bvps"].notna()].sort_values("quarter")
            if not valid_bvps.empty:
                latest_bvps_r = float(valid_bvps.iloc[-1]["bvps"])
                if latest_bvps_r < 10.0:
                    risk_criteria.append({
                        "category": "財務惡化",
                        "name": "淨值低於票面",
                        "status": "warning",
                        "value_label": f"BVPS {latest_bvps_r:.2f} < 10",
                        "description": "每股淨值低於票面10元，資本侵蝕風險高"
                    })
        except Exception:
            pass

    # R6: 利息保障倍數不足 — 營業利益/利息費用 < 2
    if not is_df_risk.empty:
        try:
            def _get_latest_annual(df: pd.DataFrame, type_names: list[str]) -> float | None:
                for t in type_names:
                    sub = df[df["type"] == t].copy()
                    if sub.empty:
                        continue
                    sub["date"] = pd.to_datetime(sub["date"])
                    sub["year"] = sub["date"].dt.year
                    latest_year = sub["year"].max()
                    year_data = sub[sub["year"] == latest_year]
                    # Taiwan IS is cumulative YTD; use the last (Dec/Q4) entry as full year
                    q4 = year_data[year_data["date"].dt.month == 12]
                    if not q4.empty:
                        return float(q4.sort_values("date").iloc[-1]["value"])
                    # YTD-cumulative: latest available quarter already contains the running total
                    return float(year_data.sort_values("date").iloc[-1]["value"])
                return None

            op_inc = _get_latest_annual(is_df_risk, ["OperatingIncome", "ProfitFromOperations", "OperatingProfitLoss"])
            int_exp = _get_latest_annual(is_df_risk, ["FinanceCosts", "InterestExpenses", "InterestExpense", "FinancingCosts"])
            if op_inc is not None and int_exp is not None and abs(int_exp) > 0:
                coverage = op_inc / abs(int_exp)
                if coverage < 2.0:
                    risk_criteria.append({
                        "category": "財務惡化",
                        "name": "利息保障倍數不足",
                        "status": "warning",
                        "value_label": f"ICR {coverage:.1f}x",
                        "description": "營業利益不足支應利息費用兩倍，財務壓力大"
                    })
        except Exception:
            pass

    # R7: 董監質押比過高 — 全體董監質押/持股 > 50%
    if not df_sh.empty and "total_dir_pledged" in df_sh.columns and "total_dir_shares" in df_sh.columns:
        try:
            df_sh_sorted = df_sh.dropna(subset=["total_dir_pledged", "total_dir_shares"]).sort_values("date")
            if not df_sh_sorted.empty:
                latest_row = df_sh_sorted.iloc[-1]
                pledged = float(latest_row["total_dir_pledged"])
                total = float(latest_row["total_dir_shares"])
                if total > 0:
                    pledge_ratio = pledged / total * 100
                    if pledge_ratio > 50.0:
                        risk_criteria.append({
                            "category": "籌碼治理",
                            "name": "董監質押比過高",
                            "status": "warning",
                            "value_label": f"質押比 {pledge_ratio:.1f}%",
                            "description": "全體董監事質押超過自身持股半數，股價下跌恐觸發強制賣出"
                        })
        except Exception:
            pass

    # R8: 趨勢惡化 — 毛利率、ROE 或 負債比連續3季惡化
    if not df_margins.empty:
        try:
            for metric_col, metric_name, direction in [
                ("gross_margin", "毛利率", "down"),
                ("operating_margin", "營業利益率", "down"),
            ]:
                valid_m = df_margins.dropna(subset=[metric_col]).sort_values("quarter")
                if len(valid_m) >= 3:
                    last3 = valid_m.tail(3)[metric_col].tolist()
                    if direction == "down" and last3[0] > last3[1] > last3[2]:
                        risk_criteria.append({
                            "category": "財務惡化",
                            "name": f"{metric_name}趨勢惡化",
                            "status": "warning",
                            "value_label": f"連3季下滑至 {last3[-1]:.1f}%",
                            "description": f"{metric_name}連續3季單向下滑，盈利能力持續走弱"
                        })
        except Exception:
            pass

    # R8b: 負債比連續3季上升
    if not liabilities_s.empty and not assets_debt_s.empty:
        try:
            q_dates = sorted(dt for dt in assets_debt_s.index if dt.month in {3, 6, 9, 12})[-5:]
            debt_ratios = []
            for dt in q_dates:
                a_val = assets_debt_s.get(dt)
                l_val = liabilities_s.get(dt)
                if a_val and not pd.isna(a_val) and float(a_val) > 0 and l_val is not None and not pd.isna(l_val):
                    debt_ratios.append(round(float(l_val) / float(a_val) * 100, 2))
            if len(debt_ratios) >= 3 and debt_ratios[-3] < debt_ratios[-2] < debt_ratios[-1]:
                risk_criteria.append({
                    "category": "財務惡化",
                    "name": "負債比趨勢惡化",
                    "status": "warning",
                    "value_label": f"連3季攀升至 {debt_ratios[-1]:.1f}%",
                    "description": "負債比連續3季上升，財務槓桿持續擴大"
                })
        except Exception:
            pass

    risk_score = len(risk_criteria)
    if risk_score >= 2:
        recommendation, recommendation_label = "not_recommended", "高風險避開"
        signal, signal_label = "neutral", "高風險避開"

    payload: dict[str, Any] = {
        "stock_id": sid,
        "industry": industry,
        "score": score,
        "max_score": max_score,
        "eligible_count": eligible_count,
        "pass_rate": pass_rate,
        "recommendation": recommendation,
        "recommendation_label": recommendation_label,
        "signal": signal,
        "signal_label": signal_label,
        "criteria": criteria,
        "warnings": warnings,
        "risk_criteria": risk_criteria,
        "risk_score": risk_score,
    }
    # Only cache results where data was actually fetched. If all/most fetches
    # failed (quota exceeded, network error, etc.) the criteria will be mostly
    # null. Caching that failure traps the user all day — the next query would
    # silently return yesterday's stale 402 errors without retrying the API.
    # Threshold: at least one criterion must have a non-null pass value.
    has_real_data = any(c.get("pass") is not None for c in criteria)
    if has_real_data:
        cache.set(cache_key, {"ts": time.time(), **payload})
    return payload

