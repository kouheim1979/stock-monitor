from stock_monitor.market_history import analyze_rows


def _rows(values):
    return [
        {"date": f"2026-01-{(index % 28) + 1:02d}", "close": float(value)}
        for index, value in enumerate(values)
    ]


def test_strong_uptrend_is_perfect_order():
    result = analyze_rows(_rows(range(100, 360)))

    assert result["grade"] == 5
    assert result["label"] == "最強の上昇（パーフェクトオーダー）"
    assert result["order"] == "株価 > MA25 > MA75 > MA125 > MA200"
    assert result["moving_averages"]["125"]["direction"] == "上向き"
    assert result["moving_averages"]["200"]["price_above"] is True


def test_strong_downtrend_is_reverse_perfect_order():
    result = analyze_rows(_rows(range(500, 240, -1)))

    assert result["grade"] == 1
    assert result["label"] == "強い下降（逆パーフェクトオーダー）"
    assert result["order"] == "MA200 > MA125 > MA75 > MA25 > 株価"
    assert result["moving_averages"]["125"]["direction"] == "下向き"
    assert result["moving_averages"]["200"]["price_above"] is False


def test_long_term_result_contains_all_requested_averages():
    result = analyze_rows(_rows([100 + (index % 11) for index in range(260)]))

    assert set(result["moving_averages"]) == {"25", "75", "125", "200"}
    assert len(result["history"]) == 260
    assert all(f"ma{period}" in result["history"][-1] for period in (25, 75, 125, 200))
