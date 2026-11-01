from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Keep tests offline and independent of each other: no shared on-disk dataset
# cache, and no politeness delays between (mocked) FinMind calls.
os.environ.setdefault("FINMIND_DS_CACHE", "0")
os.environ.setdefault("BUY_SCORE_FETCH_DELAY", "0")
os.environ.setdefault("MICROECO_SCHEDULER", "0")
os.environ.setdefault("MICROECO_CACHE_DIR", tempfile.mkdtemp(prefix="microeco-tests-"))


@pytest.fixture(autouse=True)
def offline_market_sources(monkeypatch):
    """TWSE and MOPS report no data unless a test says otherwise."""
    import api

    monkeypatch.setattr(api.twse_open, "fetch_stock_day_all", lambda sid: pd.DataFrame())
    monkeypatch.setattr(api.twse_open, "fetch_institutional_t86", lambda sid: pd.DataFrame())
    monkeypatch.setattr(api.twse_open, "fetch_margin_margn", lambda sid: pd.DataFrame())
    monkeypatch.setattr(api, "fetch_major_news", lambda sid: [])
