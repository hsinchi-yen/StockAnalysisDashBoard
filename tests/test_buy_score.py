"""Tests for /api/stocks/{stock_id}/buy_score endpoint and sloan_ratio helper."""
from __future__ import annotations

import threading
from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import api
from cache import CacheStore
from datasource_finmind import FinMindClient
from series_builder import compute_sloan_ratio, decumulate_ytd, recent_ocf_to_income


# ---------------------------------------------------------------------------
# Unit tests: compute_sloan_ratio
# ---------------------------------------------------------------------------

class TestComputeSloanRatio:
    def test_high_quality_earnings(self) -> None:
        # Net income == operating CF → ratio = 0 (perfect quality)
        result = compute_sloan_ratio(1000.0, 1000.0, 10000.0)
        assert result == pytest.approx(0.0)

    def test_negative_accruals(self) -> None:
        # Net income < operating CF → negative ratio (earnings backed by cash)
        result = compute_sloan_ratio(800.0, 1000.0, 10000.0)
        assert result == pytest.approx(-0.02)

    def test_positive_accruals_within_threshold(self) -> None:
        result = compute_sloan_ratio(1050.0, 1000.0, 10000.0)
        assert result == pytest.approx(0.005)
        assert abs(result) < 0.1

    def test_positive_accruals_outside_threshold(self) -> None:
        result = compute_sloan_ratio(2500.0, 1000.0, 10000.0)
        assert result == pytest.approx(0.15)
        assert abs(result) >= 0.1

    def test_zero_assets_returns_none(self) -> None:
        assert compute_sloan_ratio(500.0, 400.0, 0.0) is None


# ---------------------------------------------------------------------------
# Fixtures and helpers for endpoint tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def test_client(monkeypatch, tmp_path):
    """Return a TestClient with a fresh cache and fixed today date."""
    api.cache = CacheStore(base_dir=tmp_path)
    monkeypatch.setenv("FINMIND_API_KEY", "test_token")

    class FixedDate(date):
        @classmethod
        def today(cls) -> date:  # type: ignore[override]
            return cls(2026, 3, 17)

    monkeypatch.setattr(api, "date", FixedDate)
    return TestClient(api.app)


def _make_ni_series(years: list[int], value: float = 5_000_000_000.0) -> pd.Series:
    """Four quarters of net income per year.
    `value` is the desired annual NI; each quarter gets value/4 so TTM == value.
    """
    quarterly = value / 4
    data = {}
    for y in years:
        for m in [3, 6, 9, 12]:
            data[pd.Timestamp(y, m, 1)] = quarterly
    return pd.Series(data)


def _make_equity_assets_series(years: list[int]) -> tuple[pd.Series, pd.Series]:
    equity, assets = {}, {}
    for y in years:
        for m in [3, 6, 9, 12]:
            ts = pd.Timestamp(y, m, 1)
            equity[ts] = 30_000_000_000.0
            assets[ts] = 100_000_000_000.0
    return pd.Series(equity), pd.Series(assets)


def _make_liabilities_assets(latest_debt_pct: float = 40.0) -> tuple[pd.Series, pd.Series]:
    ts = pd.Timestamp(2025, 12, 1)
    assets_val = 100_000_000_000.0
    liabilities_val = assets_val * (latest_debt_pct / 100)
    return pd.Series({ts: liabilities_val}), pd.Series({ts: assets_val})


def _make_fcf_df(years: list[int], fcf_value: float = 10_000_000_000.0) -> pd.DataFrame:
    rows = [{"year": y, "fcf": fcf_value, "operating_cf": fcf_value + 2_000_000_000.0, "capex": -2_000_000_000.0} for y in years]
    return pd.DataFrame(rows)


def _make_revenue_df(months: int = 18, yoy_positive: bool = True) -> pd.DataFrame:
    """18 months of revenue; last 3 months YoY positive if yoy_positive=True."""
    rows = []
    base = pd.Timestamp(2024, 9, 1)  # 18 months before 2026-03
    for i in range(months):
        month = base + pd.DateOffset(months=i)
        # Increase revenue over time if yoy_positive, else flat
        rev = 10_000_000_000.0 * (1.05 ** i if yoy_positive else 1.0)
        rows.append({"month": month, "revenue": rev})
    return pd.DataFrame(rows)


def _make_eps_df(quarters: int = 8, yoy_positive: bool = True) -> pd.DataFrame:
    rows = []
    for i in range(quarters):
        q = pd.Timestamp(2024, 3, 1) + pd.DateOffset(months=i * 3)
        rows.append({
            "quarter": q,
            "quarter_label": f"{q.year} Q{(q.month-1)//3+1}",
            "eps": 5.0 + (0.5 * i if yoy_positive else 0),
            "eps_yoy": 10.0 if yoy_positive else -5.0,
        })
    return pd.DataFrame(rows)


def _make_margins_df(recent_gm: float = 55.0, prior_gm: float = 50.0) -> pd.DataFrame:
    rows = []
    quarters = [
        pd.Timestamp(2023, 12, 1), pd.Timestamp(2024, 3, 1),
        pd.Timestamp(2024, 6, 1), pd.Timestamp(2024, 9, 1),  # prior 4
        pd.Timestamp(2024, 12, 1), pd.Timestamp(2025, 3, 1),
        pd.Timestamp(2025, 6, 1), pd.Timestamp(2025, 9, 1),  # recent 4
    ]
    for i, q in enumerate(quarters):
        gm = recent_gm if i >= 4 else prior_gm
        rows.append({
            "quarter": q,
            "quarter_label": f"{q.year} Q{(q.month-1)//3+1}",
            "gross_margin": gm, "operating_margin": 30.0, "net_margin": 25.0,
        })
    return pd.DataFrame(rows)


def _make_per_df(current_per: float = 15.0, median_per: float = 20.0) -> pd.DataFrame:
    """Current PER is below median → buy signal."""
    rows = []
    for i in range(60):  # 60 months of history
        d = pd.Timestamp(2021, 3, 1) + pd.DateOffset(months=i)
        rows.append({"date": d, "PER": median_per + (i % 10 - 5) * 0.5, "PBR": 3.0, "dividend_yield": 2.0})
    # Override last entry with current_per
    rows[-1]["PER"] = current_per
    return pd.DataFrame(rows)


def _make_inst_df(net_per_day: float = 1000.0) -> pd.DataFrame:
    """Foreign_Investor carries the full net_per_day; Investment_Trust is flat zero
    so the combined 10-day total equals net_per_day × 10 (positive or negative)."""
    rows = []
    for i in range(15):
        d = pd.Timestamp(2026, 2, 17) + pd.DateOffset(days=i)
        rows.append({"date": d, "name": "Foreign_Investor", "buy": 5000, "sell": 5000 - net_per_day, "net": net_per_day})
        rows.append({"date": d, "name": "Investment_Trust", "buy": 2000, "sell": 2000, "net": 0.0})
    return pd.DataFrame(rows)


def _make_shareholding_df(trend: str = "up") -> pd.DataFrame:
    if trend == "up":
        ratios = [30.0, 31.0, 32.0]
    else:
        ratios = [32.0, 31.0, 30.0]
    rows = []
    for i, ratio in enumerate(ratios):
        d = pd.Timestamp(2025, 12, 1) + pd.DateOffset(months=i)
        rows.append({"date": d, "foreign_ratio": ratio, "total_dir_ratio": 10.0})
    return pd.DataFrame(rows)


def _patch_all_finmind(monkeypatch, *, debt_pct=40.0, fcf_positive=True, roe_value=20.0,
                        # roe_value = desired TTM ROE%; annual NI = roe_value/100 * equity
                        rev_yoy_pos=True, eps_yoy_pos=True, gm_up=True,
                        inst_net=1000.0, current_per=15.0, foreign_trend="up"):
    """Patch all FinMindClient methods used by buy_score."""
    years = [2022, 2023, 2024, 2025]
    # annual NI = roe_value% × avg_equity (30B) → TTM ROE == roe_value%
    ni_series = _make_ni_series(years, value=roe_value / 100 * 30_000_000_000.0)
    eq_series, as_series = _make_equity_assets_series(years)
    liab_series, asset_debt_series = _make_liabilities_assets(debt_pct)
    fcf_val = 10_000_000_000.0 if fcf_positive else -1_000_000_000.0
    fcf_df = _make_fcf_df([2023, 2024, 2025], fcf_value=fcf_val)
    rev_df = _make_revenue_df(yoy_positive=rev_yoy_pos)
    eps_df = _make_eps_df(yoy_positive=eps_yoy_pos)
    margins_df = _make_margins_df(recent_gm=55.0 if gm_up else 45.0, prior_gm=50.0)
    per_df = _make_per_df(current_per=current_per)
    inst_df = _make_inst_df(net_per_day=inst_net)
    sh_df = _make_shareholding_df(trend=foreign_trend)

    monkeypatch.setattr(FinMindClient, "fetch_quarterly_ni",
                        lambda self, *a, **kw: ni_series)
    monkeypatch.setattr(FinMindClient, "fetch_quarterly_bs_for_roe",
                        lambda self, *a, **kw: (eq_series, as_series))
    monkeypatch.setattr(FinMindClient, "fetch_quarterly_bs_liabilities_assets",
                        lambda self, *a, **kw: (liab_series, asset_debt_series))
    monkeypatch.setattr(FinMindClient, "fetch_annual_fcf_data",
                        lambda self, *a, **kw: (fcf_df, 1_000_000_000.0))
    monkeypatch.setattr(FinMindClient, "fetch_month_revenue",
                        lambda self, *a, **kw: rev_df)
    monkeypatch.setattr(FinMindClient, "fetch_eps_trend",
                        lambda self, *a, **kw: eps_df)
    monkeypatch.setattr(FinMindClient, "fetch_margin_ratios",
                        lambda self, *a, **kw: margins_df)
    monkeypatch.setattr(FinMindClient, "fetch_stock_per",
                        lambda self, *a, **kw: per_df)
    monkeypatch.setattr(FinMindClient, "fetch_institutional_investors_buy_sell",
                        lambda self, *a, **kw: inst_df)
    monkeypatch.setattr(FinMindClient, "fetch_dividend_payout_ratio",
                        lambda self, *a, **kw: 0.6)
    monkeypatch.setattr(FinMindClient, "fetch_shareholding_spread",
                        lambda self, *a, **kw: pd.DataFrame(columns=["date", "HoldingSharesLevel", "percent"]))
    monkeypatch.setattr(FinMindClient, "fetch_inventory_and_revenue_growth",
                        lambda self, *a, **kw: {"inv_yoy": 5.0, "ar_yoy": 5.0, "rev_yoy": 10.0})
    monkeypatch.setattr(FinMindClient, "fetch_quarterly_fcf_data",
                        lambda self, *a, **kw: pd.DataFrame(
                            columns=["quarter", "quarter_label", "operating_cf", "capex", "fcf"]))

    # Patch Goodinfo
    if api.GoodinfoClient is not None:
        monkeypatch.setattr(api.GoodinfoClient, "fetch_shareholding_history",
                            lambda self, *a, **kw: sh_df)


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------

class TestBuyScoreEndpoint:

    def test_requires_token(self, test_client, monkeypatch) -> None:
        """Returns 401 when no API token is provided (env var must also be absent)."""
        monkeypatch.delenv("FINMIND_API_KEY", raising=False)
        r = test_client.get("/api/stocks/2330/buy_score")
        assert r.status_code == 401

    def test_response_structure(self, test_client, monkeypatch) -> None:
        """Response contains all required top-level keys for v3 payload."""
        _patch_all_finmind(monkeypatch)
        r = test_client.get("/api/stocks/2330/buy_score?token=test")
        assert r.status_code == 200
        payload = r.json()

        for key in (
            "stock_id", "score", "max_score", "signal", "signal_label", "criteria", "warnings",
            "pass_rate", "eligible_count", "recommendation", "recommendation_label", "risk_criteria", "risk_score"
        ):
            assert key in payload, f"Missing key: {key}"

        assert "stage1_pass" not in payload, "stage1_pass should not exist in v3 payload"
        assert payload["stock_id"] == "2330"
        assert payload["max_score"] == len(payload["criteria"])
        assert payload["max_score"] >= 20
        assert isinstance(payload["criteria"], list)
        assert len(payload["criteria"]) >= 20

    def test_criteria_structure(self, test_client, monkeypatch) -> None:
        """Each criterion has the required fields (v3 uses 'weight' not 'stage')."""
        _patch_all_finmind(monkeypatch)
        r = test_client.get("/api/stocks/2330/buy_score?token=test")
        payload = r.json()
        required_fields = {"id", "label", "weight", "pass", "value", "value_label", "threshold"}
        for criterion in payload["criteria"]:
            for f in required_fields:
                assert f in criterion, f"Criterion {criterion.get('id')} missing field: {f}"
            assert "stage" not in criterion, f"Criterion {criterion.get('id')} should not have 'stage' field in v3"

    def test_fundamental_criteria_have_weight_2(self, test_client, monkeypatch) -> None:
        """Core fundamental criteria keep weight=2 in v3."""
        _patch_all_finmind(monkeypatch)
        r = test_client.get("/api/stocks/2330/buy_score?token=test")
        criteria = {c["id"]: c for c in r.json()["criteria"]}
        assert criteria["roe"]["weight"] == 2
        assert criteria["fcf"]["weight"] == 2
        assert criteria["debt_ratio"]["weight"] == 2

    def test_all_pass_gives_strong_buy(self, test_client, monkeypatch) -> None:
        """When core indicators are healthy, signal should be strong_buy."""
        _patch_all_finmind(monkeypatch, debt_pct=40.0, fcf_positive=True,
                           rev_yoy_pos=True, eps_yoy_pos=True, gm_up=True,
                           inst_net=1000.0, current_per=10.0, foreign_trend="up")
        r = test_client.get("/api/stocks/2330/buy_score?token=test")
        payload = r.json()
        assert payload["score"] <= payload["max_score"]
        assert payload["pass_rate"] >= 75.0
        assert payload["signal"] == "strong_buy"

    def test_debt_fail_does_not_block_other_criteria(self, test_client, monkeypatch) -> None:
        """High debt ratio fails debt criterion but endpoint still evaluates all others."""
        _patch_all_finmind(monkeypatch, debt_pct=65.0)
        r = test_client.get("/api/stocks/2330/buy_score?token=test")
        payload = r.json()
        assert "stage1_pass" not in payload
        assert payload["signal"] != "avoid"
        # C3 debt must fail
        debt_c = next(c for c in payload["criteria"] if c["id"] == "debt_ratio")
        assert debt_c["pass"] is False
        # All non-core criteria are still evaluated even when debt fails
        bonus = [c for c in payload["criteria"] if c["weight"] == 1]
        assert all(c["pass"] is not None for c in bonus), "All weight-1 criteria must be evaluated even when C3 fails"

    def test_roe_below_12pct_fails_criterion(self, test_client, monkeypatch) -> None:
        """ROE < 12% marks ROE criterion as failed."""
        _patch_all_finmind(monkeypatch, roe_value=8.0)
        r = test_client.get("/api/stocks/2330/buy_score?token=test")
        payload = r.json()
        roe_c = next(c for c in payload["criteria"] if c["id"] == "roe")
        assert roe_c["pass"] is False
        assert payload["score"] <= payload["max_score"]

    def test_negative_fcf_fails_criterion(self, test_client, monkeypatch) -> None:
        """FCF all-negative across 3 years marks FCF criterion as failed."""
        _patch_all_finmind(monkeypatch, fcf_positive=False)
        r = test_client.get("/api/stocks/2330/buy_score?token=test")
        payload = r.json()
        fcf_c = next(c for c in payload["criteria"] if c["id"] == "fcf")
        assert fcf_c["pass"] is False
        assert payload["score"] <= payload["max_score"]

    def test_partial_score_gives_buy_signal(self, test_client, monkeypatch) -> None:
        """Mixed fundamentals should fall into buy/watch/neutral buckets without crashing."""
        _patch_all_finmind(monkeypatch, roe_value=8.0, rev_yoy_pos=False, eps_yoy_pos=False)
        r = test_client.get("/api/stocks/2330/buy_score?token=test")
        payload = r.json()
        assert payload["score"] <= payload["max_score"]
        assert payload["signal"] in {"buy", "watch", "neutral"}

    def test_signal_watch_at_5(self, test_client, monkeypatch) -> None:
        """Weak fundamentals should not produce strong_buy signal."""
        _patch_all_finmind(monkeypatch, roe_value=8.0, fcf_positive=False,
                           rev_yoy_pos=False, eps_yoy_pos=False,
                           gm_up=False, inst_net=-500.0, current_per=10.0)
        r = test_client.get("/api/stocks/2330/buy_score?token=test")
        payload = r.json()
        assert payload["score"] <= payload["max_score"]
        assert payload["signal"] in {"watch", "neutral"}

    def test_score_does_not_exceed_max(self, test_client, monkeypatch) -> None:
        """Score is always between 0 and max_score."""
        _patch_all_finmind(monkeypatch)
        r = test_client.get("/api/stocks/2330/buy_score?token=test")
        payload = r.json()
        assert 0 <= payload["score"] <= payload["max_score"]

    def test_invalid_stock_id(self, test_client) -> None:
        """Empty stock_id after strip returns 400."""
        r = test_client.get("/api/stocks/%20/buy_score?token=test")
        assert r.status_code == 400

    def test_result_is_cached(self, test_client, monkeypatch) -> None:
        """Second call returns same result without hitting FinMind again."""
        call_count = 0

        original_ni = FinMindClient.fetch_quarterly_ni

        def counting_ni(self, *a, **kw):
            nonlocal call_count
            call_count += 1
            years = [2022, 2023, 2024, 2025]
            return _make_ni_series(years)

        _patch_all_finmind(monkeypatch)
        monkeypatch.setattr(FinMindClient, "fetch_quarterly_ni", counting_ni)

        test_client.get("/api/stocks/2330/buy_score?token=test")
        first_count = call_count
        test_client.get("/api/stocks/2330/buy_score?token=test")

        assert call_count == first_count  # no extra calls on second request


# ---------------------------------------------------------------------------
# Data accuracy tests: verify each criterion value is correctly computed
# ---------------------------------------------------------------------------

class TestBuyScoreDataAccuracy:

    def test_roe_value_in_response(self, test_client, monkeypatch) -> None:
        """The roe criterion value reflects the computed TTM ROE; v2 threshold is >12%."""
        _patch_all_finmind(monkeypatch, roe_value=23.0)
        r = test_client.get("/api/stocks/2330/buy_score?token=test")
        roe_c = next(c for c in r.json()["criteria"] if c["id"] == "roe")
        # ROE = NI_TTM / Avg_Equity × 100; with our fixtures ≈ roe_value
        assert roe_c["pass"] is True
        assert roe_c["value"] is not None
        assert roe_c["value"] > 12.0  # v2 threshold is 12% (was 15% in v1)

    def test_debt_ratio_value_in_response(self, test_client, monkeypatch) -> None:
        """Debt ratio value matches fixture (40%)."""
        _patch_all_finmind(monkeypatch, debt_pct=40.0)
        r = test_client.get("/api/stocks/2330/buy_score?token=test")
        debt_c = next(c for c in r.json()["criteria"] if c["id"] == "debt_ratio")
        assert debt_c["pass"] is True
        assert debt_c["value"] == pytest.approx(40.0, abs=0.5)

    def test_pe_median_pass_when_current_below_median(self, test_client, monkeypatch) -> None:
        """PE criterion passes when current PER < historical median."""
        _patch_all_finmind(monkeypatch, current_per=10.0)
        r = test_client.get("/api/stocks/2330/buy_score?token=test")
        pe_c = next(c for c in r.json()["criteria"] if c["id"] == "pe_median")
        assert pe_c["pass"] is True

    def test_pe_median_fail_when_current_above_median(self, test_client, monkeypatch) -> None:
        """PE criterion fails when current PER > historical median."""
        _patch_all_finmind(monkeypatch, current_per=50.0)
        r = test_client.get("/api/stocks/2330/buy_score?token=test")
        pe_c = next(c for c in r.json()["criteria"] if c["id"] == "pe_median")
        assert pe_c["pass"] is False

    def test_foreign_holding_fail_when_trend_down(self, test_client, monkeypatch) -> None:
        """Foreign holding criterion fails when ratio is declining."""
        _patch_all_finmind(monkeypatch, foreign_trend="down")
        r = test_client.get("/api/stocks/2330/buy_score?token=test")
        fh_c = next(c for c in r.json()["criteria"] if c["id"] == "foreign_holding")
        assert fh_c["pass"] is False

    def test_inst_buy_fail_when_net_negative(self, test_client, monkeypatch) -> None:
        """Institutional criterion fails when 10-day net is negative."""
        _patch_all_finmind(monkeypatch, inst_net=-500.0)
        r = test_client.get("/api/stocks/2330/buy_score?token=test")
        inst_c = next(c for c in r.json()["criteria"] if c["id"] == "inst_buy")
        assert inst_c["pass"] is False
        assert inst_c["value"] is not None
        assert inst_c["value"] < 0


# ---------------------------------------------------------------------------
# R1 / R2 / R10 / R11
# ---------------------------------------------------------------------------

def _ytd_ocf(values: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame([
        {"quarter": q, "quarter_label": q, "operating_cf": v, "capex": 0.0, "fcf": v}
        for q, v in values.items()
    ])


def _quarterly_ni(value: float = 100.0) -> pd.Series:
    quarters = pd.to_datetime(["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"])
    return pd.Series([value] * 4, index=quarters, dtype=float)


def _risk_names(client, sid: str = "2330") -> list[str]:
    payload = client.get(f"/api/stocks/{sid}/buy_score?token=test").json()
    return [r["name"] for r in payload["risk_criteria"]]


def _set_industry(monkeypatch, industry: str) -> None:
    monkeypatch.setattr(FinMindClient, "fetch_stock_industry", lambda self, *a, **kw: industry)


class TestReceivablesRisk:

    def _with_yoy(self, monkeypatch, **yoy) -> None:
        _patch_all_finmind(monkeypatch)
        monkeypatch.setattr(FinMindClient, "fetch_inventory_and_revenue_growth", lambda self, *a, **kw: yoy)

    def test_two_quarters_far_above_revenue_trigger(self, test_client, monkeypatch) -> None:
        """2409's real figures: receivables outran revenue in both quarters."""
        self._with_yoy(monkeypatch, inv_yoy=5.0, ar_yoy=33.4, rev_yoy=2.4, ar_yoy_prev=32.3, rev_yoy_prev=-4.3)
        assert "應收帳款異常膨脹" in _risk_names(test_client)

    def test_one_quarter_alone_does_not_trigger(self, test_client, monkeypatch) -> None:
        """2330's real figures: +86.7% this quarter, but only 12.9 points ahead the one before."""
        self._with_yoy(monkeypatch, inv_yoy=5.0, ar_yoy=86.7, rev_yoy=36.0, ar_yoy_prev=48.0, rev_yoy_prev=35.1)
        assert "應收帳款異常膨脹" not in _risk_names(test_client)

    def test_earlier_quarter_alone_does_not_trigger(self, test_client, monkeypatch) -> None:
        self._with_yoy(monkeypatch, inv_yoy=5.0, ar_yoy=10.0, rev_yoy=10.0, ar_yoy_prev=60.0, rev_yoy_prev=10.0)
        assert "應收帳款異常膨脹" not in _risk_names(test_client)

    def test_missing_previous_quarter_does_not_trigger(self, test_client, monkeypatch) -> None:
        self._with_yoy(monkeypatch, inv_yoy=5.0, ar_yoy=60.0, rev_yoy=10.0, ar_yoy_prev=None, rev_yoy_prev=None)
        assert "應收帳款異常膨脹" not in _risk_names(test_client)

    def test_receivable_growth_in_line_with_revenue_does_not_trigger(self, test_client, monkeypatch) -> None:
        self._with_yoy(monkeypatch, inv_yoy=5.0, ar_yoy=10.0, rev_yoy=10.0, ar_yoy_prev=10.0, rev_yoy_prev=10.0)
        assert "應收帳款異常膨脹" not in _risk_names(test_client)

    def test_fast_receivables_matching_fast_revenue_does_not_trigger(self, test_client, monkeypatch) -> None:
        self._with_yoy(monkeypatch, inv_yoy=5.0, ar_yoy=60.0, rev_yoy=50.0, ar_yoy_prev=60.0, rev_yoy_prev=50.0)
        assert "應收帳款異常膨脹" not in _risk_names(test_client)

    def test_missing_receivables_neither_triggers_nor_errors(self, test_client, monkeypatch) -> None:
        self._with_yoy(monkeypatch, inv_yoy=5.0, ar_yoy=None, rev_yoy=10.0)
        assert "應收帳款異常膨脹" not in _risk_names(test_client)

    def test_missing_inventory_still_checks_receivables(self, test_client, monkeypatch) -> None:
        self._with_yoy(monkeypatch, inv_yoy=None, ar_yoy=60.0, rev_yoy=10.0, ar_yoy_prev=60.0, rev_yoy_prev=10.0)
        assert _risk_names(test_client) == ["應收帳款異常膨脹"]

    def test_banks_are_exempt(self, test_client, monkeypatch) -> None:
        self._with_yoy(monkeypatch, inv_yoy=5.0, ar_yoy=60.0, rev_yoy=10.0, ar_yoy_prev=60.0, rev_yoy_prev=10.0)
        _set_industry(monkeypatch, "金融保險業")
        assert "應收帳款異常膨脹" not in _risk_names(test_client, "2891")


class TestReceivablesYoy:

    def _growth(self, monkeypatch, receivables: list[float], revenue: list[float]) -> dict:
        quarters = pd.date_range("2025-03-31", periods=len(receivables), freq="QE")

        def frame(kind: str, values: list[float]) -> pd.DataFrame:
            return pd.DataFrame({"date": quarters, "type": kind, "value": values})

        monkeypatch.setattr(FinMindClient, "fetch_balance_sheet",
                            lambda self, *a, **kw: frame("AccountsReceivableNet", receivables))
        monkeypatch.setattr(FinMindClient, "fetch_financial_statements",
                            lambda self, *a, **kw: frame("Revenue", revenue))
        return FinMindClient(api_key="t").fetch_inventory_and_revenue_growth("2330", date(2024, 1, 1), date(2026, 11, 1))

    def test_reports_both_of_the_last_two_quarters(self, monkeypatch) -> None:
        result = self._growth(monkeypatch, [100, 100, 100, 100, 200, 150], [100, 100, 100, 100, 110, 120])
        assert result["ar_yoy"] == pytest.approx(50.0)
        assert result["ar_yoy_prev"] == pytest.approx(100.0)
        assert result["rev_yoy"] == pytest.approx(20.0)
        assert result["rev_yoy_prev"] == pytest.approx(10.0)

    def test_five_quarters_only_cover_the_latest(self, monkeypatch) -> None:
        result = self._growth(monkeypatch, [100, 100, 100, 100, 200], [100, 100, 100, 100, 110])
        assert result["ar_yoy"] == pytest.approx(100.0)
        assert result["ar_yoy_prev"] is None


class TestOcfDivergenceRisk:

    def _with_ocf(self, monkeypatch, ni: pd.Series, ytd: dict[str, float]) -> None:
        _patch_all_finmind(monkeypatch)
        monkeypatch.setattr(FinMindClient, "fetch_quarterly_ni", lambda self, *a, **kw: ni)
        monkeypatch.setattr(FinMindClient, "fetch_quarterly_fcf_data", lambda self, *a, **kw: _ytd_ocf(ytd))

    def test_two_weak_quarters_trigger(self, test_client, monkeypatch) -> None:
        # Single-quarter OCF of 50 against net income of 100.
        self._with_ocf(monkeypatch, _quarterly_ni(), {
            "2025-03-31": 100.0, "2025-06-30": 200.0, "2025-09-30": 250.0, "2025-12-31": 300.0,
        })
        assert "營業現金流與獲利背離" in _risk_names(test_client)

    def test_cumulative_values_are_not_read_as_quarterly(self, test_client, monkeypatch) -> None:
        """Raw YTD 250/300 would look healthy; the real quarters are 50 each."""
        self._with_ocf(monkeypatch, _quarterly_ni(), {
            "2025-03-31": 100.0, "2025-06-30": 200.0, "2025-09-30": 250.0, "2025-12-31": 300.0,
        })
        payload = test_client.get("/api/stocks/2330/buy_score?token=test").json()
        risk = next(r for r in payload["risk_criteria"] if r["name"] == "營業現金流與獲利背離")
        assert risk["value_label"] == "OCF/淨利 0.50"

    def test_one_weak_quarter_does_not_trigger(self, test_client, monkeypatch) -> None:
        self._with_ocf(monkeypatch, _quarterly_ni(), {
            "2025-03-31": 100.0, "2025-06-30": 200.0, "2025-09-30": 300.0, "2025-12-31": 350.0,
        })
        assert "營業現金流與獲利背離" not in _risk_names(test_client)

    def test_healthy_cash_flow_does_not_trigger(self, test_client, monkeypatch) -> None:
        self._with_ocf(monkeypatch, _quarterly_ni(), {
            "2025-03-31": 100.0, "2025-06-30": 200.0, "2025-09-30": 300.0, "2025-12-31": 400.0,
        })
        assert "營業現金流與獲利背離" not in _risk_names(test_client)

    def test_insufficient_quarters_warn_instead_of_trigger(self, test_client, monkeypatch) -> None:
        self._with_ocf(monkeypatch, _quarterly_ni(), {"2025-12-31": 100.0})
        payload = test_client.get("/api/stocks/2330/buy_score?token=test").json()
        assert "營業現金流與獲利背離" not in [r["name"] for r in payload["risk_criteria"]]
        assert "ocf_ni: 季度資料不足" in payload["warnings"]

    def test_banks_are_exempt(self, test_client, monkeypatch) -> None:
        self._with_ocf(monkeypatch, _quarterly_ni(), {
            "2025-03-31": 100.0, "2025-06-30": 200.0, "2025-09-30": 250.0, "2025-12-31": 300.0,
        })
        _set_industry(monkeypatch, "金融保險業")
        assert "營業現金流與獲利背離" not in _risk_names(test_client, "2891")


class TestMopsNewsRisk:

    def test_auditor_change_adds_a_governance_risk(self, test_client, monkeypatch) -> None:
        _patch_all_finmind(monkeypatch)
        monkeypatch.setattr(api, "fetch_major_news", lambda sid: [
            {"date": "2026-02-01", "title": "公告本公司更換會計師"},
        ])
        payload = test_client.get("/api/stocks/2330/buy_score?token=test").json()
        risk = next(r for r in payload["risk_criteria"] if r["category"] == "治理")
        assert risk["name"] == "近一年更換會計師"
        assert risk["value_label"] == "2026-02-01"
        assert risk["description"] == "公告本公司更換會計師"

    def test_filing_delay_adds_a_governance_risk(self, test_client, monkeypatch) -> None:
        _patch_all_finmind(monkeypatch)
        monkeypatch.setattr(api, "fetch_major_news", lambda sid: [
            {"date": "2026-02-01", "title": "本公司延後公告財務報告"},
        ])
        assert "財報延遲申報" in _risk_names(test_client)

    def test_clean_news_adds_nothing(self, test_client, monkeypatch) -> None:
        _patch_all_finmind(monkeypatch)
        monkeypatch.setattr(api, "fetch_major_news", lambda sid: [
            {"date": "2026-02-01", "title": "公告本公司2026年1月合併營業額"},
        ])
        assert _risk_names(test_client) == []

    def test_unreadable_mops_is_reported_not_treated_as_clean(self, test_client, monkeypatch) -> None:
        from datasource_mops_news import MopsNewsError

        def blocked(sid):
            raise MopsNewsError("MOPS request failed: ValueError")

        _patch_all_finmind(monkeypatch)
        monkeypatch.setattr(api, "fetch_major_news", blocked)
        r = test_client.get("/api/stocks/2330/buy_score?token=test")
        assert r.status_code == 200
        payload = r.json()
        assert payload["risk_criteria"] == []
        assert "mops_news: MOPS request failed: ValueError" in payload["warnings"]

    def test_slow_mops_does_not_hang_buy_score(self, test_client, monkeypatch) -> None:
        release = threading.Event()
        _patch_all_finmind(monkeypatch)
        monkeypatch.setattr(api, "_MOPS_NEWS_WAIT_SECONDS", 0.05)
        monkeypatch.setattr(api, "fetch_major_news", lambda sid: release.wait(5) and [])
        try:
            payload = test_client.get("/api/stocks/2330/buy_score?token=test").json()
        finally:
            release.set()
        assert "mops_news: timeout" in payload["warnings"]


class TestDecumulateYtd:

    def _ytd(self, values: dict[str, float]) -> pd.Series:
        return pd.Series(list(values.values()), index=pd.to_datetime(list(values)), dtype=float)

    def test_subtracts_the_previous_quarter_within_a_year(self) -> None:
        single = decumulate_ytd(self._ytd({
            "2025-03-31": 100.0, "2025-06-30": 250.0, "2025-09-30": 300.0, "2025-12-31": 500.0,
        }))
        assert single.tolist() == [100.0, 150.0, 50.0, 200.0]

    def test_q1_restarts_the_year(self) -> None:
        single = decumulate_ytd(self._ytd({"2025-12-31": 500.0, "2026-03-31": 80.0}))
        assert single.to_dict() == {pd.Timestamp("2026-03-31"): 80.0}

    def test_gap_in_the_year_drops_the_quarter(self) -> None:
        single = decumulate_ytd(self._ytd({"2025-03-31": 100.0, "2025-09-30": 300.0}))
        assert single.index.tolist() == [pd.Timestamp("2025-03-31")]


class TestRecentOcfToIncome:

    def test_uses_the_latest_quarters_oldest_first(self) -> None:
        ni = _quarterly_ni()
        ocf = pd.Series([10.0, 20.0, 30.0, 40.0], index=ni.index)
        assert recent_ocf_to_income(ni, ocf) == [0.3, 0.4]

    def test_skips_loss_making_quarters(self) -> None:
        ni = _quarterly_ni()
        ni.iloc[-1] = -50.0
        ocf = pd.Series([10.0, 20.0, 30.0, 40.0], index=ni.index)
        assert recent_ocf_to_income(ni, ocf) == [0.2, 0.3]

    def test_too_few_quarters_gives_nothing(self) -> None:
        ni = _quarterly_ni()
        assert recent_ocf_to_income(ni.iloc[:1], ni.iloc[:1]) == []


class TestFinancialsCashFlowRisk:

    def test_negative_fcf_is_a_risk_for_ordinary_companies(self, test_client, monkeypatch) -> None:
        _patch_all_finmind(monkeypatch, fcf_positive=False)
        assert "自由現金流轉負" in _risk_names(test_client)

    def test_negative_fcf_is_not_a_risk_for_banks(self, test_client, monkeypatch) -> None:
        _patch_all_finmind(monkeypatch, fcf_positive=False)
        _set_industry(monkeypatch, "金融保險")
        assert "自由現金流轉負" not in _risk_names(test_client, "2891")
