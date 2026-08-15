from stock_monitor.adapters import MockMarketDataAdapter
from stock_monitor.indicators import calculate_technicals, ema, sma
from stock_monitor.score import PressureScore


def test_neutral_pressure_is_50():
    result = PressureScore().calculate({})
    assert result.raw == 50
    assert result.state == "NEUTRAL"


def test_fixed_bullish_inputs_score_high():
    values = {key: .8 for key in PressureScore().config.score_weights}
    result = PressureScore().calculate(values)
    assert 85 <= result.raw <= 95


def test_indicators_and_smoothing():
    closes = tuple(float(x) for x in range(1, 101))
    technical = calculate_technicals(closes, (100.0,)*20 + (200.0,))
    assert sma(closes, 5)[-1] == 98
    assert len(ema(closes, 12)) == 100
    assert technical.trend == "UPTREND"
    assert technical.macd_positive and technical.rsi14 > 50
    assert technical.volume_ratio == 2


def test_mock_replay_runs_offline():
    adapter = MockMarketDataAdapter()
    frames = list(adapter.stream("7203"))
    assert len(frames) == 3
    assert frames[-1].snapshot.last_price == 1002
