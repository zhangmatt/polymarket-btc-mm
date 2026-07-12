from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


REGIME_PAUSE = "PAUSE"
REGIME_UP_ONLY = "UP_ONLY"
REGIME_DOWN_ONLY = "DOWN_ONLY"
REGIME_BOTH = "BOTH"


@dataclass(frozen=True)
class BestBidAsk:
    bid_cents: Optional[int]
    ask_cents: Optional[int]
    bid_size: float = 0.0
    ask_size: float = 0.0
    tick_cents: int = 1


@dataclass(frozen=True)
class QuoteConfig:
    base_size: float = 5.0
    min_notional_usdc: float = 1.0
    complete_set_margin_cents: int = 1
    momentum_quote_back_ticks: int = 0
    post_only: bool = True


@dataclass(frozen=True)
class Quote:
    asset: str
    side: str
    price_cents: int
    size: float
    post_only: bool = True

    @property
    def price(self) -> float:
        return self.price_cents / 100.0


@dataclass(frozen=True)
class QuoteSet:
    up: Optional[Quote]
    down: Optional[Quote]
    regime: str
    up_edge_cents: Optional[float]
    down_edge_cents: Optional[float]

    def active(self) -> list[Quote]:
        return [q for q in (self.up, self.down) if q is not None]


def detect_quote_regime(
    velocity: Optional[float],
    *,
    fast_threshold: float,
    directional_threshold: float,
    current_regime: str = REGIME_BOTH,
    hysteresis_factor: float = 0.5,
    allow_pause: bool = True,
) -> str:
    """Classify BTC velocity into quote regimes with simple hysteresis."""
    if velocity is None:
        return REGIME_BOTH

    if allow_pause and abs(velocity) > fast_threshold:
        return REGIME_PAUSE

    exit_threshold = directional_threshold * hysteresis_factor
    if current_regime == REGIME_UP_ONLY:
        if velocity > exit_threshold:
            return REGIME_UP_ONLY
        if velocity < -directional_threshold:
            return REGIME_DOWN_ONLY
        return REGIME_BOTH

    if current_regime == REGIME_DOWN_ONLY:
        if velocity < -exit_threshold:
            return REGIME_DOWN_ONLY
        if velocity > directional_threshold:
            return REGIME_UP_ONLY
        return REGIME_BOTH

    if velocity > directional_threshold:
        return REGIME_UP_ONLY
    if velocity < -directional_threshold:
        return REGIME_DOWN_ONLY
    return REGIME_BOTH


def fair_bid_cents(probability: float, extra_back_cents: float = 0.0) -> int:
    """
    Round fair to the nearest half cent and quote behind it.

    The live bot used this because Polymarket BTC 15m markets often trade in
    whole-cent ticks: 50.5 fair quotes one tick back, 50.0 fair quotes two half
    ticks back.
    """
    fair_cents = probability * 100.0
    rounded = round(fair_cents * 2.0) / 2.0
    frac = rounded - math.floor(rounded)
    base_back = 1.0 if abs(frac) < 1e-9 else 0.5
    return int(round(rounded - base_back - max(0.0, extra_back_cents)))


def _cap_for_post_only(target_cents: Optional[int], book: BestBidAsk) -> Optional[int]:
    if target_cents is None:
        return None
    target_cents = max(1, min(99, int(target_cents)))
    if book.ask_cents is not None and target_cents >= book.ask_cents:
        target_cents = book.ask_cents - max(1, book.tick_cents)
    if target_cents < 1:
        return None
    return target_cents


def _order_size(price_cents: int, target_size: float, max_size: float, min_notional_usdc: float) -> Optional[float]:
    size = max(0.0, min(float(target_size), float(max_size)))
    min_size = max(1.0, min_notional_usdc / max(price_cents / 100.0, 1e-9))
    return size if size >= min_size else None


def build_maker_quotes(
    *,
    up_token: str,
    down_token: str,
    fair_up: float,
    up_book: BestBidAsk,
    down_book: BestBidAsk,
    regime: str,
    config: QuoteConfig,
    allow_up: bool = True,
    allow_down: bool = True,
    up_max_size: float = float("inf"),
    down_max_size: float = float("inf"),
    up_extra_back_cents: float = 0.0,
    down_extra_back_cents: float = 0.0,
) -> QuoteSet:
    """Construct maker-only BUY quotes for Up/Down binary tokens."""
    fair_down = 1.0 - fair_up

    if regime == REGIME_PAUSE:
        return QuoteSet(None, None, regime, None, None)
    if regime == REGIME_UP_ONLY:
        allow_down = False
    elif regime == REGIME_DOWN_ONLY:
        allow_up = False

    target_up = fair_bid_cents(fair_up, up_extra_back_cents)
    target_down = fair_bid_cents(fair_down, down_extra_back_cents)

    if regime == REGIME_BOTH and config.momentum_quote_back_ticks > 0:
        target_up -= config.momentum_quote_back_ticks * max(1, up_book.tick_cents)
        target_down -= config.momentum_quote_back_ticks * max(1, down_book.tick_cents)

    target_up = _cap_for_post_only(target_up, up_book)
    target_down = _cap_for_post_only(target_down, down_book)

    limit = 100 - max(0, int(config.complete_set_margin_cents))
    if target_up is not None and target_down is not None:
        total = target_up + target_down
        if total > limit:
            excess = total - limit
            if target_up >= target_down:
                target_up = max(1, target_up - excess)
            else:
                target_down = max(1, target_down - excess)

    up_quote = None
    down_quote = None
    if allow_up and target_up is not None:
        size = _order_size(target_up, config.base_size, up_max_size, config.min_notional_usdc)
        if size is not None:
            up_quote = Quote(up_token, "BUY", target_up, size, config.post_only)
    if allow_down and target_down is not None:
        size = _order_size(target_down, config.base_size, down_max_size, config.min_notional_usdc)
        if size is not None:
            down_quote = Quote(down_token, "BUY", target_down, size, config.post_only)

    return QuoteSet(
        up=up_quote,
        down=down_quote,
        regime=regime,
        up_edge_cents=(fair_up * 100.0 - target_up) if target_up is not None else None,
        down_edge_cents=(fair_down * 100.0 - target_down) if target_down is not None else None,
    )

