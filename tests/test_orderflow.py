from datetime import datetime, timedelta, timezone

import pytest

from stock_monitor.models import Aggressor, BookLevel, BookSnapshot, Trade
from stock_monitor.orderflow import OrderFlowAnalyzer

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def snap(bid=8000, ask=5000, seconds=0):
    return BookSnapshot(
        "X",
        NOW + timedelta(seconds=seconds),
        999,
        (BookLevel(998, bid),),
        (BookLevel(1000, ask),),
    )


def test_bid_replenishment_example():
    analyzer = OrderFlowAnalyzer()
    analyzer.analyze(snap(), ())
    result = analyzer.analyze(
        snap(7800, seconds=5),
        (Trade(NOW, 998, 2000, Aggressor.SELL),),
    )
    assert result.bid_replenishment == 1800
    assert analyzer.levels[next(k for k in analyzer.levels if k[1] == 998)].executed_quantity == 2000


def test_bid_cancellation_example():
    analyzer = OrderFlowAnalyzer()
    analyzer.analyze(snap(), ())
    assert analyzer.analyze(snap(6000, seconds=5), ()).bid_cancellation == 2000


def test_ask_consumption_rate_and_trade_flow():
    analyzer = OrderFlowAnalyzer()
    analyzer.analyze(snap(), ())
    result = analyzer.analyze(
        snap(ask=1000, seconds=5),
        (Trade(NOW, 1000, 4000, Aggressor.BUY),),
    )
    assert result.ask_consumed_per_second == 800
    assert result.normalized_trade_flow == 1


def test_known_order_book_imbalance():
    result = OrderFlowAnalyzer().analyze(snap(bid=9000, ask=3000), ())
    assert result.obi == pytest.approx(0.5)
    assert result.weighted_obi == pytest.approx(0.5)


def test_replenishment_counts_execution_beyond_previously_displayed_queue():
    analyzer = OrderFlowAnalyzer()
    analyzer.analyze(snap(bid=1000), ())
    result = analyzer.analyze(
        snap(bid=0, seconds=5),
        (Trade(NOW, 998, 2000, Aggressor.SELL),),
    )
    # 2,000 shares executed against a previously displayed 1,000-share bid.
    # At least 1,000 shares must therefore have appeared during the interval.
    assert result.bid_replenishment == 1000


def test_absorption_requires_execution_and_replenishment_at_same_level():
    previous = BookSnapshot(
        "X",
        NOW,
        999,
        (BookLevel(999, 1000), BookLevel(998, 5000)),
        (BookLevel(1000, 5000),),
    )
    current = BookSnapshot(
        "X",
        NOW + timedelta(seconds=5),
        999,
        (BookLevel(999, 2000), BookLevel(998, 4000)),
        (BookLevel(1000, 5000),),
    )
    analyzer = OrderFlowAnalyzer()
    analyzer.analyze(previous, ())
    result = analyzer.analyze(
        current,
        (Trade(NOW, 998, 1000, Aggressor.SELL),),
    )

    # The +1,000 replenishment happened at 999, while the execution happened
    # at 998. Total-side aggregation alone must not call that absorption.
    assert result.bid_replenishment == 1000
    assert result.bid_absorption is False
