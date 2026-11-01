from __future__ import annotations

import pytest
import requests

import datasource_twse_open as twse
# Bound at import: conftest stubs these module attributes for every other test.
from datasource_twse_open import fetch_institutional_t86, fetch_margin_margn, fetch_stock_day_all

DAY_ALL = [
    {
        "Date": "1151030", "Code": "2330", "Name": "台積電", "TradeVolume": "36615931",
        "TradeValue": "86833096690", "OpeningPrice": "2380.00", "HighestPrice": "2380.00",
        "LowestPrice": "2360.00", "ClosingPrice": "2370.00", "Change": "-45.0000", "Transaction": "331330",
    },
    {
        "Date": "1151030", "Code": "00682U", "Name": "期元大美元指數", "TradeVolume": "",
        "TradeValue": "", "OpeningPrice": "", "HighestPrice": "", "LowestPrice": "",
        "ClosingPrice": "", "Change": "0.0000", "Transaction": "",
    },
]

T86 = {
    "stat": "OK",
    "date": "20261030",
    "fields": [
        "證券代號", "證券名稱",
        "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)", "外陸資買賣超股數(不含外資自營商)",
        "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數",
        "投信買進股數", "投信賣出股數", "投信買賣超股數", "自營商買賣超股數",
        "自營商買進股數(自行買賣)", "自營商賣出股數(自行買賣)", "自營商買賣超股數(自行買賣)",
        "自營商買進股數(避險)", "自營商賣出股數(避險)", "自營商買賣超股數(避險)", "三大法人買賣超股數",
    ],
    "data": [[
        "2330", "台積電          ", "18,503,985", "27,136,819", "-8,632,834", "0", "0", "0",
        "1,863,657", "8,980", "1,854,677", "922,012", "402,050", "420,299", "-18,249",
        "1,723,036", "782,775", "940,261", "-5,856,145",
    ]],
}

MI_MARGN = {
    "stat": "OK",
    "date": "20261030",
    "tables": [
        {"title": "信用交易統計", "fields": ["項目", "買進"], "data": [["融資(交易單位)", "1"]]},
        {
            "title": "融資融券彙總",
            "fields": ["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額", "今日餘額", "次一營業日限額",
                       "買進", "賣出", "現券償還", "前日餘額", "今日餘額", "次一營業日限額", "資券互抵", "註記"],
            "data": [
                ["2330", "台積電", "491", "858", "15", "35,343", "34,961", "6,483,092",
                 "10", "3", "0", "16", "9", "6,483,092", "0", " "],
                ["0050", "元大台灣50", "1", "1", "0", "0", "0", "1", "0", "0", "0", "0", "0", "1", "0", " "],
            ],
        },
    ],
}


class FakeResponse:
    def __init__(self, payload, status: int = 200) -> None:
        self.payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if self.payload is ValueError:
            raise ValueError("not json")
        return self.payload


@pytest.fixture(autouse=True)
def fresh_snapshots():
    twse._snapshots.clear()
    yield
    twse._snapshots.clear()


def serve(monkeypatch, responses: dict[str, FakeResponse]) -> list[str]:
    """Route requests.get by URL and record every outbound call."""
    calls: list[str] = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        assert headers and headers.get("User-Agent")
        return responses[url]

    monkeypatch.setattr(twse.requests, "get", fake_get)
    return calls


def test_price_matches_finmind_columns(monkeypatch) -> None:
    serve(monkeypatch, {twse.STOCK_DAY_ALL_URL: FakeResponse(DAY_ALL)})
    df = fetch_stock_day_all("2330")
    assert list(df.columns) == twse.PRICE_COLUMNS
    row = df.iloc[0]
    assert row["date"] == "2026-10-30"
    assert row["close"] == 2370.0
    assert row["max"] == 2380.0
    assert row["spread"] == -45.0
    assert row["Trading_Volume"] == 36615931


def test_price_for_unknown_stock_is_empty(monkeypatch) -> None:
    serve(monkeypatch, {twse.STOCK_DAY_ALL_URL: FakeResponse(DAY_ALL)})
    assert fetch_stock_day_all("6488").empty


def test_price_for_untraded_stock_is_empty(monkeypatch) -> None:
    serve(monkeypatch, {twse.STOCK_DAY_ALL_URL: FakeResponse(DAY_ALL)})
    assert fetch_stock_day_all("00682U").empty


@pytest.mark.parametrize("response", [FakeResponse(None, status=500), FakeResponse(ValueError)])
def test_failed_download_is_empty_not_an_exception(monkeypatch, response) -> None:
    serve(monkeypatch, {twse.STOCK_DAY_ALL_URL: response})
    assert fetch_stock_day_all("2330").empty


def test_network_error_is_empty_not_an_exception(monkeypatch) -> None:
    def boom(*a, **kw):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(twse.requests, "get", boom)
    assert fetch_stock_day_all("2330").empty


def test_snapshot_is_downloaded_once_per_hour(monkeypatch) -> None:
    calls = serve(monkeypatch, {twse.STOCK_DAY_ALL_URL: FakeResponse(DAY_ALL)})
    fetch_stock_day_all("2330")
    fetch_stock_day_all("00682U")
    fetch_stock_day_all("2317")
    assert len(calls) == 1


def test_expired_snapshot_is_downloaded_again(monkeypatch) -> None:
    calls = serve(monkeypatch, {twse.STOCK_DAY_ALL_URL: FakeResponse(DAY_ALL)})
    fetch_stock_day_all("2330")
    now = twse.time.time()
    monkeypatch.setattr(twse.time, "time", lambda: now + twse._SNAPSHOT_TTL_SECONDS + 1)
    fetch_stock_day_all("2330")
    assert len(calls) == 2


def test_failure_is_remembered_so_an_outage_stays_cheap(monkeypatch) -> None:
    calls = serve(monkeypatch, {twse.STOCK_DAY_ALL_URL: FakeResponse(None, status=503)})
    fetch_stock_day_all("2330")
    fetch_stock_day_all("2330")
    assert len(calls) == 1


def test_t86_maps_investors_to_finmind_names(monkeypatch) -> None:
    serve(monkeypatch, {twse.T86_URL: FakeResponse(T86)})
    df = fetch_institutional_t86("2330").set_index("name")
    assert set(df.index) == {"Foreign_Investor", "Foreign_Dealer_Self", "Investment_Trust", "Dealer_self", "Dealer_Hedging"}
    assert df.loc["Foreign_Investor", "buy"] == 18503985
    assert df.loc["Foreign_Investor", "net"] == -8632834
    assert df.loc["Investment_Trust", "net"] == 1854677
    assert df.loc["Dealer_Hedging", "sell"] == 782775
    assert (df["date"] == "2026-10-30").all()


def test_t86_without_data_is_empty(monkeypatch) -> None:
    serve(monkeypatch, {twse.T86_URL: FakeResponse({"stat": "很抱歉，沒有符合條件的資料!"})})
    assert fetch_institutional_t86("2330").empty


def test_margin_reads_both_today_balances(monkeypatch) -> None:
    serve(monkeypatch, {twse.MI_MARGN_URL: FakeResponse(MI_MARGN)})
    row = fetch_margin_margn("2330").iloc[0]
    assert row["date"] == "2026-10-30"
    assert row["margin_balance"] == 34961
    assert row["short_balance"] == 9
    assert row["short_margin_ratio"] == pytest.approx(0.03)


def test_margin_with_zero_balance_has_no_ratio(monkeypatch) -> None:
    serve(monkeypatch, {twse.MI_MARGN_URL: FakeResponse(MI_MARGN)})
    assert fetch_margin_margn("0050").iloc[0]["short_margin_ratio"] is None


def test_margin_for_otc_stock_is_empty(monkeypatch) -> None:
    serve(monkeypatch, {twse.MI_MARGN_URL: FakeResponse(MI_MARGN)})
    assert fetch_margin_margn("6488").empty
