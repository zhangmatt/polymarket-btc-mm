from polymarket_mm.risk import Inventory, RiskConfig, evaluate_quote_risk


def test_overweight_side_quotes_farther_back():
    risk = evaluate_quote_risk(Inventory(up=40, down=10, usdc=100), RiskConfig(imbalance_threshold=20))
    assert risk.up_extra_back_cents > 0
    assert risk.down_extra_back_cents == 0


def test_cash_threshold_triggers_merge():
    risk = evaluate_quote_risk(Inventory(up=12, down=8, usdc=2), RiskConfig(cash_threshold_usdc=5, merge_min_shares=1))
    assert risk.should_merge
    assert risk.merge_size == 8

