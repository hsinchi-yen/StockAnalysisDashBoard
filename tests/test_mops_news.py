from __future__ import annotations

from datetime import date

import pytest
import requests

import datasource_mops_news as mops
from datasource_mops_news import MopsNewsError, detect_auditor_change, detect_filing_delay, fetch_major_news

TODAY = date(2026, 11, 1)


def news_row(day: str, title: str) -> list:
    return ["2330", "台積電", day, "13:54:04", title, {"apiName": "t05st01_detail"}]


def ok(*rows) -> dict:
    return {"code": 200, "message": "查詢成功", "result": {"data": list(rows)}}


NO_DATA = {"code": 406, "message": "查無相符資料", "result": None}


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
def isolated(monkeypatch):
    mops._year_cache.clear()
    monkeypatch.setattr(mops.time, "sleep", lambda s: None)
    yield
    mops._year_cache.clear()


def serve(monkeypatch, by_year: dict[str, object]) -> list[dict]:
    calls: list[dict] = []

    def fake_post(url, json=None, timeout=None, headers=None):
        assert headers and headers.get("User-Agent")
        calls.append(json)
        return FakeResponse(by_year[json["year"]])

    monkeypatch.setattr(mops.requests, "post", fake_post)
    return calls


def test_reads_the_rolling_year_across_two_roc_years(monkeypatch) -> None:
    calls = serve(monkeypatch, {
        "114": ok(news_row("114/12/05", "去年底公告"), news_row("114/06/01", "超過一年的公告")),
        "115": ok(news_row("115/10/08", "台積公司2026年9月營收報告")),
    })
    news = fetch_major_news("2330", today=TODAY)
    assert [c["month"] for c in calls] == ["all", "all"]
    assert [n["title"] for n in news] == ["台積公司2026年9月營收報告", "去年底公告"]
    assert news[0]["date"] == "2026-10-08"


def test_multi_line_titles_are_flattened(monkeypatch) -> None:
    title = "公告更換簽證會計師事務所" + chr(13) + chr(10) + "及簽證會計師"
    serve(monkeypatch, {"114": NO_DATA, "115": ok(news_row("115/04/24", title))})
    assert fetch_major_news("2330", today=TODAY)[0]["title"] == "公告更換簽證會計師事務所 及簽證會計師"


def test_year_results_are_cached(monkeypatch) -> None:
    calls = serve(monkeypatch, {"114": NO_DATA, "115": ok(news_row("115/10/08", "公告"))})
    fetch_major_news("2330", today=TODAY)
    fetch_major_news("2330", today=TODAY)
    assert len(calls) == 2


def test_cache_expires_after_seven_days(monkeypatch) -> None:
    calls = serve(monkeypatch, {"114": NO_DATA, "115": NO_DATA})
    fetch_major_news("2330", today=TODAY)
    now = mops.time.time()
    monkeypatch.setattr(mops.time, "time", lambda: now + 7 * 86400 + 1)
    fetch_major_news("2330", today=TODAY)
    assert len(calls) == 4


def test_no_filings_is_a_clean_empty_result(monkeypatch) -> None:
    serve(monkeypatch, {"114": NO_DATA, "115": NO_DATA})
    assert fetch_major_news("5483", today=TODAY) == []


@pytest.mark.parametrize("failure", [
    FakeResponse(ValueError),  # HTML block page
    FakeResponse(None, status=503),
])
def test_unreadable_response_raises_rather_than_reporting_clean(monkeypatch, failure) -> None:
    monkeypatch.setattr(mops.requests, "post", lambda *a, **kw: failure)
    with pytest.raises(MopsNewsError):
        fetch_major_news("2330", today=TODAY)


def test_api_level_error_raises(monkeypatch) -> None:
    serve(monkeypatch, {"114": {"code": 500, "message": "傳入參數異常"}, "115": NO_DATA})
    with pytest.raises(MopsNewsError):
        fetch_major_news("2330", today=TODAY)


def test_failure_is_not_cached(monkeypatch) -> None:
    monkeypatch.setattr(mops.requests, "post", lambda *a, **kw: FakeResponse(ValueError))
    with pytest.raises(MopsNewsError):
        fetch_major_news("2330", today=TODAY)
    serve(monkeypatch, {"114": NO_DATA, "115": NO_DATA})
    assert fetch_major_news("2330", today=TODAY) == []


def test_retries_transient_failures(monkeypatch) -> None:
    attempts = {"n": 0}

    def flaky(url, json=None, timeout=None, headers=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise requests.ConnectionError("reset")
        return FakeResponse(NO_DATA)

    monkeypatch.setattr(mops.requests, "post", flaky)
    assert fetch_major_news("2330", today=TODAY) == []
    assert attempts["n"] == 3  # one retry, then the second year


def item(title: str) -> dict:
    return {"date": "2026-03-01", "title": title}


@pytest.mark.parametrize("title", [
    "公告本公司更換會計師",
    "本公司董事會決議變更簽證會計師",
    "公告本公司解任簽證會計師",
    "本公司委任新任簽證會計師",
    "本公司會計師事務所內部調整更換會計師",
])
def test_detects_auditor_changes(title) -> None:
    assert detect_auditor_change([item(title)])


@pytest.mark.parametrize("title", [
    "公告本公司2026年9月合併營業額",
    "本公司受邀參加法人說明會",
    "公告本公司董事會通過財務報告",
    "本公司會計師出具查核報告",
])
def test_ordinary_filings_are_not_auditor_changes(title) -> None:
    assert not detect_auditor_change([item(title)])


@pytest.mark.parametrize("title", [
    "本公司延後公告115年第二季財務報告",
    "本公司無法於期限內公告申報財務報告",
    "公告本公司財務報告將延遲申報",
])
def test_detects_filing_delays(title) -> None:
    assert detect_filing_delay([item(title)])


def test_ordinary_filings_are_not_delays() -> None:
    assert not detect_filing_delay([item("公告本公司董事會通過115年第三季財務報告")])
