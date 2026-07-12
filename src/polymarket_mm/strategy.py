from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .execution import CancelReplacePlanner, ExecutionPlan, RestingOrder
from .fair_value import BinaryFairValue, gbm_binary_fair_value
from .quoting import BestBidAsk, QuoteConfig, QuoteSet, build_maker_quotes, detect_quote_regime
from .risk import Inventory, QuoteRisk, RiskConfig, evaluate_quote_risk


@dataclass(frozen=True)
class MarketSnapshot:
    ts_ms: int
    s0: float
    spot: float
    time_remaining_s: float
    sigma_per_s: float
    up_book: BestBidAsk
    down_book: BestBidAsk
    velocity: Optional[float] = None


@dataclass(frozen=True)
class StrategyConfig:
    up_token: str
    down_token: str
    quote: QuoteConfig = QuoteConfig()
    risk: RiskConfig = RiskConfig()
    fast_velocity_threshold: float = 9.5
    directional_velocity_threshold: float = 1.0
    allow_pause: bool = True
    always_requote: bool = False


@dataclass(frozen=True)
class StrategyDecision:
    fair: BinaryFairValue
    risk: QuoteRisk
    quotes: QuoteSet
    execution: ExecutionPlan


class MarketMakerStrategy:
    def __init__(self, config: StrategyConfig):
        self.config = config
        self.current_regime = "BOTH"
        self.planner = CancelReplacePlanner(always_requote=config.always_requote)

    def decide(
        self,
        snapshot: MarketSnapshot,
        inventory: Inventory,
        current_orders: list[RestingOrder],
    ) -> StrategyDecision:
        fair = gbm_binary_fair_value(
            s0=snapshot.s0,
            spot=snapshot.spot,
            sigma_per_s=snapshot.sigma_per_s,
            time_remaining_s=snapshot.time_remaining_s,
        )
        self.current_regime = detect_quote_regime(
            snapshot.velocity,
            fast_threshold=self.config.fast_velocity_threshold,
            directional_threshold=self.config.directional_velocity_threshold,
            current_regime=self.current_regime,
            allow_pause=self.config.allow_pause,
        )
        risk = evaluate_quote_risk(inventory, self.config.risk)
        quotes = build_maker_quotes(
            up_token=self.config.up_token,
            down_token=self.config.down_token,
            fair_up=fair.up,
            up_book=snapshot.up_book,
            down_book=snapshot.down_book,
            regime=self.current_regime,
            config=self.config.quote,
            allow_up=risk.allow_up,
            allow_down=risk.allow_down,
            up_max_size=risk.up_max_size,
            down_max_size=risk.down_max_size,
            up_extra_back_cents=risk.up_extra_back_cents,
            down_extra_back_cents=risk.down_extra_back_cents,
        )
        execution = self.planner.plan(current_orders, quotes)
        return StrategyDecision(fair=fair, risk=risk, quotes=quotes, execution=execution)

