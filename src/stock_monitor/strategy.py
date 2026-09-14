"""Explainable technical decision layer for the stock dashboard.

The module intentionally avoids broker actions and price predictions.  It turns
long-term moving-average analysis into a repeatable score, risk snapshot and
reference limit-price ladder that can be explained in the UI.
"""

from __future__ import annotations

from math import sqrt
from statistics import pstdev
from typing import Any


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _pct_change(new: float, old: float) -> float:
    return (new / old - 1.0) * 100.0 if old else 0.0


def _round_yen(value: float) -> int:
    """Round to a conservative ¥1 reference price.

    TSE tick sizes vary by security and price band.  The dashboard therefore
    labels these as reference prices rather than executable orders.
    """

    return max(1, int(round(value)))


def _decision_label(score: int, stretched: bool, falling_knife: bool) -> tuple[str, str]:
    if falling_knife:
        return "E", "下降加速・見送り"
    if score >= 80 and not stretched:
        return "A", "強い候補・押し目を狙う"
    if score >= 68:
        return "B", "分割買い候補"
    if score >= 55:
        return "C", "監視・条件待ち"
    if score >= 42:
        return "D", "弱い・見送り優先"
    return "E", "下降優勢・見送り"


def build_strategy(analysis: dict[str, Any]) -> dict[str, Any]:
    """Build an explainable technical decision snapshot from MA analysis."""

    price = float(analysis.get("analysis_price") or analysis.get("price") or 0.0)
    if price <= 0:
        raise ValueError("analysis price must be positive")

    moving = analysis.get("moving_averages") or {}
    required = ("25", "75", "125", "200")
    if not all(period in moving for period in required):
        raise ValueError("moving averages 25/75/125/200 are required")

    ma = {period: float(moving[period]["value"]) for period in required}
    slopes = {period: float(moving[period].get("slope_5d_pct", 0.0)) for period in required}

    history = [row for row in analysis.get("history", []) if row.get("close") is not None]
    closes = [float(row["close"]) for row in history if float(row["close"]) > 0]
    returns = [_pct_change(closes[i], closes[i - 1]) / 100.0 for i in range(1, len(closes))]
    recent_returns = returns[-20:]
    daily_sigma_pct = pstdev(recent_returns) * 100.0 if len(recent_returns) >= 2 else 1.5
    annualized_vol_pct = daily_sigma_pct * sqrt(252.0)

    high60 = max(closes[-60:]) if closes else price
    low60 = min(closes[-60:]) if closes else price
    return20_pct = _pct_change(closes[-1], closes[-21]) if len(closes) >= 21 else 0.0
    return60_pct = _pct_change(closes[-1], closes[-61]) if len(closes) >= 61 else 0.0
    drawdown60_pct = _pct_change(price, high60)
    rebound_from_low60_pct = _pct_change(price, low60)

    grade = int(analysis.get("grade", 3))
    grade_points = {1: -32.0, 2: -20.0, 3: 0.0, 4: 22.0, 5: 32.0}.get(grade, 0.0)

    slope_points = sum(_clamp(slopes[p] * 2.2, -5.0, 5.0) for p in required)

    alignment_points = 0.0
    if price > ma["25"]:
        alignment_points += 4.0
    if ma["25"] > ma["75"]:
        alignment_points += 5.0
    if ma["75"] > ma["125"]:
        alignment_points += 4.0
    if ma["125"] > ma["200"]:
        alignment_points += 5.0
    if price < ma["200"]:
        alignment_points -= 10.0

    distance25 = _pct_change(price, ma["25"])
    distance75 = _pct_change(price, ma["75"])
    stretched = distance25 > 10.0
    stretch_points = 0.0
    if 0.0 <= distance25 <= 4.0:
        stretch_points += 7.0
    elif -3.0 <= distance25 < 0.0 and price > ma["75"]:
        stretch_points += 9.0
    elif distance25 > 10.0:
        stretch_points -= min(15.0, (distance25 - 10.0) * 1.5 + 5.0)
    elif distance25 < -8.0:
        stretch_points -= 8.0

    momentum_points = _clamp(return20_pct * 0.65, -10.0, 10.0)
    volatility_penalty = max(0.0, (annualized_vol_pct - 35.0) * 0.28)
    falling_knife = grade <= 2 and return20_pct < -8.0 and slopes["25"] < 0 and slopes["75"] < 0

    raw_score = 50.0 + grade_points + slope_points + alignment_points + stretch_points + momentum_points - volatility_penalty
    if falling_knife:
        raw_score = min(raw_score, 34.0)
    score = int(round(_clamp(raw_score, 0.0, 100.0)))
    rank, label = _decision_label(score, stretched, falling_knife)

    sigma = max(0.8, min(daily_sigma_pct, 5.0)) / 100.0
    shallow_drop = max(0.008, 0.65 * sigma)
    core_drop = max(0.018, 1.20 * sigma)
    deep_drop = max(0.035, 2.15 * sigma)

    aggressive = price * (1.0 - shallow_drop)
    standard = price * (1.0 - core_drop)
    deep = price * (1.0 - deep_drop)

    if 0 < ma["25"] < price and distance25 <= 8.0:
        standard = min(standard, ma["25"] * 1.003)
    if 0 < ma["75"] < price:
        deep = min(deep, ma["75"] * 1.003)

    ladder = sorted({_round_yen(aggressive), _round_yen(standard), _round_yen(deep)}, reverse=True)
    while len(ladder) < 3:
        ladder.append(max(1, ladder[-1] - max(1, _round_yen(price * 0.01))))

    invalidation_base = ma["75"] * 0.98 if ma["75"] < price else ma["200"] * 0.98
    volatility_stop = price * (1.0 - max(0.055, 3.0 * sigma))
    invalidation = _round_yen(min(invalidation_base, volatility_stop))
    review_upside = _round_yen(price * (1.0 + max(0.07, 3.5 * sigma)))

    reasons: list[dict[str, Any]] = []
    reasons.append({"name": "長期トレンド", "impact": round(grade_points, 1), "detail": analysis.get("label", "")})
    reasons.append({"name": "移動平均の傾き", "impact": round(slope_points, 1), "detail": f"MA25 {slopes['25']:+.2f}% / MA75 {slopes['75']:+.2f}%"})
    reasons.append({"name": "並び順", "impact": round(alignment_points, 1), "detail": str(analysis.get("order", ""))})
    reasons.append({"name": "買われ過ぎ/押し目", "impact": round(stretch_points, 1), "detail": f"株価-MA25 {distance25:+.1f}%"})
    reasons.append({"name": "20日モメンタム", "impact": round(momentum_points, 1), "detail": f"20日 {return20_pct:+.1f}%"})
    if volatility_penalty:
        reasons.append({"name": "変動率ペナルティ", "impact": round(-volatility_penalty, 1), "detail": f"年率換算 {annualized_vol_pct:.1f}%"})

    risk_level = "低め" if annualized_vol_pct < 22 else "中" if annualized_vol_pct < 38 else "高め"
    heat = "過熱" if distance25 > 10 else "やや過熱" if distance25 > 6 else "押し目" if distance25 < 0 and price > ma["75"] else "通常"

    return {
        "score": score,
        "rank": rank,
        "label": label,
        "heat": heat,
        "stretched": stretched,
        "falling_knife": falling_knife,
        "risk_level": risk_level,
        "daily_sigma_pct": round(daily_sigma_pct, 2),
        "annualized_vol_pct": round(annualized_vol_pct, 1),
        "return20_pct": round(return20_pct, 2),
        "return60_pct": round(return60_pct, 2),
        "drawdown60_pct": round(drawdown60_pct, 2),
        "rebound_from_low60_pct": round(rebound_from_low60_pct, 2),
        "distance_ma25_pct": round(distance25, 2),
        "distance_ma75_pct": round(distance75, 2),
        "limit_prices": {
            "aggressive": ladder[0],
            "standard": ladder[1],
            "deep": ladder[2],
        },
        "invalidation_price": invalidation,
        "review_upside_price": review_upside,
        "reasons": reasons,
        "method": "MA25/75/125/200 + 20日momentum + 20日volatility。テクニカル参考値であり将来収益を保証しません。",
    }


def enrich_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(analysis)
    enriched["decision"] = build_strategy(analysis)
    return enriched
