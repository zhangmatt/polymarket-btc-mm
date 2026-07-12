from polymarket_mm.attribution import (
    MakerFillAttribution,
    TakerMarkout,
    summarize_maker_attribution,
    summarize_taker_markouts,
)


def test_maker_attribution_decomposes_fill_markout():
    row = MakerFillAttribution(bid_cents=49.0, fill_mid_cents=50.0, future_mid_cents=48.0)
    assert row.spread_capture_cents == 1.0
    assert row.adverse_selection_cents == -2.0
    assert row.net_markout_cents == -1.0


def test_summarize_maker_attribution():
    summary = summarize_maker_attribution(
        [
            MakerFillAttribution(49, 50, 48),
            MakerFillAttribution(49, 51, 50),
        ]
    )
    assert summary.count == 2
    assert summary.spread_capture_cents == 1.5
    assert summary.adverse_selection_cents == -1.5
    assert summary.net_markout_cents == 0.0


def test_taker_markout_pnl_direction():
    count, avg = summarize_taker_markouts(
        [
            TakerMarkout("BUY", price=0.50, size=10, future_mid=0.49),
            TakerMarkout("SELL", price=0.50, size=10, future_mid=0.51),
        ]
    )
    assert count == 2
    assert round(avg, 8) == -0.1
