from __future__ import annotations

import pytest

import datasource_goodinfo as gi
from datasource_goodinfo import GoodinfoClient, GoodinfoError


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status_code = status
        self.text = "<html>" + "x" * 6000 + "</html>"
        self.encoding = "utf-8"
        self.url = "https://goodinfo.tw/x"

    def raise_for_status(self) -> None:
        pass


class FakeSession:
    def __init__(self, statuses: list[int]) -> None:
        self.statuses = statuses
        self.calls = 0
        self.headers: dict = {}
        self.cookies = None

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return FakeResponse(self.statuses[min(self.calls - 1, len(self.statuses) - 1)])


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(gi, "_blocked_until", 0.0)
    monkeypatch.setattr(gi.time, "sleep", lambda s: None)


def client(session: FakeSession) -> GoodinfoClient:
    c = GoodinfoClient()
    c._session = session
    return c


@pytest.mark.parametrize("fetch", [
    lambda c: c._fetch_sharehold_html("2330"),
    lambda c: c._fetch_html("2330", "BS_M_QUAR"),
])
def test_403_fails_at_once_without_retrying(fetch) -> None:
    session = FakeSession([403])
    with pytest.raises(GoodinfoError):
        fetch(client(session))
    assert session.calls == 1


def test_block_stops_every_client_during_the_cooldown() -> None:
    """Each endpoint builds its own client, so the block must outlive the instance."""
    with pytest.raises(GoodinfoError):
        client(FakeSession([403]))._fetch_sharehold_html("2330")

    other = FakeSession([200])
    with pytest.raises(GoodinfoError):
        client(other)._fetch_sharehold_html("2317")
    with pytest.raises(GoodinfoError):
        client(other)._fetch_html("2317", "BS_M_QUAR")
    assert other.calls == 0


def test_requests_resume_after_the_cooldown(monkeypatch) -> None:
    with pytest.raises(GoodinfoError):
        client(FakeSession([403]))._fetch_sharehold_html("2330")

    now = gi.time.time()
    monkeypatch.setattr(gi.time, "time", lambda: now + gi._BLOCK_COOLDOWN_SECONDS + 1)
    session = FakeSession([200])
    assert client(session)._fetch_sharehold_html("2330")
    assert session.calls == 1


def test_429_is_still_retried() -> None:
    """A rate limit clears by itself, unlike a block."""
    session = FakeSession([429, 200])
    assert client(session)._fetch_sharehold_html("2330")
    assert session.calls == 2
    assert gi._blocked_until == 0.0
