"""Daily market-history lookup and textbook long-term trend analysis.

Yahoo Finance public web endpoints are used without credentials as a temporary
free data source. They are unofficial and may rate-limit cloud hosts, so this
module deliberately minimizes calls, tries both Yahoo query hosts, and caches
successful results in memory.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from threading import RLock
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)
SEARCH_URLS = (
    "https://query1.finance.yahoo.com/v1/finance/search",
    "https://query2.finance.yahoo.com/v1/finance/search",
)
CHART_URLS = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
    "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
)
PERIODS = (25, 75, 125, 200)
CACHE_TTL_SECONDS = 12 * 60 * 60
STALE_CACHE_SECONDS = 7 * 24 * 60 * 60
_CACHE_LOCK = RLock()
_RESOLVE_CACHE: dict[str, tuple[float, dict[str, str]]] = {}
_MARKET_CACHE: dict[str, tuple[float, dict]] = {}


class MarketHistoryError(RuntimeError):
    """Raised when remote daily history cannot be resolved safely."""


def _request_json(url: str, *, timeout: float = 12.0) -> dict:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.7,en;q=0.6",
            "Connection": "close",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise MarketHistoryError(f"株価データ取得に失敗しました (HTTP {exc.code})") from exc
    except (URLError, TimeoutError) as exc:
        raise MarketHistoryError("株価データ提供元に接続できませんでした") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketHistoryError("株価データの応答形式を読み取れませんでした") from exc


def _get_json_candidates(urls: list[str]) -> dict:
    errors: list[str] = []
    for url in urls:
        try:
            return _request_json(url)
        except MarketHistoryError as exc:
            errors.append(str(exc))
    if errors:
        raise MarketHistoryError(errors[-1])
    raise MarketHistoryError("株価データを取得できませんでした")


def _candidate_score(item: dict, raw_query: str) -> tuple[int, int, int]:
    symbol = str(item.get("symbol", "")).upper()
    exchange = str(item.get("exchange", "")).upper()
    quote_type = str(item.get("quoteType", "")).upper()
    raw = raw_query.upper().removesuffix(".T")
    symbol_code = symbol.removesuffix(".T")
    exact = int(symbol_code == raw or symbol == raw_query.upper())
    japan = int(symbol.endswith(".T") or exchange in {"JPX", "TYO", "TSE"})
    security = int(quote_type in {"EQUITY", "ETF", "MUTUALFUND"})
    return exact, japan, security


def resolve_symbol(query: str) -> dict[str, str]:
    """Resolve a Japanese security from a 4-digit code, ticker, or company name."""
    raw = query.strip()
    if not raw:
        raise MarketHistoryError("銘柄コードまたは会社名を入力してください")
    if len(raw) > 80:
        raise MarketHistoryError("検索文字列が長すぎます")

    # Important for the free temporary setup: a 4-digit TSE code needs no
    # search request at all. This cuts Yahoo calls in half for normal use.
    upper = raw.upper()
    if re.fullmatch(r"\d{4}", raw):
        return {"symbol": f"{raw}.T", "name": raw}
    if re.fullmatch(r"\d{4}\.T", upper):
        return {"symbol": upper, "name": upper}

    cache_key = raw.casefold()
    now = time.time()
    with _CACHE_LOCK:
        cached = _RESOLVE_CACHE.get(cache_key)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            return dict(cached[1])

    params = urlencode({"q": raw, "quotesCount": 12, "newsCount": 0})
    payload = _get_json_candidates([f"{base}?{params}" for base in SEARCH_URLS])
    quotes = [item for item in payload.get("quotes", []) if item.get("symbol")]
    if not quotes:
        raise MarketHistoryError("銘柄を特定できませんでした。4桁コードでもう一度お試しください")

    quotes.sort(key=lambda item: _candidate_score(item, raw), reverse=True)
    best = quotes[0]
    symbol = str(best["symbol"]).upper()
    japanese = [item for item in quotes if str(item.get("symbol", "")).upper().endswith(".T")]
    if japanese and not symbol.endswith(".T"):
        japanese.sort(key=lambda item: _candidate_score(item, raw), reverse=True)
        best = japanese[0]
        symbol = str(best["symbol"]).upper()
    name = str(best.get("longname") or best.get("shortname") or symbol)
    result = {"symbol": symbol, "name": name}
    with _CACHE_LOCK:
        _RESOLVE_CACHE[cache_key] = (now, dict(result))
    return result


def _exchange_zone(meta: dict):
    name = str(meta.get("exchangeTimezoneName") or "Asia/Tokyo")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return timezone.utc


def _parse_chart(payload: dict, resolved: dict[str, str]) -> dict:
    symbol = resolved["symbol"]
    chart = payload.get("chart", {})
    if chart.get("error"):
        description = chart["error"].get("description") or "unknown error"
        raise MarketHistoryError(f"日足データを取得できませんでした: {description}")
    results = chart.get("result") or []
    if not results:
        raise MarketHistoryError("日足データが見つかりませんでした")

    result = results[0]
    meta = result.get("meta", {})
    timestamps = result.get("timestamp") or []
    quote_block = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote_block.get("close") or []
    volumes = quote_block.get("volume") or []
    zone = _exchange_zone(meta)

    rows: list[dict] = []
    for index, timestamp in enumerate(timestamps):
        close_value = closes[index] if index < len(closes) else None
        if close_value is None:
            continue
        volume_value = volumes[index] if index < len(volumes) else None
        day = datetime.fromtimestamp(int(timestamp), zone).date().isoformat()
        rows.append(
            {
                "date": day,
                "close": float(close_value),
                "volume": int(volume_value) if volume_value is not None else None,
            }
        )

    if len(rows) < 205:
        raise MarketHistoryError(f"200日線の計算に必要な日足が不足しています ({len(rows)}日)")

    name = resolved["name"]
    if name in {symbol, symbol.removesuffix(".T")}:
        name = str(meta.get("longName") or meta.get("shortName") or name)
    return {"symbol": symbol, "name": name, "rows": rows}


def fetch_daily_history(query: str) -> dict:
    """Fetch roughly two years of daily data, preferring an in-memory cache."""
    resolved = resolve_symbol(query)
    symbol = resolved["symbol"]
    now = time.time()

    with _CACHE_LOCK:
        cached = _MARKET_CACHE.get(symbol)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            result = dict(cached[1])
            result["cached"] = True
            result["stale"] = False
            return result

    params = urlencode(
        {
            "range": "2y",
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "false",
        }
    )
    urls = [f"{base.format(symbol=quote(symbol, safe=''))}?{params}" for base in CHART_URLS]
    try:
        payload = _get_json_candidates(urls)
        result = _parse_chart(payload, resolved)
        with _CACHE_LOCK:
            _MARKET_CACHE[symbol] = (now, dict(result))
        result["cached"] = False
        result["stale"] = False
        return result
    except MarketHistoryError:
        # If Yahoo temporarily rate-limits Render, a previously successful
        # result is still much better than making the app unusable. Keep it for
        # at most one week and mark it clearly as stale.
        with _CACHE_LOCK:
            cached = _MARKET_CACHE.get(symbol)
            if cached and now - cached[0] < STALE_CACHE_SECONDS:
                result = dict(cached[1])
                result["cached"] = True
                result["stale"] = True
                return result
        raise


def _aligned_sma(values: list[float], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("period must be positive")
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    running = sum(values[:period])
    result[period - 1] = running / period
    for index in range(period, len(values)):
        running += values[index] - values[index - period]
        result[index] = running / period
    return result


def analyze_rows(rows: list[dict]) -> dict:
    """Calculate MA25/75/125/200 and a textbook-style five-level trend grade."""
    clean = [row for row in rows if row.get("close") is not None]
    if len(clean) < 205:
        raise MarketHistoryError("200日線の5日傾き計算には205営業日以上の終値が必要です")

    closes = [float(row["close"]) for row in clean]
    series = {period: _aligned_sma(closes, period) for period in PERIODS}
    current_price = closes[-1]
    latest = {period: float(series[period][-1]) for period in PERIODS}

    slopes: dict[int, float] = {}
    slope_pct: dict[int, float] = {}
    lookback = 5
    for period in PERIODS:
        current = series[period][-1]
        previous = series[period][-1 - lookback]
        if current is None or previous is None:
            slopes[period] = 0.0
            slope_pct[period] = 0.0
        else:
            slopes[period] = current - previous
            slope_pct[period] = (current / previous - 1) * 100 if previous else 0.0

    full_up = (
        current_price > latest[25] > latest[75] > latest[125] > latest[200]
        and all(slopes[period] > 0 for period in PERIODS)
    )
    full_down = (
        current_price < latest[25] < latest[75] < latest[125] < latest[200]
        and all(slopes[period] < 0 for period in PERIODS)
    )
    long_up = current_price > latest[200] and latest[75] > latest[200] and slopes[200] > 0
    long_down = current_price < latest[200] and latest[75] < latest[200] and slopes[200] < 0

    if full_up:
        grade, label = 5, "最強の上昇（パーフェクトオーダー）"
    elif long_up:
        grade, label = 4, "長期上昇トレンド"
    elif full_down:
        grade, label = 1, "強い下降（逆パーフェクトオーダー）"
    elif long_down:
        grade, label = 2, "長期下降トレンド"
    else:
        grade, label = 3, "持ち合い・転換局面"

    ordered = sorted(
        [("株価", current_price), *[(f"MA{period}", latest[period]) for period in PERIODS]],
        key=lambda item: item[1],
        reverse=True,
    )
    order_text = " > ".join(name for name, _ in ordered)

    moving_averages = {}
    for period in PERIODS:
        value = latest[period]
        moving_averages[str(period)] = {
            "value": value,
            "direction": "上向き" if slopes[period] > 0 else "下向き" if slopes[period] < 0 else "横ばい",
            "slope_5d_pct": slope_pct[period],
            "price_distance_pct": (current_price / value - 1) * 100 if value else 0.0,
            "price_above": current_price > value,
        }

    chart_rows = []
    start = max(0, len(clean) - 280)
    for index in range(start, len(clean)):
        item = {"date": clean[index]["date"], "close": closes[index]}
        for period in PERIODS:
            item[f"ma{period}"] = series[period][index]
        chart_rows.append(item)

    return {
        "price": current_price,
        "as_of": clean[-1]["date"],
        "grade": grade,
        "stars": "★" * grade + "☆" * (5 - grade),
        "label": label,
        "order": order_text,
        "moving_averages": moving_averages,
        "history": chart_rows,
    }


def get_long_term_analysis(query: str) -> dict:
    """Resolve a security, fetch daily prices, and return long-term MA analysis."""
    market = fetch_daily_history(query)
    analysis = analyze_rows(market["rows"])
    source = "Yahoo Finance（非公式・一時利用）"
    if market.get("cached"):
        source += " / サーバーキャッシュ"
    if market.get("stale"):
        source += "（最新取得失敗のため保存データ）"
    return {
        "symbol": market["symbol"],
        "name": market["name"],
        "source": source,
        "cached": bool(market.get("cached")),
        "stale": bool(market.get("stale")),
        **analysis,
    }
