"""Broker-neutral domain models used by every application layer."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Side(str, Enum):
    BID = "bid"
    ASK = "ask"


class Aggressor(str, Enum):
    BUY = "buy"
    SELL = "sell"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BookLevel:
    price: float
    quantity: float


@dataclass(frozen=True)
class BookSnapshot:
    symbol: str
    timestamp: datetime
    last_price: float
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    name: str = ""
    previous_close: float = 0.0


@dataclass(frozen=True)
class Trade:
    timestamp: datetime
    price: float
    quantity: float
    aggressor: Aggressor = Aggressor.UNKNOWN


@dataclass
class LevelState:
    price: float
    quantity: float
    previous_quantity: float = 0.0
    quantity_delta: float = 0.0
    executed_quantity: float = 0.0
    replenished_quantity: float = 0.0
    cancelled_quantity: float = 0.0


@dataclass(frozen=True)
class MarketFrame:
    snapshot: BookSnapshot
    trades: tuple[Trade, ...] = ()
    daily_closes: tuple[float, ...] = ()
    daily_volumes: tuple[float, ...] = ()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
