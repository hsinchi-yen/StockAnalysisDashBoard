"""隔夜工作：每天 02:00（台北）預算近期查過的股票並整理快取。

預算需要 FINMIND_API_KEY（使用者的 token 只在瀏覽器裡，不落地）；快取整理不需要。
手動跑一輪：python scheduler.py --now
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import query_history

SCHED_HOUR = 2
SCHED_MAX_STOCKS = 40
SCHED_STOCK_DELAY_SECONDS = 5.0
DEFAULT_CACHE_TTL_DAYS = 7.0
# Fixed offset rather than a zone name: slim containers have no tz database.
TAIPEI = timezone(timedelta(hours=8))

_log = logging.getLogger("uvicorn.error")
_scheduler: Any = None
_last_run: Optional[dict[str, Any]] = None


def _persist_dir() -> Optional[Path]:
    value = os.environ.get("MICROECO_CACHE_PERSIST_DIR", "").strip()
    return Path(value) if value else None


def _cache_ttl_seconds() -> float:
    return float(os.environ.get("MICROECO_CACHE_TTL_DAYS", DEFAULT_CACHE_TTL_DAYS)) * 86400


def precompute_recent(token: Optional[str] = None) -> dict[str, Any]:
    """Compute buy_score for recently queried stocks so it is cached by morning."""
    import api

    token = token or os.environ.get("FINMIND_API_KEY", "").strip()
    if not token:
        _log.info("scheduler: precompute skipped, FINMIND_API_KEY not set")
        return {"stocks": 0, "ok": 0, "failed": []}

    stocks = query_history.top_recent(api.CACHE_DIR, limit=SCHED_MAX_STOCKS)
    ok = 0
    failed: list[str] = []
    for index, sid in enumerate(stocks):
        if index:
            time.sleep(SCHED_STOCK_DELAY_SECONDS)
        try:
            result = api.buy_score(sid, token=token, x_finmind_token=None)
        except Exception as exc:
            failed.append(sid)
            _log.warning("scheduler: %s failed: %s", sid, type(exc).__name__)
            continue
        ok += 1
        # buy_score reports quota errors as warnings. Once the quota is gone,
        # every remaining stock would only burn calls and cache nothing.
        if any("quota" in str(w).lower() or "402" in str(w) for w in result.get("warnings", [])):
            _log.warning("scheduler: FinMind quota exhausted after %s, stopping batch", sid)
            break

    _log.info("scheduler: precompute finished, %d/%d ok, failed=%s", ok, len(stocks), failed)
    return {"stocks": len(stocks), "ok": ok, "failed": failed}


def maintain_cache() -> None:
    import api

    removed = api.cache.purge_expired(_cache_ttl_seconds())
    persist = _persist_dir()
    flushed = api.cache.flush_to(persist) if persist else 0
    _log.info("scheduler: cache maintenance removed=%d flushed=%d", removed, flushed)


def nightly() -> None:
    global _last_run
    result: dict[str, Any] = {"stocks": 0, "ok": 0, "failed": []}
    try:
        result = precompute_recent()
    finally:
        maintain_cache()
        _last_run = {"finished_at": datetime.now(TAIPEI).isoformat(timespec="seconds"), **result}


def status() -> dict[str, Any]:
    """Nightly job state for /api/health. last_run resets when the process restarts."""
    return {
        "running": _scheduler is not None,
        "precompute_enabled": bool(os.environ.get("FINMIND_API_KEY", "").strip()),
        "last_run": _last_run,
    }


def start() -> bool:
    """Start the nightly job. Skipped where APScheduler isn't installed (Android)."""
    global _scheduler
    if _scheduler is not None or os.environ.get("MICROECO_SCHEDULER", "1") == "0":
        return False
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        _log.info("scheduler: APScheduler not installed, nightly jobs disabled")
        return False

    scheduler = BackgroundScheduler(timezone=TAIPEI, daemon=True)
    scheduler.add_job(
        nightly, CronTrigger(hour=SCHED_HOUR, minute=0, timezone=TAIPEI),
        id="nightly", max_instances=1, coalesce=True, misfire_grace_time=3600,
    )
    scheduler.start()
    _scheduler = scheduler
    if not os.environ.get("FINMIND_API_KEY", "").strip():
        _log.info("scheduler: FINMIND_API_KEY not set, nightly precompute will be skipped")
    return True


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    if "--now" not in sys.argv:
        sys.exit("usage: python scheduler.py --now")
    nightly()
