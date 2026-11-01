from __future__ import annotations

import time
from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import api
from cache import CacheStore
from datasource_finmind import FinMindClient


def test_health_ok() -> None:
    client = TestClient(api.app)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_latest_and_revenue_mocked(monkeypatch, tmp_path) -> None:
    api.cache = CacheStore(base_dir=tmp_path)
    monkeypatch.setenv("FINMIND_API_KEY", "test_token_dummy")

    class FixedDate(date):
        @classmethod
        def today(cls) -> date:  # type: ignore[override]
            return cls(2026, 3, 17)

    monkeypatch.setattr(api, "date", FixedDate)

    def fake_fetch_stock_price(self: FinMindClient, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "date": "2026-01-31",
                    "open": 90.0,
                    "max": 101.0,
                    "min": 88.0,
                    "close": 100.0,
                    "spread": 1.0,
                    "Trading_Volume": 12345,
                    "Trading_money": 999999,
                    "Trading_turnover": 321,
                },
                {
                    "date": "2026-02-28",
                    "open": 100.0,
                    "max": 115.0,
                    "min": 99.0,
                    "close": 110.0,
                    "spread": 2.0,
                    "Trading_Volume": 23456,
                    "Trading_money": 1111111,
                    "Trading_turnover": 654,
                },
                {
                    "date": "2026-03-17",
                    "open": 100.0,
                    "max": 110.0,
                    "min": 95.0,
                    "close": 105.0,
                    "spread": 2.0,
                    "Trading_Volume": 34567,
                    "Trading_money": 2222222,
                    "Trading_turnover": 987,
                },
            ]
        )

    def fake_fetch_inst(self: FinMindClient, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"date": "2026-03-17", "name": "Foreign_Investor", "buy": 10, "sell": 7, "net": 3},
                {"date": "2026-03-17", "name": "Investment_Trust", "buy": 5, "sell": 9, "net": -4},
            ]
        )

    def fake_fetch_month_revenue(self: FinMindClient, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"month": pd.Timestamp("2026-01-01"), "revenue": 100.0},
                {"month": pd.Timestamp("2026-02-01"), "revenue": 110.0},
                {"month": pd.Timestamp("2026-03-01"), "revenue": 120.0},
            ]
        )

    def fake_fetch_stock_per(self: FinMindClient, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"date": "2026-01-31", "dividend_yield": 2.1, "PER": 10.0, "PBR": 1.0},
                {"date": "2026-02-28", "dividend_yield": 2.2, "PER": 11.0, "PBR": 1.1},
                {"date": "2026-03-17", "dividend_yield": 2.3, "PER": 12.0, "PBR": 1.2},
            ]
        )

    def fake_fetch_stock_name(self: FinMindClient, stock_id: str, timeout: float = 20.0) -> str | None:
        return "台積電" if stock_id == "2330" else None

    def fake_fetch_stock_dividend(self: FinMindClient, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"year": 2026, "ex_dividend_date": "2026-03-10", "cash_dividend": 20.0},
            ]
        )

    monkeypatch.setattr(FinMindClient, "fetch_stock_price", fake_fetch_stock_price)
    monkeypatch.setattr(FinMindClient, "fetch_institutional_investors_buy_sell", fake_fetch_inst)
    monkeypatch.setattr(FinMindClient, "fetch_month_revenue", fake_fetch_month_revenue)
    monkeypatch.setattr(FinMindClient, "fetch_stock_per", fake_fetch_stock_per)
    monkeypatch.setattr(FinMindClient, "fetch_stock_name", fake_fetch_stock_name)
    monkeypatch.setattr(FinMindClient, "fetch_stock_dividend", fake_fetch_stock_dividend)

    client = TestClient(api.app)

    r1 = client.get("/api/stocks/2330/latest")
    assert r1.status_code == 200
    payload = r1.json()
    assert payload["stock_id"] == "2330"
    assert payload["stock_name"] == "台積電"
    assert payload["price"]["date"] == "2026-03-17"
    assert len(payload["institutional"]) == 2
    assert payload["institutional_as_of"] == "2026-03-17"
    assert payload["institutional_status"] == "fresh"
    assert payload["institutional_lag_days"] == 0

    r2 = client.get("/api/stocks/2330/revenue?years=3")
    assert r2.status_code == 200
    payload2 = r2.json()
    assert payload2["stock_id"] == "2330"
    assert isinstance(payload2["rows"], list)
    assert any(row.get("month") == "2026-03-01" for row in payload2["rows"])

    r3 = client.get("/api/stocks/2330/price_history?years=3")
    assert r3.status_code == 200
    payload3 = r3.json()
    assert payload3["stock_id"] == "2330"
    assert any((row.get("month") == "2026-03-01" and row.get("close") == 105.0) for row in payload3["rows"])

    r4 = client.get("/api/stocks/2330/dividend_yield?years=3")
    assert r4.status_code == 200
    payload4 = r4.json()
    assert payload4["stock_id"] == "2330"
    assert any((row.get("month") == "2026-03-01" and row.get("dividend_yield") == 2.3) for row in payload4["rows"])

    r5 = client.get("/api/stocks/2330/dividends_cash?years=3")
    assert r5.status_code == 200
    payload5 = r5.json()
    assert payload5["stock_id"] == "2330"
    assert any((row.get("month") == "2026-03-01" and row.get("cash_dividend") == 20.0) for row in payload5["rows"])

    r6 = client.get("/api/stocks/2330/volume_turnover_recent")
    assert r6.status_code == 200
    payload6 = r6.json()
    assert payload6["stock_id"] == "2330"
    assert isinstance(payload6["rows"], list)
    assert any(
        (row.get("date") == "2026-03-17" and row.get("volume") == 34567 and row.get("turnover") == 987)
        for row in payload6["rows"]
    )


def _install_minimal_latest_mocks(monkeypatch, *, latest_price_date: str = "2026-03-17") -> None:
    """Minimal mocks so /api/stocks/{id}/latest can run for institutional tests."""

    def fake_fetch_stock_price(self: FinMindClient, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "date": latest_price_date,
                    "open": 100.0,
                    "max": 110.0,
                    "min": 95.0,
                    "close": 105.0,
                    "spread": 2.0,
                    "Trading_Volume": 34567,
                    "Trading_money": 2222222,
                    "Trading_turnover": 987,
                }
            ]
        )

    def fake_fetch_stock_name(self: FinMindClient, stock_id: str, timeout: float = 20.0) -> str | None:
        return None

    monkeypatch.setattr(FinMindClient, "fetch_stock_price", fake_fetch_stock_price)
    monkeypatch.setattr(FinMindClient, "fetch_stock_name", fake_fetch_stock_name)


def test_institutional_fresh_on_latest_day(monkeypatch, tmp_path) -> None:
    """T86 已釋出當日資料 — 應為 fresh、lag = 0、回傳該日 rows。"""

    api.cache = CacheStore(base_dir=tmp_path)
    monkeypatch.setenv("FINMIND_API_KEY", "tok")

    class FixedDate(date):
        @classmethod
        def today(cls) -> date:  # type: ignore[override]
            return cls(2026, 3, 17)

    monkeypatch.setattr(api, "date", FixedDate)
    _install_minimal_latest_mocks(monkeypatch)

    received_window: dict[str, date] = {}

    def fake_fetch_inst(self: FinMindClient, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0) -> pd.DataFrame:
        received_window["start"] = start_date
        received_window["end"] = end_date
        return pd.DataFrame(
            [
                {"date": pd.Timestamp("2026-03-13"), "name": "Foreign_Investor", "buy": 1, "sell": 0, "net": 1},
                {"date": pd.Timestamp("2026-03-17"), "name": "Foreign_Investor", "buy": 10, "sell": 7, "net": 3},
                {"date": pd.Timestamp("2026-03-17"), "name": "Investment_Trust", "buy": 5, "sell": 9, "net": -4},
            ]
        )

    monkeypatch.setattr(FinMindClient, "fetch_institutional_investors_buy_sell", fake_fetch_inst)

    r = TestClient(api.app).get("/api/stocks/2330/latest")
    assert r.status_code == 200
    payload = r.json()

    # window: 5+ trading days back from latest_date
    assert received_window["end"] == date(2026, 3, 17)
    assert (received_window["end"] - received_window["start"]).days >= 7

    assert payload["institutional_status"] == "fresh"
    assert payload["institutional_as_of"] == "2026-03-17"
    assert payload["institutional_lag_days"] == 0
    cats = {row["category"] for row in payload["institutional"]}
    assert cats == {"外資", "投信"}  # only 2026-03-17 rows


def test_institutional_stale_falls_back_to_previous_day(monkeypatch, tmp_path) -> None:
    """T86 當日尚未公布，但前一交易日有資料 — 應為 stale、回傳前一日資料。"""

    api.cache = CacheStore(base_dir=tmp_path)
    monkeypatch.setenv("FINMIND_API_KEY", "tok")

    class FixedDate(date):
        @classmethod
        def today(cls) -> date:  # type: ignore[override]
            return cls(2026, 3, 17)

    monkeypatch.setattr(api, "date", FixedDate)
    _install_minimal_latest_mocks(monkeypatch)

    def fake_fetch_inst(self: FinMindClient, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"date": pd.Timestamp("2026-03-13"), "name": "Foreign_Investor", "buy": 2, "sell": 1, "net": 1},
                {"date": pd.Timestamp("2026-03-16"), "name": "Foreign_Investor", "buy": 8, "sell": 5, "net": 3},
            ]
        )

    monkeypatch.setattr(FinMindClient, "fetch_institutional_investors_buy_sell", fake_fetch_inst)

    r = TestClient(api.app).get("/api/stocks/2330/latest")
    assert r.status_code == 200
    payload = r.json()

    assert payload["institutional_status"] == "stale"
    assert payload["institutional_as_of"] == "2026-03-16"
    assert payload["institutional_lag_days"] == 1
    assert len(payload["institutional"]) == 1
    assert payload["institutional"][0]["category"] == "外資"


def test_institutional_unavailable_no_data_in_window(monkeypatch, tmp_path) -> None:
    """5 個交易日內皆無資料 — 應為 unavailable，且不快取（下一次仍重新呼叫）。"""

    api.cache = CacheStore(base_dir=tmp_path)
    monkeypatch.setenv("FINMIND_API_KEY", "tok")

    class FixedDate(date):
        @classmethod
        def today(cls) -> date:  # type: ignore[override]
            return cls(2026, 3, 17)

    monkeypatch.setattr(api, "date", FixedDate)
    _install_minimal_latest_mocks(monkeypatch)

    call_count = {"n": 0}

    def fake_fetch_inst(self: FinMindClient, stock_id: str, start_date: date, end_date: date, timeout: float = 30.0) -> pd.DataFrame:
        call_count["n"] += 1
        return pd.DataFrame(columns=["date", "name", "buy", "sell", "net"])

    monkeypatch.setattr(FinMindClient, "fetch_institutional_investors_buy_sell", fake_fetch_inst)

    tc = TestClient(api.app)
    r1 = tc.get("/api/stocks/2330/latest")
    assert r1.status_code == 200
    payload1 = r1.json()
    assert payload1["institutional"] == []
    assert payload1["institutional_status"] == "unavailable"
    assert payload1["institutional_as_of"] is None
    assert payload1["institutional_lag_days"] == 0

    # second call must re-fetch (unavailable is never cached) so the user can
    # retry after T86 publishes later in the day
    r2 = tc.get("/api/stocks/2330/latest")
    assert r2.status_code == 200
    assert call_count["n"] == 2


def test_token_usage_returns_remaining(monkeypatch) -> None:
    def fake_fetch_token_usage_from_finmind(token: str, timeout: float = 10.0) -> dict[str, int]:
        assert token == "test_token"
        return {
            "user_count": 158,
            "api_request_limit": 600,
            "remaining": 442,
        }

    monkeypatch.setattr(api, "_fetch_token_usage_from_finmind", fake_fetch_token_usage_from_finmind)

    client = TestClient(api.app)
    r = client.get("/api/token_usage?token=test_token")
    assert r.status_code == 200
    payload = r.json()
    assert payload["user_count"] == 158
    assert payload["api_request_limit"] == 600
    assert payload["remaining"] == 442


def _quick_summary_parts(monkeypatch) -> None:
    monkeypatch.setattr(api, "latest", lambda sid, **kw: {"price": {"close": 120.0}})
    monkeypatch.setattr(api, "dcf_valuation", lambda sid, **kw: {"entry_price": 90.0})
    monkeypatch.setattr(api, "valuation_extra", lambda sid, **kw: {"graham_number": 100.0, "eps_cagr": 20.0})
    monkeypatch.setattr(api, "forward_rolling_eps", lambda sid, **kw: {"price_mid": 150.0, "price_mid_m2": 170.0})
    monkeypatch.setattr(api, "pe_river", lambda sid, **kw: {
        "bands": {"p25": 22.0, "p75": 35.0},
        "eps_ttm_rows": [{"quarter": "2026-03-31", "eps_ttm": 5.0}],
    })


def test_quick_summary_combines_components(monkeypatch) -> None:
    _quick_summary_parts(monkeypatch)
    r = TestClient(api.app).get("/api/stocks/2330/quick_summary?token=test")
    assert r.status_code == 200
    payload = r.json()
    assert payload["stock_id"] == "2330"
    assert payload["floor"] == 100.0
    assert payload["ceiling"] == 160.0
    assert payload["position"] == 0.3333
    assert {c["source"] for c in payload["floor_components"]} == {"dcf", "valuation_extra", "pe_river"}
    assert payload["warnings"] == []


def test_quick_summary_passes_explicit_parameters(monkeypatch) -> None:
    """Direct calls bypass FastAPI, so defaults must never reach endpoints as Query objects."""
    _quick_summary_parts(monkeypatch)
    seen: dict[str, dict] = {}

    def capture(name):
        def fake(sid, **kw):
            seen[name] = kw
            return {}
        return fake

    monkeypatch.setattr(api, "dcf_valuation", capture("dcf"))
    monkeypatch.setattr(api, "pe_river", capture("pe_river"))
    r = TestClient(api.app).get("/api/stocks/2330/quick_summary?token=test&years=3&growth_rate=0.05")
    assert r.status_code == 200
    assert seen["dcf"]["years"] == 3
    assert seen["dcf"]["growth_rate"] == 0.05
    assert seen["dcf"]["margin_of_safety"] == 0.30
    assert seen["dcf"]["x_finmind_token"] is None
    assert seen["pe_river"]["years"] == 3


def test_quick_summary_survives_a_failing_component(monkeypatch) -> None:
    _quick_summary_parts(monkeypatch)

    def bad_dcf(sid, **kw):
        raise api.HTTPException(status_code=502, detail="FinMind quota exceeded (HTTP 402).")

    monkeypatch.setattr(api, "dcf_valuation", bad_dcf)
    r = TestClient(api.app).get("/api/stocks/2330/quick_summary?token=test")
    assert r.status_code == 200
    payload = r.json()
    assert payload["warnings"] == ["dcf: FinMind quota exceeded (HTTP 402)."]
    assert [c["source"] for c in payload["floor_components"]] == ["valuation_extra", "pe_river"]


def test_quick_summary_does_not_leak_token_from_exceptions(monkeypatch) -> None:
    _quick_summary_parts(monkeypatch)

    def boom(sid, **kw):
        raise RuntimeError("GET https://api.finmindtrade.com/api/v4/data?token=SECRET failed")

    monkeypatch.setattr(api, "pe_river", boom)
    r = TestClient(api.app).get("/api/stocks/2330/quick_summary?token=test")
    assert r.status_code == 200
    assert r.json()["warnings"] == ["pe_river: RuntimeError"]


def test_quick_summary_requires_token(monkeypatch) -> None:
    monkeypatch.delenv("FINMIND_API_KEY", raising=False)
    assert TestClient(api.app).get("/api/stocks/2330/quick_summary").status_code == 401


def test_api_responses_report_elapsed_ms() -> None:
    r = TestClient(api.app).get("/api/health")
    assert float(r.headers["X-Elapsed-Ms"]) >= 0
    assert isinstance(r.json()["finmind_calls_since_start"], int)


def _twse_price(close: float = 2370.0) -> pd.DataFrame:
    return pd.DataFrame([{
        "date": "2026-03-17", "open": 2380.0, "max": 2380.0, "min": 2360.0, "close": close,
        "spread": -45.0, "Trading_Volume": 36615931.0, "Trading_money": 86833096690.0,
        "Trading_turnover": 331330.0,
    }])


def _no_finmind_institutional(self, stock_id, start_date, end_date, timeout=30.0) -> pd.DataFrame:
    return pd.DataFrame()


def test_latest_prefers_twse(monkeypatch, tmp_path) -> None:
    api.cache = CacheStore(base_dir=tmp_path)

    def must_not_call(self, *a, **kw):
        raise AssertionError("FinMind price must not be fetched when TWSE has data")

    monkeypatch.setattr(api.twse_open, "fetch_stock_day_all", lambda sid: _twse_price())
    monkeypatch.setattr(FinMindClient, "fetch_stock_price", must_not_call)
    monkeypatch.setattr(FinMindClient, "fetch_stock_name", lambda self, sid: "台積電")
    monkeypatch.setattr(FinMindClient, "fetch_institutional_investors_buy_sell", _no_finmind_institutional)

    r = TestClient(api.app).get("/api/stocks/2330/latest?token=test")
    assert r.status_code == 200
    payload = r.json()
    assert payload["price"]["close"] == 2370.0
    assert payload["price"]["high"] == 2380.0
    assert payload["price"]["volume"] == 36615931
    assert payload["price_source"] == "twse"


def test_latest_falls_back_to_finmind(monkeypatch, tmp_path) -> None:
    api.cache = CacheStore(base_dir=tmp_path)
    monkeypatch.setattr(FinMindClient, "fetch_stock_price",
                        lambda self, stock_id, start_date, end_date, timeout=30.0: pd.DataFrame([{
                            "date": "2026-03-17", "open": 100.0, "max": 110.0, "min": 95.0, "close": 105.0,
                            "spread": 1.0, "Trading_Volume": 1, "Trading_money": 2, "Trading_turnover": 3,
                        }]))
    monkeypatch.setattr(FinMindClient, "fetch_stock_name", lambda self, sid: None)
    monkeypatch.setattr(FinMindClient, "fetch_institutional_investors_buy_sell", _no_finmind_institutional)

    payload = TestClient(api.app).get("/api/stocks/6488/latest?token=test").json()
    assert payload["price"]["close"] == 105.0
    assert payload["price_source"] == "finmind"


def test_latest_source_survives_the_cache(monkeypatch, tmp_path) -> None:
    api.cache = CacheStore(base_dir=tmp_path)
    monkeypatch.setattr(api.twse_open, "fetch_stock_day_all", lambda sid: _twse_price())
    monkeypatch.setattr(FinMindClient, "fetch_stock_name", lambda self, sid: None)
    monkeypatch.setattr(FinMindClient, "fetch_institutional_investors_buy_sell", _no_finmind_institutional)
    client = TestClient(api.app)
    client.get("/api/stocks/2330/latest?token=test")
    assert client.get("/api/stocks/2330/latest?token=test").json()["price_source"] == "twse"


def test_latest_price_refreshes_after_an_hour(monkeypatch, tmp_path) -> None:
    """A morning query must not pin the day's close to the previous session."""
    api.cache = CacheStore(base_dir=tmp_path)
    prices = iter([2370.0, 2400.0])
    monkeypatch.setattr(api.twse_open, "fetch_stock_day_all", lambda sid: _twse_price(next(prices)))
    monkeypatch.setattr(FinMindClient, "fetch_stock_name", lambda self, sid: None)
    monkeypatch.setattr(FinMindClient, "fetch_institutional_investors_buy_sell", _no_finmind_institutional)
    client = TestClient(api.app)
    assert client.get("/api/stocks/2330/latest?token=test").json()["price"]["close"] == 2370.0

    now = api.time.time()
    monkeypatch.setattr(api.time, "time", lambda: now + api._LATEST_PRICE_TTL_SECONDS + 1)
    assert client.get("/api/stocks/2330/latest?token=test").json()["price"]["close"] == 2400.0


def test_institutional_falls_back_to_twse_when_finmind_is_empty(monkeypatch, tmp_path) -> None:
    api.cache = CacheStore(base_dir=tmp_path)
    monkeypatch.setattr(api.twse_open, "fetch_stock_day_all", lambda sid: _twse_price())
    monkeypatch.setattr(FinMindClient, "fetch_stock_name", lambda self, sid: None)
    monkeypatch.setattr(FinMindClient, "fetch_institutional_investors_buy_sell", _no_finmind_institutional)
    monkeypatch.setattr(api.twse_open, "fetch_institutional_t86", lambda sid: pd.DataFrame([
        {"date": "2026-03-17", "name": "Foreign_Investor", "buy": 10, "sell": 4, "net": 6},
    ]))

    payload = TestClient(api.app).get("/api/stocks/2330/latest?token=test").json()
    assert payload["institutional_source"] == "twse"
    assert payload["institutional_status"] == "fresh"
    assert payload["institutional"] == [{"category": "外資", "buy": 10, "sell": 4, "net": 6}]


def test_institutional_falls_back_to_twse_when_finmind_fails(monkeypatch, tmp_path) -> None:
    api.cache = CacheStore(base_dir=tmp_path)

    def quota(self, *a, **kw):
        raise api.FinMindError("FinMind quota exceeded (HTTP 402).")

    monkeypatch.setattr(api.twse_open, "fetch_stock_day_all", lambda sid: _twse_price())
    monkeypatch.setattr(FinMindClient, "fetch_stock_name", lambda self, sid: None)
    monkeypatch.setattr(FinMindClient, "fetch_institutional_investors_buy_sell", quota)
    monkeypatch.setattr(api.twse_open, "fetch_institutional_t86", lambda sid: pd.DataFrame([
        {"date": "2026-03-17", "name": "Foreign_Investor", "buy": 10, "sell": 4, "net": 6},
    ]))
    assert TestClient(api.app).get("/api/stocks/2330/latest?token=test").status_code == 200


def test_institutional_reports_finmind_error_when_twse_is_also_empty(monkeypatch, tmp_path) -> None:
    api.cache = CacheStore(base_dir=tmp_path)

    def quota(self, *a, **kw):
        raise api.FinMindError("FinMind quota exceeded (HTTP 402).")

    monkeypatch.setattr(api.twse_open, "fetch_stock_day_all", lambda sid: _twse_price())
    monkeypatch.setattr(FinMindClient, "fetch_stock_name", lambda self, sid: None)
    monkeypatch.setattr(FinMindClient, "fetch_institutional_investors_buy_sell", quota)
    assert TestClient(api.app).get("/api/stocks/2330/latest?token=test").status_code == 502


def test_margin_trading_returns_latest_balances(monkeypatch) -> None:
    monkeypatch.setattr(api.twse_open, "fetch_margin_margn", lambda sid: pd.DataFrame([{
        "date": "2026-10-30", "margin_balance": 34961.0, "short_balance": 9.0, "short_margin_ratio": 0.03,
    }]))
    r = TestClient(api.app).get("/api/stocks/2330/margin_trading")
    assert r.status_code == 200
    assert r.json() == {
        "stock_id": "2330", "available": True, "date": "2026-10-30",
        "margin_balance": 34961.0, "short_balance": 9.0, "short_margin_ratio": 0.03,
    }


def test_margin_trading_without_data_is_not_an_error() -> None:
    r = TestClient(api.app).get("/api/stocks/6488/margin_trading")
    assert r.status_code == 200
    assert r.json() == {"stock_id": "6488", "available": False}


def test_margin_trading_handles_missing_ratio(monkeypatch) -> None:
    monkeypatch.setattr(api.twse_open, "fetch_margin_margn", lambda sid: pd.DataFrame([{
        "date": "2026-10-30", "margin_balance": 0.0, "short_balance": 0.0, "short_margin_ratio": None,
    }]))
    assert TestClient(api.app).get("/api/stocks/0050/margin_trading").json()["short_margin_ratio"] is None


def test_large_responses_are_gzipped() -> None:
    r = TestClient(api.app).get("/static/app.js?v=1", headers={"Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip"


def test_versioned_static_files_are_immutable() -> None:
    r = TestClient(api.app).get("/static/app.js?v=33")
    assert "immutable" in r.headers["cache-control"]


def test_unversioned_static_files_stay_revalidatable() -> None:
    r = TestClient(api.app).get("/static/app.js")
    assert "immutable" not in r.headers.get("cache-control", "")


def test_index_page_always_revalidates() -> None:
    assert TestClient(api.app).get("/").headers["cache-control"] == "no-cache"


def test_index_references_versioned_assets() -> None:
    """immutable caching only works if index.html always carries the cache-busters."""
    html = TestClient(api.app).get("/").text
    assert "/static/app.js?v=" in html
    assert "/static/styles.css?v=" in html


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="tzset is POSIX only")
def test_process_runs_in_taiwan_time() -> None:
    """Daily cache keys use date.today(); in UTC they would roll over at 08:00 Taipei."""
    assert time.timezone == -8 * 3600
