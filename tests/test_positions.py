from polymarket_mm.positions import DataApiPositionTracker


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        return FakeResponse(self.rows)


def test_activity_tracker_reconciles_and_dedupes_fills():
    rows = [
        {
            "asset": "UP",
            "side": "BUY",
            "size": 5,
            "timestamp": 101,
            "transactionHash": "tx1",
            "conditionId": "0xabc",
            "slug": "btc-updown-15m-1",
            "outcome": "Up",
            "price": 0.5,
        },
        {
            "asset": "DOWN",
            "side": "BUY",
            "size": 3,
            "timestamp": 102,
            "transactionHash": "tx2",
            "conditionId": "0xabc",
            "slug": "btc-updown-15m-1",
            "outcome": "Down",
            "price": 0.5,
        },
        {
            "asset": "UP",
            "side": "SELL",
            "size": 2,
            "timestamp": 103,
            "transactionHash": "tx3",
            "conditionId": "0xabc",
            "slug": "btc-updown-15m-1",
            "outcome": "Up",
            "price": 0.51,
        },
    ]
    tracker = DataApiPositionTracker(
        wallet="0x0000000000000000000000000000000000000001",
        condition_id="0xabc",
        up_token="UP",
        down_token="DOWN",
        start_ts=100,
        session=FakeSession(rows),
    )

    inventory = tracker.poll()
    assert inventory.up == 3
    assert inventory.down == 3
    assert inventory.imbalance == 0

    inventory = tracker.poll()
    assert inventory.up == 3
    assert inventory.down == 3


def test_trades_endpoint_requests_taker_only_false():
    session = FakeSession([])
    tracker = DataApiPositionTracker(
        wallet="0x0000000000000000000000000000000000000001",
        condition_id="0xabc",
        up_token="UP",
        down_token="DOWN",
        endpoint="trades",
        start_ts=100,
        session=session,
    )
    tracker.poll()
    assert session.calls[0][1]["takerOnly"] == "false"
