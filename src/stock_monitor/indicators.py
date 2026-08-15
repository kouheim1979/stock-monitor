"""Dependency-free signal smoothing and daily technical indicators."""

from dataclasses import dataclass
from math import sqrt


def sma(values: tuple[float, ...], period: int) -> tuple[float, ...]:
    """Return an aligned rolling simple moving average."""
    if period <= 0:
        raise ValueError("period must be positive")
    return tuple(sum(values[i-period+1:i+1])/period for i in range(period-1, len(values)))


def ema(values: tuple[float, ...], period: int) -> tuple[float, ...]:
    """Return a conventional recursively smoothed EMA."""
    if period <= 0:
        raise ValueError("period must be positive")
    if not values:
        return ()
    alpha = 2/(period+1)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha*value + (1-alpha)*result[-1])
    return tuple(result)


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


def calculate_technicals(closes: tuple[float, ...], volumes: tuple[float, ...]) -> TechnicalMetrics:
    """Calculate MA trend, MACD, RSI, Bollinger bands, and relative volume."""
    if not closes:
        return TechnicalMetrics()
    ma = {p: (sma(closes, min(p, len(closes)))[-1]) for p in (5, 25, 75)}
    slopes = {p: ma[p] - (sma(closes[:-3], min(p, max(len(closes)-3, 1)))[-1] if len(closes)>3 else ma[p]) for p in ma}
    up = closes[-1] > ma[5] > ma[25] > ma[75] and all(x > 0 for x in slopes.values())
    down = closes[-1] < ma[5] < ma[25] < ma[75] and all(x < 0 for x in slopes.values())
    e12, e26 = ema(closes, 12), ema(closes, 26)
    macd_series = tuple(a-b for a, b in zip(e12, e26))
    signal_series = ema(macd_series, 9)
    histogram = tuple(a-b for a, b in zip(macd_series, signal_series))
    changes = tuple(b-a for a, b in zip(closes, closes[1:]))[-14:]
    gains, losses = sum(max(x, 0) for x in changes), sum(max(-x, 0) for x in changes)
    rsi = 100 if losses == 0 and gains else (50 if not changes or gains+losses == 0 else 100-100/(1+gains/losses))
    window = closes[-20:]
    middle = sum(window)/len(window)
    sigma = sqrt(sum((x-middle)**2 for x in window)/len(window))
    bandwidth = 4*sigma/middle if middle else 0
    prior = closes[-40:-20]
    prior_bw = 0
    if prior:
        pm = sum(prior)/len(prior); ps = sqrt(sum((x-pm)**2 for x in prior)/len(prior)); prior_bw = 4*ps/pm if pm else 0
    current_volume = volumes[-1] if volumes else 0
    average_volume = sum(volumes[-21:-1])/len(volumes[-21:-1]) if len(volumes)>1 else current_volume
    return TechnicalMetrics(ma[5], ma[25], ma[75], "UPTREND" if up else "DOWNTREND" if down else "NEUTRAL",
                            1 if up else -1 if down else 0, macd_series[-1], signal_series[-1], histogram[-1],
                            macd_series[-1] > 0, len(macd_series)>1 and macd_series[-1]>macd_series[-2],
                            len(histogram)>1 and histogram[-1]>histogram[-2], rsi,
                            middle, middle+sigma, middle-sigma, middle+2*sigma, middle-2*sigma,
                            bandwidth, bool(prior_bw and bandwidth > prior_bw*1.25), current_volume, average_volume,
                            current_volume/average_volume if average_volume else 0)
