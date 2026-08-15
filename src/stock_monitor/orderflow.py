"""Order-book reconciliation and execution-flow calculations."""

from dataclasses import dataclass

from .config import AnalysisConfig
from .models import Aggressor, BookSnapshot, LevelState, Side, Trade


@dataclass(frozen=True)
class FlowMetrics:
    aggressive_buy_volume: float = 0
    aggressive_sell_volume: float = 0
    trade_delta: float = 0
    normalized_trade_flow: float = 0
    obi: float = 0
    weighted_obi: float = 0
    bid_replenishment: float = 0
    ask_replenishment: float = 0
    bid_cancellation: float = 0
    ask_cancellation: float = 0
    bid_consumed_per_second: float = 0
    ask_consumed_per_second: float = 0
    bid_absorption: bool = False
    ask_absorption: bool = False


class OrderFlowAnalyzer:
    """Infer additions/cancellations from snapshots; results are estimates, not order-ID facts."""

    def __init__(self, config: AnalysisConfig | None = None):
        self.config = config or AnalysisConfig()
        self.previous: BookSnapshot | None = None
        self.levels: dict[tuple[Side, float], LevelState] = {}

    @staticmethod
    def classify_trades(trades: tuple[Trade, ...], snapshot: BookSnapshot) -> tuple[Trade, ...]:
        best_ask = min((x.price for x in snapshot.asks), default=float("inf"))
        best_bid = max((x.price for x in snapshot.bids), default=-float("inf"))
        return tuple(Trade(t.timestamp, t.price, t.quantity,
                           t.aggressor if t.aggressor != Aggressor.UNKNOWN else
                           (Aggressor.BUY if t.price >= best_ask else Aggressor.SELL if t.price <= best_bid else Aggressor.UNKNOWN))
                     for t in trades)

    def analyze(self, snapshot: BookSnapshot, trades: tuple[Trade, ...]) -> FlowMetrics:
        trades = self.classify_trades(trades, self.previous or snapshot)
        buy = sum(t.quantity for t in trades if t.aggressor == Aggressor.BUY)
        sell = sum(t.quantity for t in trades if t.aggressor == Aggressor.SELL)
        elapsed = max((snapshot.timestamp - self.previous.timestamp).total_seconds(), 1) if self.previous else 1
        totals = {"bid_rep": 0., "ask_rep": 0., "bid_can": 0., "ask_can": 0.}
        previous_maps = self._maps(self.previous) if self.previous else {Side.BID: {}, Side.ASK: {}}
        current_maps = self._maps(snapshot)
        for side in Side:
            for price in set(previous_maps[side]) | set(current_maps[side]):
                old, current = previous_maps[side].get(price, 0), current_maps[side].get(price, 0)
                executed = sum(t.quantity for t in trades if t.price == price and
                               ((side == Side.BID and t.aggressor == Aggressor.SELL) or
                                (side == Side.ASK and t.aggressor == Aggressor.BUY)))
                expected = max(old - executed, 0)
                replenished = max(current - expected, 0) if self.previous else 0
                cancelled = max(expected - current, 0) if self.previous else 0
                self.levels[(side, price)] = LevelState(price, current, old, current-old, executed, replenished, cancelled)
                totals[f"{side.value}_rep"] += replenished
                totals[f"{side.value}_can"] += cancelled
        obi = self._imbalance(snapshot, weighted=False)
        weighted = self._imbalance(snapshot, weighted=True)
        total_flow = buy + sell
        self.previous = snapshot
        return FlowMetrics(buy, sell, buy-sell, (buy-sell)/total_flow if total_flow else 0,
                           obi, weighted, totals["bid_rep"], totals["ask_rep"],
                           totals["bid_can"], totals["ask_can"], sell/elapsed, buy/elapsed,
                           totals["bid_rep"] >= self.config.absorption_min_replenishment and sell > 0,
                           totals["ask_rep"] >= self.config.absorption_min_replenishment and buy > 0)

    @staticmethod
    def _maps(snapshot: BookSnapshot | None) -> dict[Side, dict[float, float]]:
        if snapshot is None:
            return {Side.BID: {}, Side.ASK: {}}
        return {Side.BID: {x.price: x.quantity for x in snapshot.bids},
                Side.ASK: {x.price: x.quantity for x in snapshot.asks}}

    def _imbalance(self, snapshot: BookSnapshot, weighted: bool) -> float:
        n = self.config.depth_levels
        bids = sorted(snapshot.bids, key=lambda x: x.price, reverse=True)[:n]
        asks = sorted(snapshot.asks, key=lambda x: x.price)[:n]
        def depth(levels):
            return sum(x.quantity * (self.config.depth_weights[i] if weighted and i < len(self.config.depth_weights) else 1)
                       for i, x in enumerate(levels))
        bid, ask = depth(bids), depth(asks)
        return (bid-ask)/(bid+ask) if bid+ask else 0
