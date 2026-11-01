from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from datasource_finmind import FinMindClient


def _eps(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame([
        {"quarter": q, "quarter_label": q, "eps": eps, "eps_yoy": None} for q, eps in rows
    ], columns=["quarter", "quarter_label", "eps", "eps_yoy"])


def _river(monkeypatch, rows: list[tuple[str, float]]) -> list[dict]:
    monkeypatch.setattr(FinMindClient, "fetch_stock_per", lambda self, *a, **kw: pd.DataFrame())
    monkeypatch.setattr(FinMindClient, "fetch_eps_trend", lambda self, *a, **kw: _eps(rows))
    client = FinMindClient(api_key="tok")
    return client.fetch_pe_river_data("2330", date(2024, 1, 1), date(2026, 11, 1))["eps_ttm_rows"]


def test_ttm_eps_sums_reported_quarters(monkeypatch) -> None:
    """2330's real figures: price 2370 / PER 27.47 gives the same 86.28."""
    rows = _river(monkeypatch, [
        ("2025-09-30", 17.44), ("2025-12-31", 19.51), ("2026-03-31", 22.08), ("2026-06-30", 27.25),
    ])
    assert rows == [{"quarter": "2026-06-30", "quarter_label": "2026-06-30", "eps_ttm": 86.28}]


def test_needs_four_quarters(monkeypatch) -> None:
    assert _river(monkeypatch, [("2025-12-31", 1.0), ("2026-03-31", 1.0), ("2026-06-30", 1.0)]) == []


def test_gap_between_quarters_is_skipped(monkeypatch) -> None:
    assert _river(monkeypatch, [
        ("2025-03-31", 1.0), ("2025-06-30", 1.0), ("2025-12-31", 1.0), ("2026-03-31", 1.0),
    ]) == []


def test_losses_stay_negative(monkeypatch) -> None:
    rows = _river(monkeypatch, [
        ("2025-09-30", -0.24), ("2025-12-31", -0.03), ("2026-03-31", -0.07), ("2026-06-30", -0.45),
    ])
    assert rows[-1]["eps_ttm"] == pytest.approx(-0.79)
