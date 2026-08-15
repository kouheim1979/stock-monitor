"""Application service connecting market data, analytics, scoring, and events."""

import logging
from dataclasses import asdict, dataclass

from .adapters import MarketDataAdapter
from .config import AnalysisConfig
from .indicators import TechnicalMetrics, calculate_technicals
from .models import MarketFrame
from .orderflow import FlowMetrics, OrderFlowAnalyzer
from .score import PressureResult, PressureScore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnalysisResult:
    frame: MarketFrame
    flow: FlowMetrics
    technical: TechnicalMetrics
    pressure: PressureResult
    events: tuple[str, ...]


class StockMonitor:
    """Stateful per-symbol orchestration engine, independent of UI and broker."""

    def __init__(self, adapter: MarketDataAdapter, config: AnalysisConfig | None = None):
        self.adapter, self.config = adapter, config or AnalysisConfig()
        self.orderflow, self.scorer = OrderFlowAnalyzer(self.config), PressureScore(self.config)
        self._last_state = ""

    def process(self, frame: MarketFrame) -> AnalysisResult:
        flow = self.orderflow.analyze(frame.snapshot, frame.trades)
        technical = calculate_technicals(frame.daily_closes, frame.daily_volumes)
        cancellation_balance = PressureScore.bounded_ratio(flow.ask_cancellation-flow.bid_cancellation, 5000)
        replenishment = PressureScore.bounded_ratio(flow.bid_replenishment-flow.ask_replenishment, 5000)
        consumption = PressureScore.bounded_ratio(flow.ask_consumed_per_second-flow.bid_consumed_per_second, 1000)
        momentum = 0
        if len(frame.daily_closes) > 1:
            momentum = PressureScore.bounded_ratio((frame.daily_closes[-1]/frame.daily_closes[-2]-1), .02)
        macd_signal = max(-1, min(1, technical.macd_histogram/(frame.snapshot.last_price*.01))) if frame.snapshot.last_price else 0
        volume_signal = max(-1, min(1, (technical.volume_ratio-1)/2)) * (1 if momentum >= 0 else -1)
        pressure = self.scorer.calculate({"book": flow.weighted_obi, "trade_flow": flow.normalized_trade_flow,
            "replenishment": replenishment, "consumption": consumption, "cancellation": cancellation_balance,
            "momentum": momentum, "trend": technical.trend_signal, "macd": macd_signal, "volume": volume_signal})
        events = []
        for trade in frame.trades:
            events.append(f"{trade.timestamp:%H:%M:%S} {trade.price:g}円 {trade.aggressor.value} 約定 {trade.quantity:g}株")
        if flow.bid_replenishment: events.append(f"買い板 {flow.bid_replenishment:g}株補充（推定）")
        if flow.ask_replenishment: events.append(f"売り板 {flow.ask_replenishment:g}株補充（推定）")
        if flow.bid_absorption: events.append("BID ABSORPTION detected（推定）")
        if flow.ask_absorption: events.append("ASK ABSORPTION detected（推定）")
        if self._last_state and self._last_state != pressure.state: events.append(f"Pressure state: {self._last_state} → {pressure.state}")
        self._last_state = pressure.state
        logger.info("analysis symbol=%s pressure=%.1f state=%s", frame.snapshot.symbol, pressure.smoothed, pressure.state)
        return AnalysisResult(frame, flow, technical, pressure, tuple(events))

    def run(self, symbol: str):
        for frame in self.adapter.stream(symbol):
            yield self.process(frame)
