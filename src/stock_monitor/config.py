"""Central, overridable analysis settings (no strategy magic numbers in code)."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AnalysisConfig:
    """Parameters for depth, smoothing, classification, and scoring."""

    depth_levels: int = 5
    depth_weights: tuple[float, ...] = (1.0, 0.8, 0.6, 0.4, 0.2)
    pressure_ema_alpha: float = 0.30
    absorption_min_replenishment: float = 1000.0
    volume_spike_ratio: float = 1.5
    thresholds: tuple[float, float, float, float] = (20, 40, 60, 80)
    score_weights: dict[str, float] = field(default_factory=lambda: {
        "book": .20, "trade_flow": .20, "replenishment": .12,
        "consumption": .12, "cancellation": .08, "momentum": .08,
        "trend": .10, "macd": .06, "volume": .04,
    })
