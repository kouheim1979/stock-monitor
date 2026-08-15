"""Dependency-free signal smoothing and daily technical indicators."""

from dataclasses import dataclass
from math import sqrt


def sma(values: tuple[float, ...], period: int) -> tuple[float, ...]:
    """Return an aligned rolling simple moving average."""
    if period <= 0:
        raise ValueError("period must be positive")
    return tuple(
        sum(values[i - period + 1 : i + 1]) / period
        for i in range(period - 1, len(values))
    )


def ema(values: tuple[float, ...], period: int) -> tuple[float, ...]:
    """Return a conventional recursively smoothed EMA."""
    if period <= 0:
        raise ValueError("period must be positive")
    if not values:
        return ()
    alpha = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return tuple(result)


def _latest_sma(values: tuple[float, ...], period: int) -> float:
    """Return the requested SMA only when a full window is available."""
    series = sma(values, period)
    return series[-1] if series else 0.0


def _rsi_wilder(closes: tuple[float, ...], period: int = 14) -> float:
    """Calculate RSI using Wilder's recursive smoothing."""
    changes = tuple(b - a for a, b in zip(closes, closes[1:]))
    if not changes:
        return 50.0

    # For short warm-up input, provide a neutral/simple estimate without
    # pretending that a full RSI(period) history exists.
    if len(changes) < period:
        gains = sum(max(change, 0.0) for change in changes)
        losses = sum(max(-change, 0.0) for change in changes)
        if gains == 0 and losses == 0:
            return 50.0
        if losses == 0:
            return 100.0
        rs = gains / losses
        return 100 - 100 / (1 + rs)

    initial = changes[:period]
    avg_gain = sum(max(change, 0.0) for change in initial) / period
    avg_loss = sum(max(-change, 0.0) for change in initial) / period
    for change in changes[period:]:
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


@dataclass(frozen=True)
class TechnicalMetrics:
    ma5: float = 0
    ma25: float = 0
    ma75: float = 0
    trend: str = "NEUTRAL"
    trend_signal: float = 0
    macd: float = 0
    macd_signal: float = 0
    macd_histogram: float = 0
    macd_positive: bool = False
    macd_rising: bool = False
    histogram_rising: bool = False
    rsi14: float = 50
    bb_middle: float = 0
    bb_plus1: float = 0
    bb_minus1: float = 0
    bb_plus2: float = 0
    bb_minus2: float = 0
    bandwidth: float = 0
    volatility_expansion: bool = False
    current_volume: float = 0
    average_volume: float = 0
    volume_ratio: float = 0


def calculate_technicals(
    closes: tuple[float, ...], volumes: tuple[float, ...]
) -> TechnicalMetrics:
    """Calculate MA trend, MACD, RSI, Bollinger bands, and relative volume."""
    if not closes:
        return TechnicalMetrics()

    ma = {period: _latest_sma(closes, period) for period in (5, 25, 75)}
    slopes = {
        period: (
            ma[period] - _latest_sma(closes[:-3], period)
            if len(closes) >= period + 3
            else 0.0
        )
        for period in ma
    }

    # Do not label a 75-day trend until both the current and 3-day-prior
    # 75-day windows exist. Using a shorter substitute would make "MA75"
    # mathematically misleading during warm-up.
    trend_ready = len(closes) >= 78
    up = (
        trend_ready
        and closes[-1] > ma[5] > ma[25] > ma[75]
        and all(value > 0 for value in slopes.values())
    )
    down = (
        trend_ready
        and closes[-1] < ma[5] < ma[25] < ma[75]
        and all(value < 0 for value in slopes.values())
    )

    e12, e26 = ema(closes, 12), ema(closes, 26)
    macd_series = tuple(a - b for a, b in zip(e12, e26))
    signal_series = ema(macd_series, 9)
    histogram = tuple(a - b for a, b in zip(macd_series, signal_series))

    rsi = _rsi_wilder(closes, 14)

    if len(closes) >= 20:
        window = closes[-20:]
        middle = sum(window) / 20
        sigma = sqrt(sum((x - middle) ** 2 for x in window) / 20)
        bandwidth = 4 * sigma / middle if middle else 0.0
    else:
        middle = sigma = bandwidth = 0.0

    prior_bw = 0.0
    if len(closes) >= 40:
        prior = closes[-40:-20]
        prior_middle = sum(prior) / 20
        prior_sigma = sqrt(sum((x - prior_middle) ** 2 for x in prior) / 20)
        prior_bw = 4 * prior_sigma / prior_middle if prior_middle else 0.0

    current_volume = volumes[-1] if volumes else 0.0
    prior_volumes = volumes[-21:-1] if len(volumes) > 1 else ()
    average_volume = (
        sum(prior_volumes) / len(prior_volumes)
        if prior_volumes
        else current_volume
    )

    return TechnicalMetrics(
        ma[5],
        ma[25],
        ma[75],
        "UPTREND" if up else "DOWNTREND" if down else "NEUTRAL",
        1 if up else -1 if down else 0,
        macd_series[-1],
        signal_series[-1],
        histogram[-1],
        macd_series[-1] > 0,
        len(macd_series) > 1 and macd_series[-1] > macd_series[-2],
        len(histogram) > 1 and histogram[-1] > histogram[-2],
        rsi,
        middle,
        middle + sigma,
        middle - sigma,
        middle + 2 * sigma,
        middle - 2 * sigma,
        bandwidth,
        bool(prior_bw and bandwidth > prior_bw * 1.25),
        current_volume,
        average_volume,
        current_volume / average_volume if average_volume else 0.0,
    )
