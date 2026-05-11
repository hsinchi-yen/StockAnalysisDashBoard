from __future__ import annotations

import pytest

from datasource_finmind import FinMindClient, FinMindError


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload


class _FakeSession:
    def __init__(self, post_response: _FakeResponse, get_response: _FakeResponse | None = None) -> None:
        self.post_response = post_response
        self.get_response = get_response
        self.post_calls: list[dict] = []
        self.get_calls: list[dict] = []

    def post(self, url: str, data: dict, timeout: float) -> _FakeResponse:
        self.post_calls.append({"url": url, "data": data, "timeout": timeout})
        return self.post_response

    def get(self, url: str, params: dict, timeout: float) -> _FakeResponse:
        self.get_calls.append({"url": url, "params": params, "timeout": timeout})
        if self.get_response is None:
            raise AssertionError("unexpected GET call")
        return self.get_response


def test_fetch_user_info_uses_post_first() -> None:
    session = _FakeSession(
        post_response=_FakeResponse(
            200,
            {"status": 200, "user_count": 12, "api_request_limit": 600},
        )
    )
    client = FinMindClient(api_key="abc123", session=session)

    payload = client.fetch_user_info(timeout=8.0)

    assert payload["user_count"] == 12
    assert len(session.post_calls) == 1
    assert session.post_calls[0]["data"] == {"token": "abc123"}
    assert session.get_calls == []


def test_fetch_user_info_falls_back_to_get_on_405() -> None:
    session = _FakeSession(
        post_response=_FakeResponse(405, text="Method Not Allowed"),
        get_response=_FakeResponse(
            200,
            {"status": 200, "user_count": 20, "api_request_limit": 600},
        ),
    )
    client = FinMindClient(api_key="abc123", session=session)

    payload = client.fetch_user_info()

    assert payload["user_count"] == 20
    assert len(session.post_calls) == 1
    assert len(session.get_calls) == 1
    assert session.get_calls[0]["params"] == {"token": "abc123"}


def test_fetch_user_info_raises_on_bad_payload_status() -> None:
    session = _FakeSession(post_response=_FakeResponse(200, {"status": 400, "msg": "bad token"}))
    client = FinMindClient(api_key="abc123", session=session)

    with pytest.raises(FinMindError):
        client.fetch_user_info()
