"""Registration-free Yahoo Finance access through Jina Reader.

Render shared IPs are frequently rate-limited by Yahoo and Stooq. Jina Reader
acts only as a fetch transport: the underlying market data remains Yahoo
Finance. Anonymous Reader access currently has a small free rate limit, so this
module caches successful responses for 12 hours and keeps the legacy direct
providers as a last fallback.
"""

from __future__ import annotations

import json
import time
from threading import RLock
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from . import market_history as legacy

JINA_PREFIX = "https://r.jina.ai/"
YAHOO_SEARCH_URLS = (
    "https://query1.finance.yahoo.com/v1/finance/search",
    "https://query2.finance.yahoo.com/v1/finance/search",
)
YAHOO_CHART_URLS = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
    "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
)
CACHE_TTL_SECONDS = 12 * 60 * 60
_CACHE_LOCK = RLock()
_RESOLVE_CACHE: dict[str, tuple[float, dict[str, str]]] = {}
_RESULT_CACHE: dict[str, tuple[float, dict]] = {}


class JinaHistoryError(legacy.MarketHistoryError):
    """Raised when the registration-free proxy path cannot return market data."""


def _request_text(target_url: str, *, timeout: float = 20.0) -> str:
    url = JINA_PREFIX + target_url
    request = Request(
        url,
        headers={
            "User-Agent": "stock-monitor/0.5.1 (+personal technical-analysis dashboard)",
            "Accept": "text/plain,text/markdown,*/*",
            "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.7",
            "Connection": "close",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise JinaHistoryError(f"Jina Readerからの取得に失敗しました (HTTP {exc.code})") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise JinaHistoryError("Jina Readerに接続できませんでした") from exc


def _extract_json_document(text: str) -> dict:
    """Extract a JSON object from either raw JSON or Jina's Markdown wrapper."""
    stripped = text.strip()
    if not stripped:
        raise JinaHistoryError("Jina Readerの応答が空でした")

    try:
        value = json.loads(stripped)
        if isinstance(value, dict):
            # Reader can return an envelope when JSON output is requested.
            content = value.get("data", {}).get("content") if isinstance(value.get("data"), dict) else None
            if isinstance(content, str):
                return _extract_json_document(content)
            return value
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    pos = 0
    while True:
        pos = text.find("{", pos)
        if pos < 0:
            break
        try:
            value, _ = decoder.raw_decode(text[pos:])
            if isinstance(value, dict) and (
                "chart" in value or "quotes" in value or "finance" in value
            ):
                return value
        except json.JSONDecodeError:
            pass
        pos += 1
    raise JinaHistoryError("Jina Reader経由の株価応答をJSONとして読み取れませんでした")


def _jina_json_candidates(targets: list[str]) -> dict:
    errors: list[str] = []
    for target in targets:
        try:
            return _extract_json_document(_request_text(target))
        except JinaHistoryError as exc:
            errors.append(str(exc))
    raise JinaHistoryError(errors[-1] if errors else "Jina Readerから取得できませんでした")


def resolve_symbol(query: str) -> dict[str, str]:
    raw = query.strip()
    if not raw:
        raise JinaHistoryError("銘柄コードまたは会社名を入力してください")
    if len(raw) > 80:
        raise JinaHistoryError("検索文字列が長すぎます")

    direct = legacy._direct_code(raw)
    if direct:
        return {"symbol": f"{direct}.T", "code": direct, "name": direct}

    key = raw.casefold()
    now = time.time()
    with _CACHE_LOCK:
        cached = _RESOLVE_CACHE.get(key)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            return dict(cached[1])

    params = urlencode({"q": raw, "quotesCount": 12, "newsCount": 0})
    payload = _jina_json_candidates([f"{base}?{params}" for base in YAHOO_SEARCH_URLS])
    quotes = [item for item in payload.get("quotes", []) if item.get("symbol")]
    if not quotes:
        raise JinaHistoryError("銘柄を特定できませんでした。証券コードでもう一度お試しください")

    quotes.sort(key=lambda item: legacy._candidate_score(item, raw), reverse=True)
    japanese = [item for item in quotes if str(item.get("symbol", "")).upper().endswith(".T")]
    best = (japanese or quotes)[0]
    symbol = str(best["symbol"]).upper()
    if not symbol.endswith(".T"):
        raise JinaHistoryError("日本株を特定できませんでした。証券コードでお試しください")
    code = symbol.removesuffix(".T")
    name = str(best.get("longname") or best.get("shortname") or code)
    result = {"symbol": symbol, "code": code, "name": name}
    with _CACHE_LOCK:
        _RESOLVE_CACHE[key] = (now, dict(result))
    return result


def _fetch_jina_yahoo_history(resolved: dict[str, str]) -> dict:
    # One calendar year normally contains ~245 TSE sessions, enough for the
    # 200-day average plus its 5-session slope while keeping the response small.
    params = urlencode(
        {
            "range": "1y",
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        }
    )
    encoded_symbol = quote(resolved["symbol"], safe="")
    targets = [f"{base.format(symbol=encoded_symbol)}?{params}" for base in YAHOO_CHART_URLS]
    payload = _jina_json_candidates(targets)
    market = legacy._parse_yahoo_chart(payload, resolved)
    market["provider"] = "jina-yahoo"
    market["source"] = "Yahoo Finance Chart（Jina Reader経由・登録不要）"
    return market


def _copy(value: dict) -> dict:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _jina_analysis(query: str) -> dict:
    resolved = resolve_symbol(query)
    code = resolved["code"]
    now = time.time()
    with _CACHE_LOCK:
        cached = _RESULT_CACHE.get(code)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            result = _copy(cached[1])
            result["cached"] = True
            result["source"] += " / サーバー内キャッシュ"
            return result

    market = _fetch_jina_yahoo_history(resolved)
    analysis = legacy.analyze_rows(market["rows"])
    result = {
        "symbol": market["symbol"],
        "code": market["code"],
        "name": market["name"],
        "provider": "jina-yahoo",
        "source": market["source"],
        "cached": False,
        "stale": False,
        "cache_age_seconds": 0,
        **analysis,
    }
    with _CACHE_LOCK:
        _RESULT_CACHE[code] = (now, _copy(result))
    return result


def get_long_term_analysis(query: str) -> dict:
    """Use Jina->Yahoo first, then direct legacy sources if necessary."""
    jina_error: str | None = None
    try:
        return _jina_analysis(query)
    except legacy.MarketHistoryError as exc:
        jina_error = str(exc)

    try:
        return legacy.get_long_term_analysis(query)
    except legacy.MarketHistoryError as exc:
        raise legacy.MarketHistoryError(
            "日足を取得できませんでした。"
            f"（登録不要ルート: {jina_error} / 直接取得: {exc}）"
        ) from exc


def provider_status() -> dict:
    return {
        "primary": "jina-yahoo",
        "registration_required": False,
        "jina_cached_symbols": len(_RESULT_CACHE),
        "fallback": legacy.provider_status(),
    }
