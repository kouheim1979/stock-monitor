import stock_monitor.market_history as market_history
from stock_monitor.market_history import analyze_rows


def _rows(values):
    return [
        {
            "date": f"2025-{(index // 28) % 12 + 1:02d}-{(index % 28) + 1:02d}",
            "close": float(value),
        }
        for index, value in enumerate(values)
    ]


def _market(provider="stooq", code="8306"):
    return {
        "symbol": f"{code}.T",
        "code": code,
        "name": code,
        "provider": provider,
        "source": provider,
        "rows": _rows(range(100, 360)),
    }


def setup_function():
    market_history._RESOLVE_CACHE.clear()
    market_history._MARKET_CACHE.clear()
    market_history._SYMBOL_LOCKS.clear()
    market_history._PROVIDER_COOLDOWN_UNTIL.clear()


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


def test_alphanumeric_jpx_code_does_not_need_search(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("network search must not be called")

    monkeypatch.setattr(market_history, "_get_json_candidates", fail)
    result = market_history.resolve_symbol("130A")

    assert result == {"symbol": "130A.T", "code": "130A", "name": "130A"}


def test_parse_stooq_csv():
    from datetime import date, timedelta

    header = "Date,Open,High,Low,Close,Volume\n"
    start_day = date(2024, 1, 1)
    rows = "".join(
        f"{(start_day + timedelta(days=index)).isoformat()},1,2,0.5,{100 + index},1000\n"
        for index in range(210)
    )
    resolved = {"symbol": "8306.T", "code": "8306", "name": "8306"}

    result = market_history._parse_stooq_csv(header + rows, resolved)

    assert result["provider"] == "stooq"
    assert len(result["rows"]) == 210
    assert result["rows"][-1]["close"] == 309.0


def test_parse_yahoo_prefers_adjusted_close():
    start_timestamp = 1_700_000_000
    timestamps = [start_timestamp + index * 86400 for index in range(210)]
    raw = [200.0 + index for index in range(210)]
    adjusted = [100.0 + index for index in range(210)]
    payload = {
        "chart": {
            "result": [{
                "meta": {"exchangeTimezoneName": "Asia/Tokyo", "shortName": "MUFG"},
                "timestamp": timestamps,
                "indicators": {
                    "quote": [{"close": raw, "volume": [1000] * 210}],
                    "adjclose": [{"adjclose": adjusted}],
                },
            }],
            "error": None,
        }
    }
    resolved = {"symbol": "8306.T", "code": "8306", "name": "8306"}

    result = market_history._parse_yahoo_chart(payload, resolved)

    assert result["provider"] == "yahoo"
    assert result["rows"][-1]["close"] == adjusted[-1]
    assert result["rows"][-1]["market_close"] == raw[-1]
    assert result["name"] == "MUFG"


def test_provider_fallback_uses_yahoo_when_stooq_fails(monkeypatch):
    monkeypatch.setattr(market_history, "_provider_order", lambda: ("stooq", "yahoo"))

    def stooq_fail(resolved):
        raise market_history.ProviderError("stooq", "temporary")

    monkeypatch.setattr(market_history, "_fetch_stooq_history", stooq_fail)
    monkeypatch.setattr(market_history, "_fetch_yahoo_history", lambda resolved: _market("yahoo"))

    result = market_history.fetch_daily_history("8306")

    assert result["provider"] == "yahoo"
    assert result["cached"] is False


def test_second_request_uses_memory_cache(monkeypatch):
    calls = {"count": 0}
    monkeypatch.setattr(market_history, "_provider_order", lambda: ("stooq",))

    def fetch(resolved):
        calls["count"] += 1
        return _market("stooq")

    monkeypatch.setattr(market_history, "_fetch_stooq_history", fetch)

    first = market_history.fetch_daily_history("8306")
    second = market_history.fetch_daily_history("8306")

    assert first["cached"] is False
    assert second["cached"] is True
    assert calls["count"] == 1
