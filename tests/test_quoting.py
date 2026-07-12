from polymarket_mm.quoting import (
    REGIME_DOWN_ONLY,
    REGIME_PAUSE,
    REGIME_UP_ONLY,
    BestBidAsk,
    QuoteConfig,
    build_maker_quotes,
    detect_quote_regime,
    fair_bid_cents,
)


def test_fair_bid_rounding():
    assert fair_bid_cents(0.502) == 49
    assert fair_bid_cents(0.503) == 50
    assert fair_bid_cents(0.500) == 49


def test_post_only_caps_crossing_quote():
    quotes = build_maker_quotes(
        up_token="UP",
        down_token="DOWN",
        fair_up=0.80,
        up_book=BestBidAsk(bid_cents=79, ask_cents=80),
        down_book=BestBidAsk(bid_cents=19, ask_cents=21),
        regime="BOTH",
        config=QuoteConfig(base_size=5),
    )
    assert quotes.up is not None
    assert quotes.up.price_cents < 80
    assert quotes.up.post_only


def test_regime_filtering():
    assert detect_quote_regime(10.0, fast_threshold=9.5, directional_threshold=1.0) == REGIME_PAUSE
    assert detect_quote_regime(2.0, fast_threshold=9.5, directional_threshold=1.0) == REGIME_UP_ONLY
    assert detect_quote_regime(-2.0, fast_threshold=9.5, directional_threshold=1.0) == REGIME_DOWN_ONLY

