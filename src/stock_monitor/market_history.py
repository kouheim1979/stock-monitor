"""Japanese daily market history and textbook long-term trend analysis.

The dashboard is intentionally usable without credentials. It therefore uses a
best-effort chain of public data sources (Stooq, then Yahoo by default), keeps a
short server-memory cache, and lets the browser retain successful responses.
Public endpoints can change or rate-limit cloud hosts, so failures are surfaced
rather than replaced with guessed prices.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from threading import Lock, RLock
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)
YAHOO_SEARCH_URLS = (
    "https://query1.finance.yahoo.com/v1/finance/search",
    "https://query2.finance.yahoo.com/v1/finance/search",
)
YAHOO_CHART_URLS = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
    "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
)
STOOQ_CSV_URL = "https://stooq.com/q/d/l/"
PERIODS = (25, 75, 125, 200)
CACHE_TTL_SECONDS = 12 * 60 * 60
STALE_CACHE_SECONDS = 7 * 24 * 60 * 60
PROVIDER_COOLDOWN_SECONDS = 10 * 60

_CACHE_LOCK = RLock()
_RESOLVE_CACHE: dict[str, tuple[float, dict[str, str]]] = {}
_MARKET_CACHE: dict[str, tuple[float, dict]] = {}
_SYMBOL_LOCKS: dict[str, Lock] = {}
_PROVIDER_COOLDOWN_UNTIL: dict[str, float] = {}


class MarketHistoryError(RuntimeError):
    """Raised when daily history cannot be resolved safely."""


class ProviderError(MarketHistoryError):
    """A recoverable error from one upstream provider."""

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        cooldown_seconds: int = 0,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.cooldown_seconds = cooldown_seconds


def _copy_market(value: dict) -> dict:
    """Return a defensive copy of a market payload including its row list."""
    result = dict(value)
    result["rows"] = [dict(row) for row in value.get("rows", [])]
    return result


def _direct_code(raw: str) -> str | None:
    """Normalize a 4-character JPX-style code, including alphanumeric codes."""
    upper = raw.strip().upper()
    for suffix in (".T", ".JP"):
        if upper.endswith(suffix):
            upper = upper[: -len(suffix)]
            break
    if re.fullmatch(r"[0-9A-Z]{4}", upper) and any(char.isdigit() for char in upper):
        return upper
    return None


def _provider_order() -> tuple[str, ...]:
    """Read and validate the configured provider order."""
    requested = os.getenv("MARKET_DATA_PROVIDERS", "stooq,yahoo")
    providers: list[str] = []
    for item in requested.split(","):
        provider = item.strip().lower()
        if provider in {"stooq", "yahoo"} and provider not in providers:
            providers.append(provider)
    return tuple(providers or ("stooq", "yahoo"))


def _request_bytes(url: str, *, timeout: float = 15.0, attempts: int = 2) -> bytes:
    """Fetch bytes with a small retry for transient network/server failures."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/csv,text/plain,*/*",
                "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.7,en;q=0.6",
                "Connection": "close",
            },
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except HTTPError as exc:
            last_error = exc
            if exc.code not in {408, 425, 429, 500, 502, 503, 504} or attempt + 1 >= attempts:
                raise
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                raise
        time.sleep(0.35 * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError("request failed without an exception")


def _decode_text(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("response text encoding is not supported")


def _request_json(url: str, provider: str) -> dict:
    try:
        return json.loads(_decode_text(_request_bytes(url)))
    except HTTPError as exc:
        cooldown = PROVIDER_COOLDOWN_SECONDS if exc.code in {403, 429} else 0
        raise ProviderError(
            provider,
            f"{provider}からの取得に失敗しました (HTTP {exc.code})",
            cooldown_seconds=cooldown,
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ProviderError(provider, f"{provider}に接続できませんでした") from exc
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ProviderError(provider, f"{provider}の応答形式を読み取れませんでした") from exc


def _get_json_candidates(urls: list[str], provider: str) -> dict:
    errors: list[str] = []
    cooldown = 0
    for url in urls:
        try:
            return _request_json(url, provider)
        except ProviderError as exc:
            errors.append(str(exc))
            cooldown = max(cooldown, exc.cooldown_seconds)
    raise ProviderError(
        provider,
        errors[-1] if errors else f"{provider}から取得できませんでした",
        cooldown_seconds=cooldown,
    )


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
    """Resolve a Japanese security from a code, Tokyo ticker, or company name."""
    raw = query.strip()
    if not raw:
        raise MarketHistoryError("銘柄コードまたは会社名を入力してください")
    if len(raw) > 80:
        raise MarketHistoryError("検索文字列が長すぎます")

    direct = _direct_code(raw)
    if direct:
        return {"symbol": f"{direct}.T", "code": direct, "name": direct}

    cache_key = raw.casefold()
    now = time.time()
    with _CACHE_LOCK:
        cached = _RESOLVE_CACHE.get(cache_key)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            return dict(cached[1])

    params = urlencode({"q": raw, "quotesCount": 12, "newsCount": 0})
    payload = _get_json_candidates(
        [f"{base}?{params}" for base in YAHOO_SEARCH_URLS],
        "yahoo",
    )
    quotes = [item for item in payload.get("quotes", []) if item.get("symbol")]
    if not quotes:
        raise MarketHistoryError("銘柄を特定できませんでした。4桁コードでもう一度お試しください")

    quotes.sort(key=lambda item: _candidate_score(item, raw), reverse=True)
    best = quotes[0]
    japanese = [
        item
        for item in quotes
        if str(item.get("symbol", "")).upper().endswith(".T")
    ]
    if japanese:
        japanese.sort(key=lambda item: _candidate_score(item, raw), reverse=True)
        best = japanese[0]

    symbol = str(best["symbol"]).upper()
    if not symbol.endswith(".T"):
        raise MarketHistoryError("日本株を特定できませんでした。銘柄コードでお試しください")
    code = symbol.removesuffix(".T")
    name = str(best.get("longname") or best.get("shortname") or code)
    result = {"symbol": symbol, "code": code, "name": name}
    with _CACHE_LOCK:
        _RESOLVE_CACHE[cache_key] = (now, dict(result))
    return result


def _exchange_zone(meta: dict):
    name = str(meta.get("exchangeTimezoneName") or "Asia/Tokyo")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return timezone.utc


def _valid_price(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _parse_yahoo_chart(payload: dict, resolved: dict[str, str]) -> dict:
    symbol = resolved["symbol"]
    chart = payload.get("chart", {})
    if chart.get("error"):
        description = chart["error"].get("description") or "unknown error"
        raise ProviderError("yahoo", f"Yahooの日足を取得できませんでした: {description}")
    results = chart.get("result") or []
    if not results:
        raise ProviderError("yahoo", "Yahooの日足データが見つかりませんでした")

    result = results[0]
    meta = result.get("meta", {})
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quote_block = (indicators.get("quote") or [{}])[0]
    raw_closes = quote_block.get("close") or []
    volumes = quote_block.get("volume") or []
    adjusted_blocks = indicators.get("adjclose") or []
    adjusted_closes = adjusted_blocks[0].get("adjclose", []) if adjusted_blocks else []
    zone = _exchange_zone(meta)

    rows: list[dict] = []
    for index, timestamp in enumerate(timestamps):
        raw_close = _valid_price(raw_closes[index] if index < len(raw_closes) else None)
        adjusted = _valid_price(
            adjusted_closes[index] if index < len(adjusted_closes) else None
        )
        close_value = adjusted or raw_close
        if close_value is None:
            continue
        volume_value = volumes[index] if index < len(volumes) else None
        day = datetime.fromtimestamp(int(timestamp), zone).date().isoformat()
        rows.append(
            {
                "date": day,
                "close": close_value,
                "market_close": raw_close or close_value,
                "volume": int(volume_value) if volume_value is not None else None,
            }
        )

    if len(rows) < 205:
        raise ProviderError(
            "yahoo",
            f"200日線の計算に必要な日足が不足しています ({len(rows)}日)",
        )

    name = resolved["name"]
    if name in {symbol, resolved["code"]}:
        name = str(meta.get("longName") or meta.get("shortName") or name)
    return {
        "symbol": symbol,
        "code": resolved["code"],
        "name": name,
        "rows": rows,
        "provider": "yahoo",
        "source": "Yahoo Finance Chart（非公式Webエンドポイント）",
    }


def _fetch_yahoo_history(resolved: dict[str, str]) -> dict:
    params = urlencode(
        {
            "range": "3y",
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        }
    )
    urls = [
        f"{base.format(symbol=quote(resolved['symbol'], safe=''))}?{params}"
        for base in YAHOO_CHART_URLS
    ]
    payload = _get_json_candidates(urls, "yahoo")
    return _parse_yahoo_chart(payload, resolved)


def _parse_stooq_csv(text: str, resolved: dict[str, str]) -> dict:
    lowered = text.lower()
    if "exceeded the daily hits limit" in lowered:
        raise ProviderError(
            "stooq",
            "Stooqの無料取得上限に達しました",
            cooldown_seconds=PROVIDER_COOLDOWN_SECONDS,
        )
    if "api key" in lowered and "date" not in lowered[:100]:
        raise ProviderError(
            "stooq",
            "StooqがAPIキーを要求しました",
            cooldown_seconds=PROVIDER_COOLDOWN_SECONDS,
        )
    if "<html" in lowered or "<!doctype" in lowered:
        raise ProviderError("stooq", "StooqからCSV以外の応答が返されました")

    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise ProviderError("stooq", "StooqのCSVヘッダーを読み取れませんでした")
    field_map = {field.strip().lower(): field for field in reader.fieldnames}
    if "date" not in field_map or "close" not in field_map:
        raise ProviderError("stooq", "StooqのCSV形式が変更された可能性があります")

    rows_by_date: dict[str, dict] = {}
    for record in reader:
        day = str(record.get(field_map["date"], "")).strip()
        close_value = _valid_price(record.get(field_map["close"]))
        if not day or close_value is None:
            continue
        try:
            day = datetime.strptime(day, "%Y-%m-%d").date().isoformat()
        except ValueError:
            continue
        volume_raw = record.get(field_map.get("volume", ""), "")
        try:
            volume = int(float(str(volume_raw).replace(",", ""))) if volume_raw else None
        except ValueError:
            volume = None
        rows_by_date[day] = {
            "date": day,
            "close": close_value,
            "market_close": close_value,
            "volume": volume,
        }

    rows = [rows_by_date[key] for key in sorted(rows_by_date)]
    if len(rows) < 205:
        raise ProviderError(
            "stooq",
            f"Stooqの日足が200日線に不足しています ({len(rows)}日)",
        )
    return {
        "symbol": resolved["symbol"],
        "code": resolved["code"],
        "name": resolved["name"],
        "rows": rows,
        "provider": "stooq",
        "source": "Stooq 日足CSV（無料公開データ）",
    }


def _fetch_stooq_history(resolved: dict[str, str]) -> dict:
    today = date.today()
    start = today - timedelta(days=1100)
    params = {
        "s": f"{resolved['code'].lower()}.jp",
        "i": "d",
        "d1": start.strftime("%Y%m%d"),
        "d2": today.strftime("%Y%m%d"),
    }
    api_key = os.getenv("STOOQ_API_KEY", "").strip()
    if api_key:
        params["apikey"] = api_key
    url = f"{STOOQ_CSV_URL}?{urlencode(params)}"
    try:
        text = _decode_text(_request_bytes(url))
    except HTTPError as exc:
        cooldown = PROVIDER_COOLDOWN_SECONDS if exc.code in {403, 429} else 0
        raise ProviderError(
            "stooq",
            f"Stooqからの取得に失敗しました (HTTP {exc.code})",
            cooldown_seconds=cooldown,
        ) from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise ProviderError("stooq", "Stooqに接続できませんでした") from exc
    return _parse_stooq_csv(text, resolved)


def _symbol_lock(symbol: str) -> Lock:
    with _CACHE_LOCK:
        return _SYMBOL_LOCKS.setdefault(symbol, Lock())


def _provider_available(provider: str, now: float) -> bool:
    with _CACHE_LOCK:
        return now >= _PROVIDER_COOLDOWN_UNTIL.get(provider, 0.0)


def _cooldown_provider(provider: str, seconds: int, now: float) -> None:
    if seconds <= 0:
        return
    with _CACHE_LOCK:
        _PROVIDER_COOLDOWN_UNTIL[provider] = max(
            _PROVIDER_COOLDOWN_UNTIL.get(provider, 0.0),
            now + seconds,
        )


def fetch_daily_history(query: str) -> dict:
    """Fetch daily data with provider fallback and a per-symbol single flight."""
    resolved = resolve_symbol(query)
    symbol = resolved["symbol"]
    now = time.time()

    with _CACHE_LOCK:
        cached = _MARKET_CACHE.get(symbol)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            result = _copy_market(cached[1])
            result.update(cached=True, stale=False, cache_age_seconds=int(now - cached[0]))
            return result

    with _symbol_lock(symbol):
        now = time.time()
        with _CACHE_LOCK:
            cached = _MARKET_CACHE.get(symbol)
            if cached and now - cached[0] < CACHE_TTL_SECONDS:
                result = _copy_market(cached[1])
                result.update(cached=True, stale=False, cache_age_seconds=int(now - cached[0]))
                return result

        errors: list[str] = []
        fetchers = {
            "stooq": _fetch_stooq_history,
            "yahoo": _fetch_yahoo_history,
        }
        for provider in _provider_order():
            if not _provider_available(provider, now):
                errors.append(f"{provider}: 一時休止中")
                continue
            try:
                result = fetchers[provider](resolved)
            except ProviderError as exc:
                errors.append(f"{provider}: {exc}")
                _cooldown_provider(provider, exc.cooldown_seconds, now)
                continue
            except Exception as exc:
                errors.append(f"{provider}: {type(exc).__name__}")
                continue

            with _CACHE_LOCK:
                _MARKET_CACHE[symbol] = (now, _copy_market(result))
            result = _copy_market(result)
            result.update(cached=False, stale=False, cache_age_seconds=0)
            return result

        with _CACHE_LOCK:
            cached = _MARKET_CACHE.get(symbol)
            if cached and now - cached[0] < STALE_CACHE_SECONDS:
                result = _copy_market(cached[1])
                result.update(cached=True, stale=True, cache_age_seconds=int(now - cached[0]))
                return result

        detail = " / ".join(errors) if errors else "利用可能なデータ元がありません"
        raise MarketHistoryError(
            "無料データ元から日足を取得できませんでした。少し待って再試行してください。"
            f"（{detail}）"
        )


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
    market_closes = [float(row.get("market_close", row["close"])) for row in clean]
    series = {period: _aligned_sma(closes, period) for period in PERIODS}
    current_price = closes[-1]
    displayed_price = market_closes[-1]
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
        item = {
            "date": clean[index]["date"],
            "close": closes[index],
        }
        for period in PERIODS:
            item[f"ma{period}"] = series[period][index]
        chart_rows.append(item)

    return {
        "price": displayed_price,
        "analysis_price": current_price,
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
    source = market["source"]
    if market.get("cached"):
        source += " / サーバー内キャッシュ"
    if market.get("stale"):
        source += "（最新取得失敗のため保存データ）"
    return {
        "symbol": market["symbol"],
        "code": market["code"],
        "name": market["name"],
        "provider": market["provider"],
        "source": source,
        "cached": bool(market.get("cached")),
        "stale": bool(market.get("stale")),
        "cache_age_seconds": int(market.get("cache_age_seconds", 0)),
        **analysis,
    }


def provider_status() -> dict:
    """Expose non-secret runtime provider status for the health endpoint."""
    now = time.time()
    with _CACHE_LOCK:
        cooldowns = {
            provider: max(0, int(until - now))
            for provider, until in _PROVIDER_COOLDOWN_UNTIL.items()
            if until > now
        }
    return {
        "order": list(_provider_order()),
        "cooldowns_seconds": cooldowns,
        "server_cached_symbols": len(_MARKET_CACHE),
    }
