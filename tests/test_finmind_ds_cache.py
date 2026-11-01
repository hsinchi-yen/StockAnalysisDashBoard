from __future__ import annotations

import threading
import time
from datetime import date

import pytest

import datasource_finmind as fm
from datasource_finmind import FinMindClient, FinMindError

ROWS = [
    {"date": "2024-03-31", "type": "Revenue", "value": 1},
    {"date": "2025-03-31", "type": "Revenue", "value": 2},
    {"date": "2026-03-31", "type": "Revenue", "value": 3},
]


class FakeResponse:
    def __init__(self, data=None, status: int = 200, text: str = "") -> None:
        self.data = data if data is not None else []
        self.status_code = status
        self.text = text

    def json(self):
        return {"status": 200, "data": self.data}


class FakeSession:
    """Counts outbound calls across clients, like FinMind's quota would."""

    def __init__(self, data=ROWS, delay: float = 0.0, status: int = 200) -> None:
        self.data = data
        self.delay = delay
        self.status = status
        self.calls: list[dict] = []
        self.lock = threading.Lock()

    def get(self, url, params=None, timeout=None):
        with self.lock:
            self.calls.append(dict(params))
        if self.delay:
            time.sleep(self.delay)
        return FakeResponse(self.data, status=self.status)


@pytest.fixture()
def shared_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("FINMIND_DS_CACHE", "1")
    monkeypatch.setenv("MICROECO_CACHE_DIR", str(tmp_path))
    fm._store_at.cache_clear()
    fm._ds_inflight.clear()
    yield
    fm._store_at.cache_clear()


def client(session) -> FinMindClient:
    return FinMindClient(api_key="tok", session=session)


def read(session, start=date(2025, 1, 1), end=None, dataset="TaiwanStockFinancialStatements"):
    return client(session)._get_dataset(
        dataset=dataset, stock_id="2330", start_date=start, end_date=end or date.today(),
    )


def test_second_endpoint_for_same_range_makes_no_call(shared_cache) -> None:
    session = FakeSession()
    first = read(session)
    second = read(session)
    assert len(session.calls) == 1
    assert first == second


def test_shorter_range_is_served_from_the_widened_fetch(shared_cache) -> None:
    session = FakeSession()
    read(session, start=date(2025, 1, 1))
    narrower = read(session, start=date(2026, 1, 1))
    assert len(session.calls) == 1
    assert [r["value"] for r in narrower] == [3]


def test_first_fetch_widens_to_six_years(shared_cache) -> None:
    session = FakeSession()
    read(session, start=date.today().replace(day=1))
    assert session.calls[0]["start_date"] == f"{date.today().year - 6}-{date.today().month:02d}-01"
    assert session.calls[0]["end_date"] == date.today().isoformat()


def test_range_older_than_the_cache_fetches_again_and_keeps_the_wider_window(shared_cache) -> None:
    session = FakeSession()
    read(session)
    older = date(date.today().year - 9, 1, 1)
    read(session, start=older)
    assert len(session.calls) == 2
    assert session.calls[1]["start_date"] == older.isoformat()
    read(session, start=older)
    assert len(session.calls) == 2


def test_empty_results_are_not_cached(shared_cache) -> None:
    """盤後 data arrives later the same day, so an empty answer must stay retryable."""
    session = FakeSession(data=[])
    assert read(session) == []
    assert read(session) == []
    assert len(session.calls) == 2


def test_errors_are_not_cached(shared_cache) -> None:
    failing = FakeSession(status=402)
    with pytest.raises(FinMindError):
        read(failing)
    working = FakeSession()
    assert len(read(working)) == 2
    assert len(working.calls) == 1


def test_concurrent_cold_requests_share_one_fetch(shared_cache) -> None:
    """Cold page queries fire in parallel, so a plain cache would miss for all of them."""
    session = FakeSession(delay=0.1)
    results: list[list[dict]] = []
    threads = [threading.Thread(target=lambda: results.append(read(session))) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert len(results) == 8
    assert len(session.calls) == 1
    assert all(len(r) == 2 for r in results)


def test_concurrent_empty_results_still_share_one_fetch(shared_cache) -> None:
    session = FakeSession(data=[], delay=0.1)
    threads = [threading.Thread(target=lambda: read(session)) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert len(session.calls) == 1


def test_waiters_receive_the_leaders_failure(shared_cache) -> None:
    session = FakeSession(delay=0.1, status=402)
    errors: list[Exception] = []

    def attempt() -> None:
        try:
            read(session)
        except FinMindError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=attempt) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert len(errors) == 4
    assert len(session.calls) == 1
    assert not fm._ds_inflight


def test_intraday_datasets_expire_within_the_day(shared_cache, monkeypatch) -> None:
    session = FakeSession(data=[{"date": date.today().isoformat(), "close": 1}])
    read(session, dataset="TaiwanStockPrice")
    read(session, dataset="TaiwanStockPrice")
    assert len(session.calls) == 1

    now = time.time()
    monkeypatch.setattr(fm.time, "time", lambda: now + fm._DS_HOURLY_TTL_SECONDS + 1)
    read(session, dataset="TaiwanStockPrice")
    assert len(session.calls) == 2


def test_statements_stay_cached_past_an_hour(shared_cache, monkeypatch) -> None:
    session = FakeSession()
    read(session)
    now = time.time()
    monkeypatch.setattr(fm.time, "time", lambda: now + fm._DS_HOURLY_TTL_SECONDS + 1)
    read(session)
    assert len(session.calls) == 1


def test_cache_refreshes_on_a_new_day_without_keeping_old_copies(shared_cache, monkeypatch, tmp_path) -> None:
    session = FakeSession()
    read(session)
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1

    class Tomorrow(date):
        @classmethod
        def today(cls):
            return date(2099, 1, 1)

    monkeypatch.setattr(fm, "date", Tomorrow)
    read(session, end=date(2098, 12, 31))
    assert len(session.calls) == 2
    assert list(tmp_path.glob("*.json")) == files


def test_cache_can_be_switched_off(shared_cache, monkeypatch) -> None:
    monkeypatch.setenv("FINMIND_DS_CACHE", "0")
    session = FakeSession()
    read(session)
    read(session)
    assert len(session.calls) == 2


def test_rejected_token_is_only_tried_once(shared_cache, monkeypatch) -> None:
    """A session JWT fails every call, so its anonymous retry shouldn't cost two requests."""
    fm._illegal_tokens.clear()
    monkeypatch.setenv("FINMIND_DS_CACHE", "0")
    calls: list[dict] = []

    class IllegalTokenSession:
        def get(self, url, params=None, timeout=None):
            calls.append(dict(params))
            if "token" in params:
                return FakeResponse(status=400, text='{"msg":"Token is illegal."}')
            return FakeResponse(ROWS)

    session = IllegalTokenSession()
    read(session)
    read(session, start=date(2024, 1, 1))
    assert ["token" in c for c in calls] == [True, False, False]
    fm._illegal_tokens.clear()
