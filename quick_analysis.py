"""快速分析：把既有估值端點的輸出組成「安全邊際估值」與「成長期估值」並給出判定。

純函式、無 I/O。端點失敗時對應輸入為 None，該候選直接略過。

`judge_verdict` 在 static/app.js 的 `judgeVerdict` 有一份對應實作（估值先到、排雷後到，
前端要在 buy_score 到達時自行更新判定）。兩邊以 tests/fixtures/quick_verdict_cases.json
比對，改判定表時兩邊要一起改。
"""
from __future__ import annotations

from statistics import median
from typing import Any, Optional

# PEG = 1 的合理本益比上限：避免短期高成長把成長估值推到不合理的倍數。
PEG_MAX_FAIR_PE = 30.0


def _positive(value: Any) -> Optional[float]:
    """Return value as a float if it is a real number > 0, else None (NaN included)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _latest_eps_ttm(pe_river: Optional[dict]) -> Optional[float]:
    """Most recent TTM EPS, or None when it is missing or a loss.

    Takes the newest quarter that has a value and does not fall back to an older
    positive quarter: a company that just turned loss-making must not be valued
    on last year's earnings.
    """
    rows = [r for r in (pe_river or {}).get("eps_ttm_rows") or [] if r.get("eps_ttm") is not None]
    if not rows:
        return None
    newest = max(rows, key=lambda r: str(r.get("quarter") or ""))
    return _positive(newest["eps_ttm"])


def _component(name: str, value: Optional[float], source: str) -> Optional[dict[str, Any]]:
    if value is None:
        return None
    return {"name": name, "value": round(value, 2), "source": source}


def _price(latest: Optional[dict], *fallbacks: Optional[dict]) -> Optional[float]:
    close = _positive(((latest or {}).get("price") or {}).get("close"))
    if close is not None:
        return close
    for source in fallbacks:
        current = _positive((source or {}).get("current_price"))
        if current is not None:
            return current
    return None


def _pct(numerator: float, price: float) -> float:
    return round(numerator / price * 100, 1)


def compute_summary(
    latest: Optional[dict],
    dcf: Optional[dict],
    valuation_extra: Optional[dict],
    forward: Optional[dict],
    pe_river: Optional[dict],
) -> dict[str, Any]:
    """Combine endpoint payloads into the floor/ceiling price ladder."""
    dcf = dcf or {}
    extra = valuation_extra or {}
    forward_data = forward or {}
    bands = (pe_river or {}).get("bands") or {}

    price = _price(latest, dcf, extra)
    eps_ttm = _latest_eps_ttm(pe_river)

    def pe_price(percentile: str) -> Optional[float]:
        pe = _positive(bands.get(percentile))
        return pe * eps_ttm if pe is not None and eps_ttm is not None else None

    eps_cagr = _positive(extra.get("eps_cagr"))
    peg_price = eps_ttm * min(eps_cagr, PEG_MAX_FAIR_PE) if eps_ttm and eps_cagr else None

    floor_components = [c for c in (
        _component("DCF 進場價", _positive(dcf.get("entry_price")), "dcf"),
        _component("葛拉漢數", _positive(extra.get("graham_number")), "valuation_extra"),
        _component("低檔本益比價", pe_price("p25"), "pe_river"),
    ) if c]
    ceiling_components = [c for c in (
        _component("前瞻估值（營益率）", _positive(forward_data.get("price_mid")), "forward_rolling_eps"),
        _component("前瞻估值（淨利率）", _positive(forward_data.get("price_mid_m2")), "forward_rolling_eps"),
        _component("PEG=1 合理價", peg_price, "valuation_extra"),
        _component("高檔本益比價", pe_price("p75"), "pe_river"),
    ) if c]

    floor = round(median(c["value"] for c in floor_components), 2) if floor_components else None
    ceiling = round(median(c["value"] for c in ceiling_components), 2) if ceiling_components else None

    downside_pct = _pct(floor - price, price) if price and floor else None
    upside_pct = _pct(ceiling - price, price) if price and ceiling else None
    gap_pct = _pct(ceiling - floor, price) if price and floor and ceiling else None

    position: Optional[float] = None
    if price and floor and ceiling and floor < ceiling:
        position = round(min(1.0, max(0.0, (price - floor) / (ceiling - floor))), 4)

    return {
        "price": price,
        "eps_ttm": eps_ttm,
        "floor": floor,
        "floor_components": floor_components,
        "ceiling": ceiling,
        "ceiling_components": ceiling_components,
        "downside_pct": downside_pct,
        "upside_pct": upside_pct,
        "gap_pct": gap_pct,
        "position": position,
    }


def risk_level_of(risk_count: int) -> str:
    """Two or more warnings is high, matching buy_score's 高風險避開 downgrade."""
    if risk_count >= 2:
        return "high"
    return "medium" if risk_count == 1 else "low"


def judge_verdict(
    price: Optional[float],
    floor: Optional[float],
    ceiling: Optional[float],
    position: Optional[float],
    risk_level: Optional[str],
    risk_count: int = 0,
) -> tuple[str, str]:
    """Return (verdict, text). risk_level None means the risk check did not complete."""
    if risk_level == "high":
        return "avoid", f"偵測到 {risk_count} 項排雷警示，先避開"
    if price is None or floor is None or ceiling is None:
        return "insufficient", "估值資料不足"
    if floor >= ceiling:
        # Models disagree, so the only safe reading is outside both bounds.
        if price < min(floor, ceiling):
            return "buy", "現價低於安全邊際估值，具下檔保護"
        if price > max(floor, ceiling):
            return "wait", "現價高於成長估值，等待回檔"
        return "hold", "估值模型分歧，現價落在兩者之間"
    if price < floor:
        return "buy", "現價低於安全邊際估值，具下檔保護"
    if price > ceiling:
        return "wait", "現價高於成長估值，等待回檔"
    if position is not None and position < 0.5:
        if risk_level == "low":
            return "accumulate", "現價位於區間下半，可分批佈局"
        if risk_level == "medium":
            return "watch", "估值合理但有 1 項警示，列入觀察"
        return "watch", "估值合理，排雷資料不足，列入觀察"
    return "hold", "現價偏向成長估值，上檔有限"


def apply_risk(summary: dict[str, Any], buy_score: Optional[dict]) -> dict[str, Any]:
    """Overlay buy_score's risk result on a compute_summary() payload."""
    result = dict(summary)
    criteria = (buy_score or {}).get("risk_criteria")
    if isinstance(criteria, list):
        risk_count = int((buy_score or {}).get("risk_score") or len(criteria))
        risk_level: Optional[str] = risk_level_of(risk_count)
        risk_names = [str(item.get("name")) for item in criteria if item.get("name")]
    else:
        risk_count, risk_level, risk_names = 0, None, []

    verdict, verdict_text = judge_verdict(
        summary.get("price"), summary.get("floor"), summary.get("ceiling"),
        summary.get("position"), risk_level, risk_count,
    )
    result.update({
        "risk_level": risk_level,
        "risk_count": risk_count,
        "risk_names": risk_names,
        "pass_rate": (buy_score or {}).get("pass_rate"),
        "verdict": verdict,
        "verdict_text": verdict_text,
    })
    return result
