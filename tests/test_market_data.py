from polymarket_mm.market_data import OrderBookStore, realized_velocity


def test_order_book_store_handles_book_and_price_change_messages():
    store = OrderBookStore(["UP"])
    store.update_book(
        {
            "event_type": "book",
            "asset_id": "UP",
            "bids": [{"price": "0.48", "size": "30"}, {"price": "0.49", "size": "20"}],
            "asks": [{"price": "0.52", "size": "25"}, {"price": "0.53", "size": "60"}],
            "timestamp": "1000",
        }
    )
    book = store.snapshot("UP")
    assert book.bid_cents == 49
    assert book.ask_cents == 52

    store.update_price_change(
        {
            "event_type": "price_change",
            "timestamp": "1100",
            "price_changes": [{"asset_id": "UP", "best_bid": "0.50", "best_ask": "0.51"}],
        }
    )
    book = store.snapshot("UP")
    assert book.bid_cents == 50
    assert book.ask_cents == 51


def test_realized_velocity_uses_oldest_tick_outside_window():
    assert realized_velocity([(0, 100.0), (1000, 101.0), (2000, 104.0)], window_ms=2000) == 2.0
