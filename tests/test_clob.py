from polymarket_mm.clob import extract_order_ids, resting_orders_from_open_orders


def test_extract_order_ids_handles_batch_shapes():
    response = [
        {"orderID": "a"},
        {"data": {"orderIds": ["b", "c"]}},
        {"id": "d"},
    ]
    assert extract_order_ids(response) == ["a", "b", "c", "d"]


def test_resting_orders_from_open_orders_normalizes_remaining_size():
    resting = resting_orders_from_open_orders(
        {
            "o1": {
                "asset_id": "UP",
                "side": "BUY",
                "price": "0.49",
                "original_size": "10",
                "size_matched": "3",
            }
        }
    )
    assert len(resting) == 1
    assert resting[0].order_id == "o1"
    assert resting[0].price_cents == 49
    assert resting[0].size == 7
