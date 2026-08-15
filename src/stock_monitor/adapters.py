"""Market-data ports and deterministic offline implementations."""

import json
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .models import Aggressor, BookLevel, BookSnapshot, MarketFrame, Trade


class MarketDataAdapter(ABC):
    """Interface that a verified broker-specific adapter must implement."""

    @abstractmethod
    def get_snapshot(self, symbol: str) -> BookSnapshot: ...

    @abstractmethod
    def get_recent_trades(self, symbol: str) -> tuple[Trade, ...]: ...

    @abstractmethod
    def stream(self, symbol: str) -> Iterator[MarketFrame]: ...


class ReplayMarketDataAdapter(MarketDataAdapter):
    """Replay JSON frames without networking or market credentials."""

    def __init__(self, path: str | Path):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self._frames = tuple(self._parse_frame(item) for item in payload["frames"])
        if not self._frames:
            raise ValueError("replay must contain at least one frame")
        self._position = 0

    @staticmethod
    def _parse_frame(raw: dict) -> MarketFrame:
        timestamp = datetime.fromisoformat(raw["timestamp"].replace("Z", "+00:00"))
        snapshot = BookSnapshot(
            symbol=str(raw["symbol"]), timestamp=timestamp,
            last_price=float(raw["last_price"]), name=raw.get("name", ""),
            previous_close=float(raw.get("previous_close", 0)),
            bids=tuple(BookLevel(float(p), float(q)) for p, q in raw["bids"]),
            asks=tuple(BookLevel(float(p), float(q)) for p, q in raw["asks"]),
        )
        trades = tuple(Trade(datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00")),
                             float(t["price"]), float(t["quantity"]), Aggressor(t.get("aggressor", "unknown")))
                       for t in raw.get("trades", []))
        return MarketFrame(snapshot, trades, tuple(map(float, raw.get("daily_closes", []))),
                           tuple(map(float, raw.get("daily_volumes", []))))

    def get_snapshot(self, symbol: str) -> BookSnapshot:
        frame = self._frames[min(self._position, len(self._frames) - 1)]
        if frame.snapshot.symbol != symbol:
            raise KeyError(f"symbol {symbol} is not present in replay")
        return frame.snapshot

    def get_recent_trades(self, symbol: str) -> tuple[Trade, ...]:
        self.get_snapshot(symbol)
        return self._frames[min(self._position, len(self._frames) - 1)].trades

    def stream(self, symbol: str) -> Iterator[MarketFrame]:
        for position, frame in enumerate(self._frames):
            if frame.snapshot.symbol != symbol:
                continue
            self._position = position
            yield frame


class MockMarketDataAdapter(ReplayMarketDataAdapter):
    """Built-in realistic scenario for tests and the dashboard."""

    def __init__(self):
        super().__init__(Path(__file__).with_name("data") / "simulation.json")
