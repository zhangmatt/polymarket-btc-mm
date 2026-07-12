from polymarket_mm.execution import CancelReplacePlanner, RestingOrder, SimulatedExecutionClient, execute_cancel_then_post
from polymarket_mm.quoting import Quote, QuoteSet


def test_planner_noops_when_quote_unchanged():
    planner = CancelReplacePlanner()
    current = [RestingOrder("o1", "UP", "BUY", 50, 5)]
    desired = QuoteSet(Quote("UP", "BUY", 50, 5), None, "BOTH", 0.5, None)
    plan = planner.plan(current, desired)
    assert not plan.changed


def test_cancel_then_post_replaces_stale_order():
    planner = CancelReplacePlanner()
    current = [RestingOrder("o1", "UP", "BUY", 50, 5)]
    desired = QuoteSet(Quote("UP", "BUY", 49, 5), None, "BOTH", 1.5, None)
    plan = planner.plan(current, desired)
    client = SimulatedExecutionClient()
    client.live["o1"] = current[0]
    ids = execute_cancel_then_post(client, plan)
    assert client.cancel_log == [["o1"]]
    assert ids == ["sim-1"]
    assert "o1" not in client.live

