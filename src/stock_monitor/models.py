from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Side = Literal["buy", "sell", "unknown"]


@dataclass(frozen=True)
class Trade:
    """One execution from the tape (歩み値).

    side means aggressor side:
    - buy: buyer crossed the spread and hit the ask
    - sell: seller crossed the spread and hit the bid
    - unknown: infer from the previous best bid/ask when possible
    """

    price: float
    quantity: int
    side: Side = "unknown"

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("trade quantity must be positive")


@dataclass(frozen=True)
class BookSnapshot:
    """A point-in-time order book snapshot.

    bids/asks map price -> displayed quantity.
    """

    bids: dict[float, int]
    asks: dict[float, int]

    def __post_init__(self) -> None:
        if any(qty < 0 for qty in self.bids.values()):
            raise ValueError("bid quantities must be non-negative")
        if any(qty < 0 for qty in self.asks.values()):
            raise ValueError("ask quantities must be non-negative")

    @property
    def best_bid(self) -> float | None:
        return max(self.bids, default=None)

    @property
    def best_ask(self) -> float | None:
        return min(self.asks, default=None)

    def top_bid_levels(self, levels: int) -> list[tuple[float, int]]:
        return sorted(self.bids.items(), reverse=True)[:levels]

    def top_ask_levels(self, levels: int) -> list[tuple[float, int]]:
        return sorted(self.asks.items())[:levels]
