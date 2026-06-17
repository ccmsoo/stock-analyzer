"""profitability_score 및 진입 제외 평가
==========================================
50점에서 시작해 가점/감점으로 0~100점을 산출한다. 모든 임계값과 가중치는 상단
상수로 분리해 튜닝 용이.

이 점수는 매수 추천이 아니라 **관찰 우선순위**다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


# ───────────────────────── 점수 가중치 ─────────────────────────

BASE_SCORE = 50

# 가점
P_CONF_HIGH = 12
P_CONF_MEDIUM = 6
P_TRIGGER_STRONG = 10           # disclosure/earnings/contract/policy
P_CHART_80 = 12
P_CHART_70 = 8
P_BREAKOUT_20 = 6
P_BREAKOUT_60 = 8
P_VALUE_RATIO_3 = 8
P_CLOSE_POSITION_70 = 5

# 감점
N_CONF_LOW = 25
N_TRIGGER_UNKNOWN = 20
N_ENTRY_RISK_HIGH = 20
N_RSI_OVERHEAT = 10             # RSI >= 80
N_UPPER_SHADOW_45 = 12
N_DISTANCE_MA20_25 = 12
N_CHART_BELOW_60 = 15
N_VALUE_RATIO_LOW = 10          # < 1.5

# 라벨 컷오프
LABEL_STRONG = 80
LABEL_WATCH = 65
LABEL_NEUTRAL = 50
LABEL_WEAK = 35


# ───────────────────────── 진입 제외 조건 ─────────────────────────

EXCLUDE_CONFIDENCE = {"low"}
EXCLUDE_TRIGGER = {"unknown"}
EXCLUDE_ENTRY_RISK = {"high", "extreme"}
EXCLUDE_CHART_SCORE_BELOW = 60
EXCLUDE_VALUE_RATIO_BELOW = 1.5
EXCLUDE_UPPER_SHADOW_ABOVE = 45.0
EXCLUDE_DISTANCE_MA20_ABOVE = 25.0


# ───────────────────────── 데이터 클래스 ─────────────────────────


@dataclass
class ScoringInput:
    confidence: str = ""
    trigger_type: str = ""
    chart_score: int | None = None
    entry_risk: str = ""
    rsi14: float | None = None
    value_ratio_20d: float | None = None
    distance_ma20_pct: float | None = None
    upper_shadow_pct: float | None = None
    close_position_pct: float | None = None
    high_20d_breakout: bool = False
    high_60d_breakout: bool = False


# ───────────────────────── 점수 계산 ─────────────────────────


def _label(score: int) -> str:
    if score >= LABEL_STRONG:
        return "strong_watch"
    if score >= LABEL_WATCH:
        return "watch"
    if score >= LABEL_NEUTRAL:
        return "neutral"
    if score >= LABEL_WEAK:
        return "weak"
    return "avoid"


def compute(inp: ScoringInput) -> dict:
    """50점 시작 + 가점/감점 누적. 0~100 clamp.

    Returns
    -------
    dict
        ``{profitability_score, label, breakdown}``
        ``breakdown`` 은 ``{rule_name: delta}`` (양수=가점, 음수=감점)
    """
    score = BASE_SCORE
    breakdown: dict[str, float] = {}

    def _add(name: str, delta: float) -> None:
        nonlocal score
        score += delta
        breakdown[name] = round(delta, 1)

    conf = (inp.confidence or "").lower()
    trig = (inp.trigger_type or "").lower()
    risk = (inp.entry_risk or "").lower()

    # 가점 ─────────────────────────────────
    if conf == "high":
        _add("conf_high", P_CONF_HIGH)
    elif conf == "medium":
        _add("conf_medium", P_CONF_MEDIUM)

    if trig in ("disclosure", "earnings", "contract", "policy"):
        _add("trigger_strong", P_TRIGGER_STRONG)

    if inp.chart_score is not None:
        if inp.chart_score >= 80:
            _add("chart_80", P_CHART_80)
        elif inp.chart_score >= 70:
            _add("chart_70", P_CHART_70)

    if inp.high_60d_breakout:
        _add("breakout_60d", P_BREAKOUT_60)
    elif inp.high_20d_breakout:
        _add("breakout_20d", P_BREAKOUT_20)

    if inp.value_ratio_20d is not None and inp.value_ratio_20d >= 3:
        _add("value_ratio_3x", P_VALUE_RATIO_3)

    if inp.close_position_pct is not None and inp.close_position_pct >= 70:
        _add("close_pos_70", P_CLOSE_POSITION_70)

    # 감점 ─────────────────────────────────
    if conf == "low":
        _add("conf_low", -N_CONF_LOW)
    if trig == "unknown":
        _add("trigger_unknown", -N_TRIGGER_UNKNOWN)
    if risk in EXCLUDE_ENTRY_RISK:
        _add("entry_risk_high", -N_ENTRY_RISK_HIGH)
    if inp.rsi14 is not None and inp.rsi14 >= 80:
        _add("rsi_overheat", -N_RSI_OVERHEAT)
    if inp.upper_shadow_pct is not None and inp.upper_shadow_pct >= 45:
        _add("upper_shadow", -N_UPPER_SHADOW_45)
    if inp.distance_ma20_pct is not None and inp.distance_ma20_pct >= 25:
        _add("distance_ma20", -N_DISTANCE_MA20_25)
    if inp.chart_score is not None and inp.chart_score < 60:
        _add("chart_below_60", -N_CHART_BELOW_60)
    if inp.value_ratio_20d is not None and inp.value_ratio_20d < 1.5:
        _add("value_ratio_low", -N_VALUE_RATIO_LOW)

    score = max(0, min(100, score))
    return {
        "profitability_score": int(round(score)),
        "label": _label(int(round(score))),
        "breakdown": breakdown,
    }


# ───────────────────────── 진입 제외 평가 ─────────────────────────


def is_strategy_eligible(trade: dict) -> tuple[bool, list[str]]:
    """룰 기반 전략 진입 가능 여부 + 제외 사유 목록.

    ``trade`` 는 backtest.BacktestTrade 의 부분 필드를 dict 로 받음.
    """
    reasons: list[str] = []
    conf = (trade.get("confidence") or "").lower()
    trig = (trade.get("trigger_type") or "").lower()
    risk = (trade.get("entry_risk") or "").lower()
    cs = trade.get("chart_score")
    vr = trade.get("value_ratio_20d")
    us = trade.get("upper_shadow_pct")
    dm = trade.get("distance_ma20_pct")

    if conf in EXCLUDE_CONFIDENCE:
        reasons.append("confidence_low")
    if trig in EXCLUDE_TRIGGER:
        reasons.append("trigger_unknown")
    if risk in EXCLUDE_ENTRY_RISK:
        reasons.append("entry_risk_high")
    if cs is not None and cs < EXCLUDE_CHART_SCORE_BELOW:
        reasons.append("chart_score_low")
    if vr is not None and vr < EXCLUDE_VALUE_RATIO_BELOW:
        reasons.append("value_ratio_low")
    if us is not None and us >= EXCLUDE_UPPER_SHADOW_ABOVE:
        reasons.append("upper_shadow_overheat")
    if dm is not None and dm >= EXCLUDE_DISTANCE_MA20_ABOVE:
        reasons.append("distance_ma20_overheat")

    return (len(reasons) == 0, reasons)
