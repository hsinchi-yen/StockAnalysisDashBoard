from __future__ import annotations

from datetime import date

import pandas as pd
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
