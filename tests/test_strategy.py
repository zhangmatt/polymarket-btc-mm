from polymarket_mm.quoting import BestBidAsk
from polymarket_mm.risk import Inventory
from polymarket_mm.strategy import MarketMakerStrategy, MarketSnapshot, StrategyConfig


def test_strategy_outputs_execution_plan():
    strategy = MarketMakerStrategy(StrategyConfig(up_token="UP", down_token="DOWN"))
    snapshot = MarketSnapshot(
        ts_ms=1,
        s0=100.0,
        spot=100.0,
        time_remaining_s=600.0,
        sigma_per_s=0.001,
        up_book=BestBidAsk(49, 51),
        down_book=BestBidAsk(49, 51),
        velocity=0.0,
    )
    decision = strategy.decide(snapshot, Inventory(), [])
    assert decision.quotes.up is not None
    assert decision.quotes.down is not None
    assert decision.execution.post

