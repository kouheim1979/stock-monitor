from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .models import BookSnapshot, Trade


@dataclass(frozen=True)
class LevelChange:
    price: float
    previous_quantity: int
    current_quantity: int
    executed_quantity: int
    replenished_quantity: int
    cancelled_quantity: int


@dataclass(frozen=True)
class OrderFlowResult:
    bid_changes: tuple[LevelChange, ...]
    ask_changes: tuple[LevelChange, ...]
    aggressive_buy_volume: int
    aggressive_sell_volume: int
    unknown_trade_volume: int
    bid_depth: int
    ask_depth: int

    @property
    def total_bid_replenishment(self) -> int:
        return sum(x.replenished_quantity for x in self.bid_changes)

    @property
    def total_ask_replenishment(self) -> int:
        return sum(x.replenished_quantity for x in self.ask_changes)

    @property
    def total_bid_cancellation(self) -> int:
        return sum(x.cancelled_quantity for x in self.bid_changes)

    @property
    def total_ask_cancellation(self) -> int:
        return sum(x.cancelled_quantity for x in self.ask_changes)


class OrderFlowAnalyzer:
    """Compare two book snapshots and the executions between them.

    This is intentionally deterministic and conservative. It does not claim to
    reconstruct exchange-level order IDs. Instead, it estimates how much of a
    displayed level was consumed by executions, replenished, or cancelled.
    """

    def __init__(self, depth_levels: int = 5) -> None:
        if depth_levels <= 0:
            raise ValueError("depth_levels must be positive")
        self.depth_levels = depth_levels

    def analyze(
        self,
        previous: BookSnapshot,
        current: BookSnapshot,
        trades: list[Trade],
    ) -> OrderFlowResult:
        classified = [self._classify_trade(t, previous) for t in trades]

        aggressive_buy = sum(t.quantity for t in classified if t.side == "buy")
        aggressive_sell = sum(t.quantity for t in classified if t.side == "sell")
        unknown = sum(t.quantity for t in classified if t.side == "unknown")

        buys_by_price: dict[float, int] = defaultdict(int)
        sells_by_price: dict[float, int] = defaultdict(int)
        for trade in classified:
            if trade.side == "buy":
                buys_by_price[trade.price] += trade.quantity
            elif trade.side == "sell":
                sells_by_price[trade.price] += trade.quantity

        bid_prices = set(previous.bids) | set(current.bids) | set(sells_by_price)
        ask_prices = set(previous.asks) | set(current.asks) | set(buys_by_price)

        bid_changes = tuple(
            self._level_change(
                price=price,
                previous_quantity=previous.bids.get(price, 0),
                current_quantity=current.bids.get(price, 0),
                executed_quantity=sells_by_price.get(price, 0),
            )
            for price in sorted(bid_prices, reverse=True)
        )
        ask_changes = tuple(
            self._level_change(
                price=price,
                previous_quantity=previous.asks.get(price, 0),
                current_quantity=current.asks.get(price, 0),
                executed_quantity=buys_by_price.get(price, 0),
            )
            for price in sorted(ask_prices)
        )

        bid_depth = sum(qty for _, qty in current.top_bid_levels(self.depth_levels))
        ask_depth = sum(qty for _, qty in current.top_ask_levels(self.depth_levels))

        return OrderFlowResult(
            bid_changes=bid_changes,
            ask_changes=ask_changes,
            aggressive_buy_volume=aggressive_buy,
            aggressive_sell_volume=aggressive_sell,
            unknown_trade_volume=unknown,
            bid_depth=bid_depth,
            ask_depth=ask_depth,
        )

    @staticmethod
    def _classify_trade(trade: Trade, previous: BookSnapshot) -> Trade:
        if trade.side != "unknown":
            return trade

        best_ask = previous.best_ask
        best_bid = previous.best_bid

        if best_ask is not None and trade.price >= best_ask:
            return Trade(trade.price, trade.quantity, "buy")
        if best_bid is not None and trade.price <= best_bid:
            return Trade(trade.price, trade.quantity, "sell")
        return trade

    @staticmethod
    def _level_change(
        *,
        price: float,
        previous_quantity: int,
        current_quantity: int,
        executed_quantity: int,
    ) -> LevelChange:
        # If executions would have reduced the old displayed queue below the
        # observed current quantity, the difference must have been replenished.
        expected_after_execution = max(previous_quantity - executed_quantity, 0)
        replenished = max(current_quantity - expected_after_execution, 0)

        # Quantity that disappeared beyond what executions explain is treated
        # as cancellation. This is an estimate because snapshot feeds do not
        # expose exchange order IDs.
        explained_current = expected_after_execution + replenished
        cancelled = max(explained_current - current_quantity, 0)

        # For the common case where executions do not fully explain the visible
        # decrease, explicitly recover the unexplained disappearance.
        visible_decrease = max(previous_quantity - current_quantity, 0)
        execution_decrease = min(previous_quantity, executed_quantity)
        cancelled = max(visible_decrease - execution_decrease, cancelled)

        return LevelChange(
            price=price,
            previous_quantity=previous_quantity,
            current_quantity=current_quantity,
            executed_quantity=executed_quantity,
            replenished_quantity=replenished,
            cancelled_quantity=cancelled,
        )
