"""Daily market-history lookup and textbook long-term trend analysis.

Yahoo Finance's public web endpoints are used without credentials.  They are
not an official, contracted market-data API, so callers must treat failures,
rate limits, and schema changes as recoverable data-source errors.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


USER_AGENT = "stock-monitor/0.3 (+personal technical-analysis dashboard)"
SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
PERIODS = (25, 75, 125, 200)


class MarketHistoryError(RuntimeError):
    """Raised when remote daily history cannot be resolved safely."""


def _get_json(url: str, *, timeout: float = 12.0) -> dict:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
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

    params = urlencode({"q": raw, "quotesCount": 12, "newsCount": 0})
    try:
        payload = _get_json(f"{SEARCH_URL}?{params}")
        quotes = [item for item in payload.get("quotes", []) if item.get("symbol")]
    except MarketHistoryError:
        quotes = []

    if quotes:
        quotes.sort(key=lambda item: _candidate_score(item, raw), reverse=True)
        best = quotes[0]
        symbol = str(best["symbol"]).upper()
        # The app is for Japanese equities.  Prefer Tokyo listings whenever a
        # plain company name has produced multiple international candidates.
        japanese = [item for item in quotes if str(item.get("symbol", "")).upper().endswith(".T")]
        if japanese and not symbol.endswith(".T"):
            japanese.sort(key=lambda item: _candidate_score(item, raw), reverse=True)
            best = japanese[0]
            symbol = str(best["symbol"]).upper()
        name = str(best.get("longname") or best.get("shortname") or symbol)
        return {"symbol": symbol, "name": name}

    upper = raw.upper()
    if re.fullmatch(r"\d{4}", raw):
        return {"symbol": f"{raw}.T", "name": raw}
    if re.fullmatch(r"\d{4}\.T", upper):
        return {"symbol": upper, "name": upper}
    raise MarketHistoryError("銘柄を特定できませんでした。4桁コードでもう一度お試しください")


def _exchange_zone(meta: dict):
    name = str(meta.get("exchangeTimezoneName") or "Asia/Tokyo")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return timezone.utc


def fetch_daily_history(query: str) -> dict:
    """Fetch roughly two years of daily close/volume data for a resolved security."""
    resolved = resolve_symbol(query)
    symbol = resolved["symbol"]
    params = urlencode(
        {
            "range": "2y",
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "false",
        }
    )
    payload = _get_json(f"{CHART_URL.format(symbol=quote(symbol, safe=''))}?{params}")
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

    if len(rows) < 200:
        raise MarketHistoryError(f"200日線の計算に必要な日足が不足しています ({len(rows)}日)")

    name = resolved["name"]
    if name in {symbol, symbol.removesuffix(".T")}:
        name = str(meta.get("longName") or meta.get("shortName") or name)
    return {"symbol": symbol, "name": name, "rows": rows}


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
    if len(clean) < 200:
        raise MarketHistoryError("200日線の計算には200営業日以上の終値が必要です")

    closes = [float(row["close"]) for row in clean]
    series = {period: _aligned_sma(closes, period) for period in PERIODS}
    current_price = closes[-1]
    latest = {period: float(series[period][-1]) for period in PERIODS}

    slopes: dict[int, float] = {}
    slope_pct: dict[int, float] = {}
    lookback = 5
    for period in PERIODS:
        current = series[period][-1]
        previous = series[period][-1 - lookback] if len(series[period]) > lookback else None
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
    return {
        "symbol": market["symbol"],
        "name": market["name"],
        "source": "Yahoo Finance（非公式Webエンドポイント）",
        **analysis,
    }
