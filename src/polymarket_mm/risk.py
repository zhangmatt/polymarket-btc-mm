from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Inventory:
    up: float = 0.0
    down: float = 0.0
    usdc: Optional[float] = None

    @property
    def imbalance(self) -> float:
        return self.up - self.down

    @property
    def complete_sets(self) -> float:
        return max(0.0, min(self.up, self.down))


@dataclass(frozen=True)
class RiskConfig:
    max_position_shares: float = 5000.0
    imbalance_threshold: float = 20.0
    imbalance_backoff_cents: float = 1.0
    cash_threshold_usdc: float = 20.0
    merge_min_shares: float = 1.0


@dataclass(frozen=True)
class QuoteRisk:
    allow_up: bool
    allow_down: bool
    up_max_size: float
    down_max_size: float
    up_extra_back_cents: float
    down_extra_back_cents: float
    should_merge: bool
    merge_size: float


def evaluate_quote_risk(inventory: Inventory, config: RiskConfig) -> QuoteRisk:
    """Translate inventory and cash state into quoting permissions."""
    up_remaining = max(0.0, config.max_position_shares - inventory.up)
    down_remaining = max(0.0, config.max_position_shares - inventory.down)

    overweight_up = inventory.imbalance > config.imbalance_threshold
    overweight_down = inventory.imbalance < -config.imbalance_threshold

    merge_size = inventory.complete_sets
    should_merge = (
        inventory.usdc is not None
        and config.cash_threshold_usdc > 0
        and inventory.usdc < config.cash_threshold_usdc
        and merge_size >= config.merge_min_shares
    )

    return QuoteRisk(
        allow_up=up_remaining > 0,
        allow_down=down_remaining > 0,
        up_max_size=up_remaining,
        down_max_size=down_remaining,
        up_extra_back_cents=config.imbalance_backoff_cents if overweight_up else 0.0,
        down_extra_back_cents=config.imbalance_backoff_cents if overweight_down else 0.0,
        should_merge=should_merge,
        merge_size=merge_size if should_merge else 0.0,
    )


def mark_to_resolution(inventory: Inventory, resolved_up: bool, total_cost: float) -> float:
    payout = inventory.up if resolved_up else inventory.down
    return payout - total_cost

