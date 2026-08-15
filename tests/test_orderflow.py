from stock_monitor import BookSnapshot, OrderFlowAnalyzer, Trade, calculate_pressure_score


def _find(changes, price):
    return next(change for change in changes if change.price == price)


def test_detects_bid_replenishment_after_sell_execution():
    previous = BookSnapshot(
        bids={999.0: 2000, 998.0: 8000},
        asks={1001.0: 1000, 1002.0: 3000},
    )
    current = BookSnapshot(
        bids={999.0: 2000, 998.0: 7800},
        asks={1001.0: 1000, 1002.0: 3000},
    )

    result = OrderFlowAnalyzer().analyze(
        previous,
        current,
        [Trade(price=998.0, quantity=2000, side="sell")],
    )

    level = _find(result.bid_changes, 998.0)
    assert level.executed_quantity == 2000
    assert level.replenished_quantity == 1800
    assert level.cancelled_quantity == 0


def test_detects_unexplained_disappearance_as_cancellation():
    previous = BookSnapshot(bids={999.0: 2000}, asks={1001.0: 1000})
    current = BookSnapshot(bids={999.0: 2000}, asks={1001.0: 200})

    result = OrderFlowAnalyzer().analyze(previous, current, [])

    level = _find(result.ask_changes, 1001.0)
    assert level.cancelled_quantity == 800
    assert level.replenished_quantity == 0


def test_infers_buy_aggressor_when_trade_hits_previous_ask():
    previous = BookSnapshot(bids={999.0: 2000}, asks={1001.0: 1000})
    current = BookSnapshot(bids={999.0: 2000}, asks={1001.0: 700})

    result = OrderFlowAnalyzer().analyze(
        previous,
        current,
        [Trade(price=1001.0, quantity=300)],
    )

    assert result.aggressive_buy_volume == 300
    assert result.aggressive_sell_volume == 0


def test_buy_dominated_flow_scores_above_neutral():
    previous = BookSnapshot(
        bids={999.0: 2000, 998.0: 8000, 997.0: 12000},
        asks={1001.0: 1000, 1002.0: 3000, 1003.0: 5000},
    )
    current = BookSnapshot(
        bids={999.0: 3000, 998.0: 8500, 997.0: 12000},
        asks={1001.0: 200, 1002.0: 2500, 1003.0: 5000},
    )
    trades = [
        Trade(price=1001.0, quantity=800, side="buy"),
        Trade(price=1002.0, quantity=500, side="buy"),
        Trade(price=999.0, quantity=100, side="sell"),
    ]

    result = OrderFlowAnalyzer(depth_levels=3).analyze(previous, current, trades)
    score = calculate_pressure_score(result)

    assert score.score > 58
    assert score.state in {"buy_pressure", "strong_buy_pressure"}
