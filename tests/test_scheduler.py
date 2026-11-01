from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

import api
import scheduler
from cache import CacheStore


@pytest.fixture()
def batch(monkeypatch, tmp_path):
    """A batch of two stocks where buy_score just records who it was called for."""
    api.cache = CacheStore(base_dir=tmp_path)
    monkeypatch.setenv("FINMIND_API_KEY", "env-token")
    monkeypatch.setattr(scheduler.time, "sleep", lambda s: None)
    monkeypatch.setattr(scheduler.query_history, "top_recent", lambda cache_dir, limit: ["2330", "2317"][:limit])
    calls: list[dict] = []

    def fake_buy_score(sid, token=None, x_finmind_token="unset"):
        calls.append({"sid": sid, "token": token, "header": x_finmind_token})
        return {"warnings": []}

    monkeypatch.setattr(api, "buy_score", fake_buy_score)
    return calls


def test_precomputes_every_recent_stock_with_the_env_token(batch) -> None:
    result = scheduler.precompute_recent()
    assert [c["sid"] for c in batch] == ["2330", "2317"]
    assert {c["token"] for c in batch} == {"env-token"}
    # A direct call bypasses FastAPI, so the Header() default must be overridden.
    assert {c["header"] for c in batch} == {None}
    assert result == {"stocks": 2, "ok": 2, "failed": []}


def test_skips_precompute_without_a_token(batch, monkeypatch) -> None:
    monkeypatch.delenv("FINMIND_API_KEY")
    assert scheduler.precompute_recent()["stocks"] == 0
    assert batch == []


def test_one_failing_stock_does_not_stop_the_batch(batch, monkeypatch) -> None:
    def flaky(sid, token=None, x_finmind_token=None):
        if sid == "2330":
            raise RuntimeError("boom")
        batch.append({"sid": sid})
        return {"warnings": []}

    monkeypatch.setattr(api, "buy_score", flaky)
    result = scheduler.precompute_recent()
    assert result == {"stocks": 2, "ok": 1, "failed": ["2330"]}
    assert [c["sid"] for c in batch] == ["2317"]


def test_batch_stops_once_quota_is_exhausted(batch, monkeypatch) -> None:
    def out_of_quota(sid, token=None, x_finmind_token=None):
        batch.append({"sid": sid})
        return {"warnings": ["roe_data: FinMind quota exceeded (HTTP 402)."]}

    monkeypatch.setattr(api, "buy_score", out_of_quota)
    scheduler.precompute_recent()
    assert [c["sid"] for c in batch] == ["2330"]


def test_stocks_are_spaced_out(batch, monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(scheduler.time, "sleep", sleeps.append)
    scheduler.precompute_recent()
    assert sleeps == [scheduler.SCHED_STOCK_DELAY_SECONDS]


def test_limit_is_forty_stocks(monkeypatch, batch) -> None:
    seen: dict[str, int] = {}
    monkeypatch.setattr(scheduler.query_history, "top_recent",
                        lambda cache_dir, limit: seen.update(limit=limit) or [])
    scheduler.precompute_recent()
    assert seen["limit"] == 40


def test_nightly_maintains_cache_even_without_a_token(batch, monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("FINMIND_API_KEY")
    api.cache.set("old", {"v": 1})
    path = api.cache._key_to_path("old")
    stale = time.time() - 30 * 86400
    os.utime(path, (stale, stale))
    scheduler.nightly()
    assert not path.exists()


def test_nightly_maintains_cache_after_a_failed_precompute(batch, monkeypatch) -> None:
    done: list[bool] = []

    def boom(token=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(scheduler, "precompute_recent", boom)
    monkeypatch.setattr(scheduler, "maintain_cache", lambda: done.append(True))
    with pytest.raises(RuntimeError):
        scheduler.nightly()
    assert done == [True]


def test_maintenance_flushes_when_a_persist_dir_is_set(batch, monkeypatch, tmp_path_factory) -> None:
    persist = tmp_path_factory.mktemp("persist")
    monkeypatch.setenv("MICROECO_CACHE_PERSIST_DIR", str(persist))
    api.cache.set("key", {"v": 1})
    scheduler.maintain_cache()
    assert list(persist.glob("*.json"))


def test_nightly_records_its_result_for_health_checks(batch, monkeypatch) -> None:
    monkeypatch.setattr(scheduler, "_last_run", None)
    scheduler.nightly()
    last_run = TestClient(api.app).get("/api/health").json()["scheduler"]["last_run"]
    assert last_run["stocks"] == 2
    assert last_run["ok"] == 2
    assert last_run["failed"] == []
    assert last_run["finished_at"].endswith("+08:00")


def test_health_reports_scheduler_state_before_the_first_run(monkeypatch) -> None:
    monkeypatch.delenv("FINMIND_API_KEY", raising=False)
    monkeypatch.setattr(scheduler, "_scheduler", None)
    monkeypatch.setattr(scheduler, "_last_run", None)
    assert TestClient(api.app).get("/api/health").json()["scheduler"] == {
        "running": False, "precompute_enabled": False, "last_run": None,
    }


def test_scheduler_runs_at_two_am_taipei(monkeypatch) -> None:
    pytest.importorskip("apscheduler")
    monkeypatch.setenv("MICROECO_SCHEDULER", "1")
    monkeypatch.setattr(scheduler, "_scheduler", None)
    try:
        assert scheduler.start() is True
        job = scheduler._scheduler.get_job("nightly")
        fields = {f.name: str(f) for f in job.trigger.fields}
        assert fields["hour"] == "2"
        assert fields["minute"] == "0"
        assert job.trigger.timezone.utcoffset(None).total_seconds() == 8 * 3600
    finally:
        scheduler.stop()


def test_scheduler_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("MICROECO_SCHEDULER", "0")
    monkeypatch.setattr(scheduler, "_scheduler", None)
    assert scheduler.start() is False


def test_app_starts_without_a_finmind_key(monkeypatch) -> None:
    monkeypatch.delenv("FINMIND_API_KEY", raising=False)
    monkeypatch.setenv("MICROECO_SCHEDULER", "1")
    monkeypatch.setattr(scheduler, "_scheduler", None)
    with TestClient(api.app) as client:
        assert client.get("/api/health").status_code == 200
    assert scheduler._scheduler is None
