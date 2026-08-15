"""Explainable configurable pressure score aggregation."""

from dataclasses import dataclass
from math import tanh

from .config import AnalysisConfig


@dataclass(frozen=True)
class PressureResult:
    raw: float
    smoothed: float
    state: str
    components: dict[str, float]


class PressureScore:
    """Normalize signed evidence to [-1, 1], weight it, and map to [0, 100]."""

    def __init__(self, config: AnalysisConfig | None = None):
        self.config = config or AnalysisConfig()
        self.previous: float | None = None

    def calculate(self, inputs: dict[str, float]) -> PressureResult:
        weights = self.config.score_weights
        normalized = {key: max(-1., min(1., float(inputs.get(key, 0)))) for key in weights}
        denominator = sum(weights.values()) or 1
        raw = max(0., min(100., 50 + 50*sum(normalized[k]*weights[k] for k in weights)/denominator))
        smoothed = raw if self.previous is None else self.config.pressure_ema_alpha*raw + (1-self.config.pressure_ema_alpha)*self.previous
        self.previous = smoothed
        a, b, c, d = self.config.thresholds
        state = ("STRONG_SELL_PRESSURE" if smoothed < a else "SELL_PRESSURE" if smoothed < b else
                 "NEUTRAL" if smoothed < c else "BUY_PRESSURE" if smoothed < d else "STRONG_BUY_PRESSURE")
        return PressureResult(raw, smoothed, state, {k: round(v*100) for k, v in normalized.items()})

    @staticmethod
    def bounded_ratio(value: float, scale: float) -> float:
        return tanh(value/scale) if scale else 0
