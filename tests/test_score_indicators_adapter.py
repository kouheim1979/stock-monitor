from datetime import datetime, timezone

from stock_monitor.adapters import MockMarketDataAdapter
from stock_monitor.engine import StockMonitor
from stock_monitor.indicators import calculate_technicals, ema, sma
from stock_monitor.models import BookLevel, BookSnapshot, MarketFrame
from stock_monitor.score import PressureScore


def test_neutral_pressure_is_50():
    result = PressureScore().calculate({})
    assert result.raw == 50
    assert result.state == "NEUTRAL"


def test_fixed_bullish_inputs_score_high():
    values = {key: 0.8 for key in PressureScore().config.score_weights}
    result = PressureScore().calculate(values)
    assert 85 <= result.raw <= 95


def test_indicators_and_smoothing():
    closes = tuple(float(x) for x in range(1, 101))
    technical = calculate_technicals(closes, (100.0,) * 20 + (200.0,))
    assert sma(closes, 5)[-1] == 98
    assert len(ema(closes, 12)) == 100
    assert technical.trend == "UPTREND"
    assert technical.macd_positive and technical.rsi14 > 50
    assert technical.volume_ratio == 2


def test_insufficient_history_does_not_fake_ma75_trend():
    closes = tuple(float(x) for x in range(1, 29))
    technical = calculate_technicals(closes, (100.0,) * 28)
    assert technical.ma5 > 0
    assert technical.ma25 > 0
    assert technical.ma75 == 0
    assert technical.trend == "NEUTRAL"
    assert technical.trend_signal == 0


def test_flat_price_with_volume_spike_has_no_directional_volume_bias():
    snapshot = BookSnapshot(
        "X",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        100.0,
        (BookLevel(99.0, 1000),),
        (BookLevel(101.0, 1000),),
        previous_close=100.0,
    )
    frame = MarketFrame(
        snapshot=snapshot,
        daily_closes=(100.0,) * 80,
        daily_volumes=(100.0,) * 20 + (300.0,),
    )
    result = StockMonitor(MockMarketDataAdapter()).process(frame)
    assert result.pressure.components["volume"] == 0


def test_mock_replay_runs_offline():
    adapter = MockMarketDataAdapter()
    frames = list(adapter.stream("7203"))
    assert len(frames) == 3
    assert frames[-1].snapshot.last_price == 1002
