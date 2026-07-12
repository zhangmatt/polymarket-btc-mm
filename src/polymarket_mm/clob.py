from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .config import Credentials
from .execution import RestingOrder
from .quoting import Quote

try:
    from py_clob_client.client import ClobClient  # type: ignore
    from py_clob_client.clob_types import OpenOrderParams, OrderArgs, OrderType, PostOrdersArgs  # type: ignore
except Exception:  # pragma: no cover - live dependency
    ClobClient = None  # type: ignore
    OpenOrderParams = None  # type: ignore
    OrderArgs = None  # type: ignore
    OrderType = None  # type: ignore
    PostOrdersArgs = None  # type: ignore


@dataclass(frozen=True)
class ClobConfig:
    host: str = "https://clob.polymarket.com"
    chain_id: int = 137
    post_only: bool = True


def create_authenticated_client(config: ClobConfig, credentials: Credentials) -> Any:
    if ClobClient is None:
        raise RuntimeError("py-clob-client is required for live CLOB execution")
    if not credentials.private_key:
        raise ValueError("POLY_PRIVATE_KEY is required")

    kwargs: dict[str, Any] = {
        "host": config.host,
        "key": credentials.private_key,
        "chain_id": config.chain_id,
    }
    if credentials.funder:
        kwargs["signature_type"] = credentials.signature_type
        kwargs["funder"] = credentials.funder

    client = ClobClient(**kwargs)
    if hasattr(client, "create_or_derive_api_creds") and hasattr(client, "set_api_creds"):
        client.set_api_creds(client.create_or_derive_api_creds())
    return client


def extract_order_ids(response: Any) -> list[str]:
    ids: list[str] = []
    if isinstance(response, dict):
        for key in ("orderID", "orderId", "order_id", "id"):
            if response.get(key):
                ids.append(str(response[key]))
        for key in ("orderIds", "order_ids"):
            if isinstance(response.get(key), list):
                ids.extend(str(item) for item in response[key] if item)
        if isinstance(response.get("data"), dict):
            ids.extend(extract_order_ids(response["data"]))
        if isinstance(response.get("data"), list):
            ids.extend(extract_order_ids(response["data"]))
    elif isinstance(response, list):
        for item in response:
            ids.extend(extract_order_ids(item))
    return ids


def _build_post_item(client: Any, quote: Quote, post_only: bool) -> Any:
    if OrderArgs is None:
        raise RuntimeError("py-clob-client order types are unavailable")
    signed = client.create_order(
        OrderArgs(
            price=float(quote.price),
            size=float(quote.size),
            side=quote.side,
            token_id=str(quote.asset),
        )
    )
    if PostOrdersArgs is None or OrderType is None:
        return signed
    for kwargs in (
        {"order": signed, "orderType": OrderType.GTC, "postOnly": post_only},
        {"order": signed, "orderType": OrderType.GTC, "post_only": post_only},
        {"order": signed, "orderType": OrderType.GTC},
    ):
        try:
            return PostOrdersArgs(**kwargs)
        except TypeError:
            continue
    raise RuntimeError("could not build PostOrdersArgs")


class PolymarketClobExecution:
    def __init__(self, client: Any, *, post_only: bool = True):
        self.client = client
        self.post_only = post_only

    def cancel_orders(self, order_ids: list[str]) -> None:
        if not order_ids:
            return
        if hasattr(self.client, "cancel_orders"):
            try:
                self.client.cancel_orders(order_ids)
                return
            except Exception:
                pass
        for order_id in order_ids:
            try:
                self.client.cancel(order_id=order_id)
            except TypeError:
                self.client.cancel(order_id)

    def post_orders(self, quotes: list[Quote]) -> list[str]:
        if not quotes:
            return []
        items = [_build_post_item(self.client, quote, self.post_only and quote.post_only) for quote in quotes]
        if hasattr(self.client, "post_orders") and PostOrdersArgs is not None:
            return extract_order_ids(self.client.post_orders(items))

        order_ids: list[str] = []
        for item in items:
            if hasattr(self.client, "post_order"):
                signed = getattr(item, "order", item)
                order_type = getattr(item, "orderType", OrderType.GTC if OrderType is not None else "GTC")
                try:
                    response = self.client.post_order(signed, order_type, post_only=self.post_only)
                except TypeError:
                    response = self.client.post_order(signed, order_type)
            else:
                response = self.client.create_and_post_order(item)
            order_ids.extend(extract_order_ids(response))
        return order_ids

    def fetch_open_orders(self, condition_id: Optional[str] = None) -> dict[str, dict[str, Any]]:
        try:
            if condition_id and OpenOrderParams is not None:
                raw_orders = self.client.get_orders(OpenOrderParams(market=condition_id))
            else:
                raw_orders = self.client.get_orders()
        except Exception:
            return {}
        out: dict[str, dict[str, Any]] = {}
        for order in raw_orders or []:
            if not isinstance(order, dict):
                continue
            order_id = order.get("id") or order.get("orderId") or order.get("order_id")
            if order_id:
                out[str(order_id)] = order
        return out


def resting_orders_from_open_orders(open_orders: dict[str, dict[str, Any]]) -> list[RestingOrder]:
    resting: list[RestingOrder] = []
    for order_id, order in open_orders.items():
        try:
            asset = str(order.get("asset_id") or order.get("token_id") or order.get("tokenId") or order.get("asset"))
            side = str(order.get("side") or "BUY").upper()
            price = float(order.get("price"))
            original = float(order.get("original_size") or order.get("size") or 0.0)
            matched = float(order.get("size_matched") or order.get("matched_size") or 0.0)
        except Exception:
            continue
        size = max(0.0, original - matched)
        if asset and size > 0:
            resting.append(RestingOrder(order_id, asset, side, int(round(price * 100.0)), size))
    return resting
