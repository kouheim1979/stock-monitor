from __future__ import annotations

from dataclasses import dataclass

from .orderflow import OrderFlowResult


@dataclass(frozen=True)
class PressureScore:
    score: float
    state: str
    book_imbalance: float
    trade_flow: float
    replenishment_bias: float
    cancellation_bias: float


def _ratio(a: float, b: float) -> float:
    total = a + b
    if total == 0:
        return 0.0
    return (a - b) / total


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def calculate_pressure_score(result: OrderFlowResult) -> PressureScore:
    """Convert order-flow observations to an interpretable 0-100 score.

    50 is neutral. The weights are deliberately explicit so they can later be
    calibrated against historical data instead of treated as magic constants.
    """

    book_imbalance = _ratio(result.bid_depth, result.ask_depth)
    trade_flow = _ratio(result.aggressive_buy_volume, result.aggressive_sell_volume)
    replenishment_bias = _ratio(
        result.total_bid_replenishment,
        result.total_ask_replenishment,
    )

    # Ask cancellations reduce nearby selling interest (bullish); bid
    # cancellations reduce nearby buying interest (bearish).
    cancellation_bias = _ratio(
        result.total_ask_cancellation,
        result.total_bid_cancellation,
    )

    raw = (
        0.30 * book_imbalance
        + 0.40 * trade_flow
        + 0.20 * replenishment_bias
        + 0.10 * cancellation_bias
    )
    score = 50.0 + 50.0 * _clamp(raw, -1.0, 1.0)

    if score >= 70:
        state = "strong_buy_pressure"
    elif score >= 58:
        state = "buy_pressure"
    elif score <= 30:
        state = "strong_sell_pressure"
    elif score <= 42:
        state = "sell_pressure"
    else:
        state = "neutral"

    return PressureScore(
        score=round(score, 1),
        state=state,
        book_imbalance=round(book_imbalance, 4),
        trade_flow=round(trade_flow, 4),
        replenishment_bias=round(replenishment_bias, 4),
        cancellation_bias=round(cancellation_bias, 4),
    )
