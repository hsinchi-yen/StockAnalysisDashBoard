from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import quick_analysis as qa

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests" / "fixtures" / "quick_verdict_cases.json").read_text(encoding="utf-8"))


def _inputs(**overrides):
    """Endpoint payloads for a stock at NT$120 with EPS TTM 5."""
    data = {
        "latest": {"price": {"close": 120.0}},
        "dcf": {"entry_price": 90.0, "current_price": 120.0},
        "valuation_extra": {"graham_number": 100.0, "eps_cagr": 40.0, "current_price": 120.0},
        "forward": {"price_mid": 150.0, "price_mid_m2": 170.0},
        "pe_river": {
            "bands": {"p25": 22.0, "p75": 35.0},
            "eps_ttm_rows": [
                {"quarter": "2025-12-31", "eps_ttm": 4.0},
                {"quarter": "2026-03-31", "eps_ttm": 5.0},
            ],
        },
    }
    data.update(overrides)
    return data


def _summary(**overrides):
    return qa.compute_summary(**_inputs(**overrides))


class TestFloor:
    def test_three_candidates_use_the_median(self) -> None:
        s = _summary()
        assert [c["value"] for c in s["floor_components"]] == [90.0, 100.0, 110.0]
        assert s["floor"] == 100.0

    def test_single_candidate_is_the_floor(self) -> None:
        s = _summary(valuation_extra=None, pe_river=None, dcf={"entry_price": 90.0})
        assert s["floor"] == 90.0

    def test_no_candidates_gives_null_floor_and_insufficient(self) -> None:
        s = _summary(dcf=None, valuation_extra=None, pe_river=None)
        assert s["floor"] is None
        assert s["position"] is None
        assert qa.apply_risk(s, {"risk_criteria": [], "risk_score": 0})["verdict"] == "insufficient"

    def test_non_positive_candidates_are_skipped(self) -> None:
        s = _summary(dcf={"entry_price": -5.0}, valuation_extra={"graham_number": 0}, pe_river=None)
        assert s["floor"] is None


class TestCeiling:
    def test_four_candidates_use_the_median_and_cap_peg_pe(self) -> None:
        s = _summary()
        values = {c["name"]: c["value"] for c in s["ceiling_components"]}
        # eps_cagr 40 is capped to PE 30, so PEG price is 5 × 30 rather than 5 × 40.
        assert values["PEG=1 合理價"] == 150.0
        assert values["高檔本益比價"] == 175.0
        assert s["ceiling"] == 160.0

    def test_eps_decline_drops_the_peg_candidate(self) -> None:
        s = _summary(valuation_extra={"graham_number": 100.0, "eps_cagr": -8.0})
        assert "PEG=1 合理價" not in [c["name"] for c in s["ceiling_components"]]


class TestEpsTtm:
    def test_uses_the_newest_quarter_regardless_of_row_order(self) -> None:
        river = {"bands": {}, "eps_ttm_rows": [
            {"quarter": "2026-03-31", "eps_ttm": 5.0},
            {"quarter": "2025-12-31", "eps_ttm": 4.0},
        ]}
        assert _summary(pe_river=river)["eps_ttm"] == 5.0

    def test_recent_loss_does_not_fall_back_to_older_profit(self) -> None:
        river = {"bands": {"p25": 22.0, "p75": 35.0}, "eps_ttm_rows": [
            {"quarter": "2025-12-31", "eps_ttm": 4.0},
            {"quarter": "2026-03-31", "eps_ttm": -1.0},
        ]}
        s = _summary(pe_river=river)
        assert s["eps_ttm"] is None
        assert "低檔本益比價" not in [c["name"] for c in s["floor_components"]]


class TestDerivedFields:
    def test_percentages_and_position(self) -> None:
        s = _summary()
        assert s["downside_pct"] == pytest.approx(-16.7)
        assert s["upside_pct"] == pytest.approx(33.3)
        assert s["gap_pct"] == pytest.approx(50.0)
        assert s["position"] == pytest.approx(1 / 3, abs=1e-4)

    def test_position_clamps_outside_the_range(self) -> None:
        assert _summary(latest={"price": {"close": 50.0}})["position"] == 0.0
        assert _summary(latest={"price": {"close": 500.0}})["position"] == 1.0

    def test_inverted_range_has_no_position(self) -> None:
        s = _summary(forward={"price_mid": 40.0, "price_mid_m2": 50.0}, pe_river=None,
                     valuation_extra={"graham_number": 100.0})
        assert s["floor"] >= s["ceiling"]
        assert s["position"] is None

    def test_price_falls_back_when_latest_failed(self) -> None:
        assert _summary(latest=None)["price"] == 120.0

    def test_all_inputs_missing_does_not_raise(self) -> None:
        s = qa.compute_summary(None, None, None, None, None)
        assert s["price"] is None and s["floor"] is None and s["ceiling"] is None


class TestApplyRisk:
    @pytest.mark.parametrize("count,level", [(0, "low"), (1, "medium"), (2, "high"), (5, "high")])
    def test_risk_levels(self, count, level) -> None:
        assert qa.risk_level_of(count) == level

    def test_overlays_buy_score_fields(self) -> None:
        result = qa.apply_risk(_summary(), {
            "risk_score": 1,
            "risk_criteria": [{"name": "連續虧損"}],
            "pass_rate": 0.7,
        })
        assert result["risk_level"] == "medium"
        assert result["risk_names"] == ["連續虧損"]
        assert result["pass_rate"] == 0.7
        assert result["verdict"] == "watch"

    def test_failed_buy_score_leaves_risk_unknown(self) -> None:
        result = qa.apply_risk(_summary(), None)
        assert result["risk_level"] is None
        assert result["verdict_text"] == "估值合理，排雷資料不足，列入觀察"


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_verdict_table(case) -> None:
    verdict, text = qa.judge_verdict(
        case["price"], case["floor"], case["ceiling"], case["position"],
        case["risk_level"], case["risk_count"],
    )
    assert (verdict, text) == (case["verdict"], case["text"])


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_frontend_verdict_table_matches_backend() -> None:
    """static/app.js carries a copy of judge_verdict; both must agree on every case."""
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    table = re.search(r"// <quick-verdict>(.*?)// </quick-verdict>", source, re.S)
    assert table, "verdict table markers missing from static/app.js"
    script = table.group(1) + """
const cases = JSON.parse(require("fs").readFileSync(0, "utf8"));
console.log(JSON.stringify(cases.map((c) =>
  judgeVerdict(c.price, c.floor, c.ceiling, c.position, c.risk_level, c.risk_count))));
"""
    done = subprocess.run(
        ["node", "-e", script], input=json.dumps(CASES), capture_output=True,
        text=True, encoding="utf-8", timeout=30, check=True,
    )
    assert json.loads(done.stdout) == [[c["verdict"], c["text"]] for c in CASES]
