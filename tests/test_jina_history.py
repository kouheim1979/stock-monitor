import json

import stock_monitor.jina_history as jina_history


def setup_function():
    jina_history._RESOLVE_CACHE.clear()
    jina_history._RESULT_CACHE.clear()


def test_extract_raw_json():
    payload = {"chart": {"result": []}}
    assert jina_history._extract_json_document(json.dumps(payload)) == payload


def test_extract_json_from_reader_markdown_wrapper():
    payload = {"chart": {"result": [{"timestamp": [1]}]}}
    text = "Title: example\n\nMarkdown Content:\n```json\n" + json.dumps(payload) + "\n```\n"
    assert jina_history._extract_json_document(text) == payload


def test_direct_code_does_not_need_reader(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("reader search must not be called")

    monkeypatch.setattr(jina_history, "_jina_json_candidates", fail)
    assert jina_history.resolve_symbol("8306") == {
        "symbol": "8306.T",
        "code": "8306",
        "name": "8306",
    }


def test_jina_history_uses_legacy_yahoo_parser(monkeypatch):
    resolved = {"symbol": "8306.T", "code": "8306", "name": "8306"}
    payload = {"chart": {"result": []}}
    monkeypatch.setattr(jina_history, "_jina_json_candidates", lambda targets: payload)

    expected = {
        "symbol": "8306.T",
        "code": "8306",
        "name": "MUFG",
        "rows": [{"date": "2026-01-01", "close": 1.0}] * 205,
        "provider": "yahoo",
        "source": "Yahoo",
    }
    monkeypatch.setattr(jina_history.legacy, "_parse_yahoo_chart", lambda data, info: dict(expected))

    result = jina_history._fetch_jina_yahoo_history(resolved)
    assert result["provider"] == "jina-yahoo"
    assert "Jina Reader" in result["source"]
