from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from .quoting import Quote, QuoteSet


@dataclass(frozen=True)
class RestingOrder:
    order_id: str
    asset: str
    side: str
    price_cents: int
    size: float


@dataclass(frozen=True)
class ExecutionPlan:
    cancel_ids: list[str]
    post: list[Quote]
    reason: str

    @property
    def changed(self) -> bool:
        return bool(self.cancel_ids or self.post)


class ExecutionClient(Protocol):
    """Small interface used by the live adapter or a simulator."""

    def cancel_orders(self, order_ids: list[str]) -> None:
        ...

    def post_orders(self, quotes: list[Quote]) -> list[str]:
        ...


def quote_key(quote: Quote) -> tuple[str, str, int, float]:
    return quote.asset, quote.side, quote.price_cents, round(float(quote.size), 8)


def order_key(order: RestingOrder) -> tuple[str, str, int, float]:
    return order.asset, order.side, order.price_cents, round(float(order.size), 8)


class CancelReplacePlanner:
    """
    Compute cancel/replace actions for a maker-only quote loop.

    The production bot favored cancel-then-post to avoid intentionally having
    stale and fresh orders live at the same time. A post-then-cancel mode is
    included because it can improve book presence, but it increases stale-fill
    exposure during fast moves.
    """

    def __init__(self, *, always_requote: bool = False):
        self.always_requote = always_requote

    def plan(self, current_orders: Iterable[RestingOrder], desired_quotes: QuoteSet) -> ExecutionPlan:
        current = list(current_orders)
        desired = desired_quotes.active()
        current_keys = {order_key(order) for order in current}
        desired_keys = {quote_key(quote) for quote in desired}

        if self.always_requote:
            return ExecutionPlan(
                cancel_ids=[order.order_id for order in current],
                post=desired,
                reason="forced requote",
            )

        if current_keys == desired_keys:
            return ExecutionPlan([], [], "unchanged")

        desired_by_asset = {quote.asset: quote for quote in desired}
        cancel_ids = []
        keep_assets = set()
        for order in current:
            wanted = desired_by_asset.get(order.asset)
            if wanted is not None and order_key(order) == quote_key(wanted):
                keep_assets.add(order.asset)
            else:
                cancel_ids.append(order.order_id)

        post = [quote for quote in desired if quote.asset not in keep_assets]
        return ExecutionPlan(cancel_ids=cancel_ids, post=post, reason="quote changed")


def execute_cancel_then_post(client: ExecutionClient, plan: ExecutionPlan) -> list[str]:
    if plan.cancel_ids:
        client.cancel_orders(plan.cancel_ids)
    return client.post_orders(plan.post) if plan.post else []


def execute_post_then_cancel(client: ExecutionClient, plan: ExecutionPlan) -> list[str]:
    new_ids = client.post_orders(plan.post) if plan.post else []
    if plan.cancel_ids:
        client.cancel_orders(plan.cancel_ids)
    return new_ids


class SimulatedExecutionClient:
    """In-memory execution client for tests and backtests."""

    def __init__(self):
        self.next_id = 1
        self.live: dict[str, RestingOrder] = {}
        self.cancel_log: list[list[str]] = []
        self.post_log: list[list[Quote]] = []

    def cancel_orders(self, order_ids: list[str]) -> None:
        self.cancel_log.append(list(order_ids))
        for order_id in order_ids:
            self.live.pop(order_id, None)

    def post_orders(self, quotes: list[Quote]) -> list[str]:
        self.post_log.append(list(quotes))
        ids = []
        for quote in quotes:
            order_id = f"sim-{self.next_id}"
            self.next_id += 1
            self.live[order_id] = RestingOrder(order_id, quote.asset, quote.side, quote.price_cents, quote.size)
            ids.append(order_id)
        return ids

