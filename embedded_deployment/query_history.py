"""近期查詢紀錄：隔夜預算的輸入清單（快取目錄下的 query_history.json，7 天滾動）。"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Optional

HISTORY_FILE = "query_history.json"
WINDOW_DAYS = 7
# /latest is also called by /quick_summary, so one page query reaches record()
# twice. Counting those once keeps the ranking a measure of how often a stock is
# actually looked up.
_DEDUPE_SECONDS = 60.0

_lock = threading.Lock()


def _load(path: Path) -> list[dict]:
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # missing or corrupt: start over
        return []
    return [e for e in entries if isinstance(e, dict) and "stock_id" in e and "ts" in e] \
        if isinstance(entries, list) else []


def record(cache_dir: Path, stock_id: str, now: Optional[float] = None) -> None:
    """Append a lookup and drop entries older than the window."""
    now = time.time() if now is None else now
    path = Path(cache_dir) / HISTORY_FILE
    cutoff = now - WINDOW_DAYS * 86400
    with _lock:
        entries = [e for e in _load(path) if e["ts"] >= cutoff]
        if any(e["stock_id"] == stock_id and now - e["ts"] < _DEDUPE_SECONDS for e in entries):
            return
        entries.append({"stock_id": stock_id, "ts": now})
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries), encoding="utf-8")
        os.replace(tmp, path)


def top_recent(cache_dir: Path, limit: int = 40, days: int = WINDOW_DAYS, now: Optional[float] = None) -> list[str]:
    """Stocks looked up in the window, most-queried first, ties by most recent."""
    now = time.time() if now is None else now
    cutoff = now - days * 86400
    with _lock:
        entries = [e for e in _load(Path(cache_dir) / HISTORY_FILE) if e["ts"] >= cutoff]

    counts = Counter(e["stock_id"] for e in entries)
    latest: dict[str, float] = {}
    for entry in entries:
        latest[entry["stock_id"]] = max(entry["ts"], latest.get(entry["stock_id"], 0.0))
    return sorted(counts, key=lambda sid: (-counts[sid], -latest[sid]))[:limit]
