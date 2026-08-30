"""IRBANK-backed Japanese daily history with legacy free-source fallback."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date, timedelta
from threading import RLock
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from . import market_history as legacy

IRBANK_BASE_URL = "https://api.irbank.net/v1"
CACHE_TTL_SECONDS = 12 * 60 * 60
_CACHE_LOCK = RLock()
_RESOLVE_CACHE: dict[str, tuple[float, dict[str, str]]] = {}
_RESULT_CACHE: dict[str, tuple[float, dict]] = {}


def _api_key() -> str:
    return os.getenv("IRBANK_API_KEY", "").strip()


def _normalize_direct_code(value: str) -> str | None:
    raw = value.strip().upper()
    for suffix in (".T", ".JP"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
            break
    if re.fullmatch(r"[0-9A-Z]{4,5}", raw) and any(ch.isdigit() for ch in raw):
        return raw
    return None


def _request_json(path: str, params: dict[str, object] | None = None) -> dict:
    token = _api_key()
    if not token:
        raise legacy.MarketHistoryError(
            "IRBANKの無料APIキーが未設定です。Renderに IRBANK_API_KEY を設定してください。"
        )
    url = f"{IRBANK_BASE_URL}{path}"
    if params:
        url += "?" + urlencode(params)
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "stock-monitor/0.5 (personal technical-analysis dashboard)",
        },
    )
    try:
        with urlopen(request, timeout=18) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8"))
            detail = str((body.get("error") or {}).get("message") or "")
        except Exception:
            detail = ""
        if exc.code == 401:
            message = "IRBANK APIキーが無効です"
        elif exc.code == 403:
            message = "IRBANK APIの利用許可を確認してください"
        elif exc.code == 429:
            message = "IRBANK APIの利用上限または短時間レート制限に達しました"
        elif exc.code == 404:
            message = "IRBANKで銘柄または株価データが見つかりませんでした"
        else:
            message = f"IRBANKからの取得に失敗しました (HTTP {exc.code})"
        if detail:
            message += f": {detail}"
        raise legacy.MarketHistoryError(message) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise legacy.MarketHistoryError("IRBANK APIに接続できませんでした") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise legacy.MarketHistoryError("IRBANK APIの応答を読み取れませんでした") from exc


def _resolve_irbank(query: str) -> dict[str, str]:
    raw = query.strip()
    if not raw:
        raise legacy.MarketHistoryError("銘柄コードまたは会社名を入力してください")
    cache_key = raw.casefold()
    now = time.time()
    with _CACHE_LOCK:
        cached = _RESOLVE_CACHE.get(cache_key)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            return dict(cached[1])

    direct = _normalize_direct_code(raw)
    search_text = direct or raw
    payload = _request_json("/securities", {"q": search_text, "limit": 30})
    securities = payload.get("securities") or []
    if not securities:
        raise legacy.MarketHistoryError("IRBANKで銘柄を特定できませんでした")

    if direct:
        exact = [item for item in securities if str(item.get("code", "")).upper() == direct]
        best = exact[0] if exact else securities[0]
    else:
        needle = raw.casefold()
        exact_name = [
            item for item in securities if str(item.get("name", "")).casefold() == needle
        ]
        starts = [
            item for item in securities if str(item.get("name", "")).casefold().startswith(needle)
        ]
        best = (exact_name or starts or securities)[0]

    code = str(best.get("code", "")).upper()
    name = str(best.get("name") or code)
    if not code:
        raise legacy.MarketHistoryError("IRBANKの銘柄応答に証券コードがありません")
    result = {"code": code, "symbol": f"{code}.T", "name": name}
    with _CACHE_LOCK:
        _RESOLVE_CACHE[cache_key] = (now, dict(result))
    return result


def _valid_price(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _parse_irbank_prices(payload: dict, resolved: dict[str, str]) -> dict:
    raw_prices = payload.get("prices") or []
    rows_by_date: dict[str, dict] = {}
    for record in raw_prices:
        day = str(record.get("date") or "")
        market_close = _valid_price(record.get("close"))
        adjusted = _valid_price(record.get("adj_close"))
        analysis_close = adjusted or market_close
        if not day or analysis_close is None:
            continue
        volume_raw = record.get("volume")
        try:
            volume = int(volume_raw) if volume_raw is not None else None
        except (TypeError, ValueError):
            volume = None
        rows_by_date[day] = {
            "date": day,
            "close": analysis_close,
            "market_close": market_close or analysis_close,
            "volume": volume,
        }

    rows = [rows_by_date[key] for key in sorted(rows_by_date)]
    if len(rows) < 205:
        raise legacy.MarketHistoryError(
            f"IRBANKの日足が200日線の計算に不足しています ({len(rows)}日)"
        )

    attribution = payload.get("attribution") or {}
    source_label = str(attribution.get("source_label") or "JPX等")
    processed_by = str(attribution.get("processed_by") or "IRBANK")
    return {
        "symbol": resolved["symbol"],
        "code": resolved["code"],
        "name": resolved["name"],
        "rows": rows,
        "provider": "irbank",
        "source": f"{source_label}（加工: {processed_by} / IRBANK API）",
    }


def _fetch_irbank_history(resolved: dict[str, str]) -> dict:
    today = date.today()
    start = today - timedelta(days=1100)
    payload = _request_json(
        f"/securities/{quote(resolved['code'], safe='')}/prices",
        {
            "from": start.isoformat(),
            "to": today.isoformat(),
            "limit": 500,
        },
    )
    return _parse_irbank_prices(payload, resolved)


def _irbank_analysis(query: str) -> dict:
    resolved = _resolve_irbank(query)
    code = resolved["code"]
    now = time.time()
    with _CACHE_LOCK:
        cached = _RESULT_CACHE.get(code)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            result = json.loads(json.dumps(cached[1], ensure_ascii=False))
            result["cached"] = True
            result["source"] += " / サーバー内キャッシュ"
            return result

    market = _fetch_irbank_history(resolved)
    analysis = legacy.analyze_rows(market["rows"])
    result = {
        "symbol": market["symbol"],
        "code": market["code"],
        "name": market["name"],
        "provider": "irbank",
        "source": market["source"],
        "cached": False,
        "stale": False,
        "cache_age_seconds": 0,
        **analysis,
    }
    with _CACHE_LOCK:
        _RESULT_CACHE[code] = (now, json.loads(json.dumps(result, ensure_ascii=False)))
    return result


def get_long_term_analysis(query: str) -> dict:
    """Use IRBANK when configured, with the legacy sources only as fallback."""
    irbank_error: str | None = None
    if _api_key():
        try:
            return _irbank_analysis(query)
        except legacy.MarketHistoryError as exc:
            irbank_error = str(exc)
    else:
        irbank_error = "IRBANK APIキー未設定"

    try:
        return legacy.get_long_term_analysis(query)
    except legacy.MarketHistoryError as exc:
        raise legacy.MarketHistoryError(
            "日足を取得できませんでした。"
            f"（IRBANK: {irbank_error} / 予備取得元: {exc}）"
        ) from exc


def provider_status() -> dict:
    return {
        "primary": "irbank",
        "irbank_configured": bool(_api_key()),
        "irbank_cached_symbols": len(_RESULT_CACHE),
        "fallback": legacy.provider_status(),
    }
