from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Iterable, Optional


@dataclass(frozen=True)
class MakerFillAttribution:
    bid_cents: float
    fill_mid_cents: float
    future_mid_cents: float

    @property
    def spread_capture_cents(self) -> float:
        return self.fill_mid_cents - self.bid_cents

    @property
    def adverse_selection_cents(self) -> float:
        return self.future_mid_cents - self.fill_mid_cents

    @property
    def net_markout_cents(self) -> float:
        return self.future_mid_cents - self.bid_cents


@dataclass(frozen=True)
class TakerMarkout:
    side: str
    price: float
    size: float
    future_mid: float

    @property
    def pnl(self) -> float:
        if self.side.upper() == "BUY":
            return (self.future_mid - self.price) * self.size
        if self.side.upper() == "SELL":
            return (self.price - self.future_mid) * self.size
        raise ValueError("side must be BUY or SELL")


@dataclass(frozen=True)
class AttributionSummary:
    count: int
    spread_capture_cents: Optional[float]
    adverse_selection_cents: Optional[float]
    net_markout_cents: Optional[float]


def summarize_maker_attribution(rows: Iterable[MakerFillAttribution]) -> AttributionSummary:
    values = list(rows)
    if not values:
        return AttributionSummary(0, None, None, None)
    return AttributionSummary(
        count=len(values),
        spread_capture_cents=mean(row.spread_capture_cents for row in values),
        adverse_selection_cents=mean(row.adverse_selection_cents for row in values),
        net_markout_cents=mean(row.net_markout_cents for row in values),
    )


def summarize_taker_markouts(rows: Iterable[TakerMarkout]) -> tuple[int, Optional[float]]:
    pnls = [row.pnl for row in rows]
    if not pnls:
        return 0, None
    return len(pnls), mean(pnls)
