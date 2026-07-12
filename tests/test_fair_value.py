from polymarket_mm.fair_value import gbm_binary_fair_value, implied_sigma_from_mid


def test_gbm_is_even_at_open():
    fair = gbm_binary_fair_value(s0=100.0, spot=100.0, sigma_per_s=0.001, time_remaining_s=900.0)
    assert fair.up == 0.5
    assert fair.down == 0.5


def test_gbm_moves_with_spot():
    up = gbm_binary_fair_value(s0=100.0, spot=101.0, sigma_per_s=0.001, time_remaining_s=900.0)
    down = gbm_binary_fair_value(s0=100.0, spot=99.0, sigma_per_s=0.001, time_remaining_s=900.0)
    assert up.up > 0.5
    assert down.up < 0.5


def test_implied_sigma_inverts_probability():
    sigma = implied_sigma_from_mid(p_up=0.75, s0=100.0, spot=101.0, time_remaining_s=600.0)
    assert sigma is not None
    assert sigma > 0

