from datetime import datetime, timedelta, timezone

import pytest

from stock_monitor.models import Aggressor, BookLevel, BookSnapshot, Trade
from stock_monitor.orderflow import OrderFlowAnalyzer

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def snap(bid=8000, ask=5000, seconds=0):
    return BookSnapshot("X", NOW+timedelta(seconds=seconds), 999,
                        (BookLevel(998, bid),), (BookLevel(1000, ask),))


def test_bid_replenishment_example():
    analyzer = OrderFlowAnalyzer(); analyzer.analyze(snap(), ())
    result = analyzer.analyze(snap(7800, seconds=5), (Trade(NOW, 998, 2000, Aggressor.SELL),))
    assert result.bid_replenishment == 1800
    assert analyzer.levels[next(k for k in analyzer.levels if k[1] == 998)].executed_quantity == 2000


def test_bid_cancellation_example():
    analyzer = OrderFlowAnalyzer(); analyzer.analyze(snap(), ())
    assert analyzer.analyze(snap(6000, seconds=5), ()).bid_cancellation == 2000


def test_ask_consumption_rate_and_trade_flow():
    analyzer = OrderFlowAnalyzer(); analyzer.analyze(snap(), ())
    result = analyzer.analyze(snap(ask=1000, seconds=5), (Trade(NOW, 1000, 4000, Aggressor.BUY),))
    assert result.ask_consumed_per_second == 800
    assert result.normalized_trade_flow == 1


def test_known_order_book_imbalance():
    result = OrderFlowAnalyzer().analyze(snap(bid=9000, ask=3000), ())
    assert result.obi == pytest.approx(.5)
    assert result.weighted_obi == pytest.approx(.5)
