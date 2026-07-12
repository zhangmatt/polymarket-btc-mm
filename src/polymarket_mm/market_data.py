from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from typing import Any, Deque, Iterable, Optional

import requests

from .fair_value import RollingVolatility
from .quoting import BestBidAsk

try:  # Optional speedup if available.
    import orjson  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    orjson = None

try:
    import websocket  # type: ignore
except Exception:  # pragma: no cover - live dependency
    websocket = None


def now_ms() -> int:
    return int(time.time() * 1000)


def to_cents(price: float) -> int:
    return int(round(float(price) * 100.0))


def loads(payload: str | bytes) -> Any:
    if orjson is not None:
        return orjson.loads(payload)
    return json.loads(payload)


class OrderBookStore:
    def __init__(self, asset_ids: Iterable[str], history_len: int = 20_000):
        self._books = {str(asset_id): BestBidAsk(None, None) for asset_id in asset_ids}
        self._history: dict[str, Deque[tuple[int, float]]] = {
            str(asset_id): deque(maxlen=history_len) for asset_id in asset_ids
        }
        self._lock = threading.Lock()
        self.last_update_ms = 0

    def snapshot(self, asset_id: str) -> BestBidAsk:
        with self._lock:
            return self._books[str(asset_id)]

    def snapshots(self) -> dict[str, BestBidAsk]:
        with self._lock:
            return dict(self._books)

    def mid_history(self, asset_id: str) -> list[tuple[int, float]]:
        with self._lock:
            return list(self._history[str(asset_id)])

    def _record_mid_locked(self, asset_id: str, ts_ms: Optional[int] = None) -> None:
        book = self._books[asset_id]
        if book.bid_cents is None or book.ask_cents is None:
            return
        self._history[asset_id].append((ts_ms or now_ms(), (book.bid_cents + book.ask_cents) / 2.0))
        self.last_update_ms = ts_ms or now_ms()

    def update_book(self, payload: dict[str, Any]) -> bool:
        asset_id = str(payload.get("asset_id") or "")
        if asset_id not in self._books:
            return False
        bids = payload.get("bids") or payload.get("buys") or []
        asks = payload.get("asks") or payload.get("sells") or []
        bid_price, bid_size = _best_level(bids, "bid")
        ask_price, ask_size = _best_level(asks, "ask")
        try:
            ts_ms = int(payload.get("timestamp") or now_ms())
        except Exception:
            ts_ms = now_ms()

        with self._lock:
            old = self._books[asset_id]
            self._books[asset_id] = BestBidAsk(
                bid_cents=to_cents(bid_price) if bid_price is not None else old.bid_cents,
                ask_cents=to_cents(ask_price) if ask_price is not None else old.ask_cents,
                bid_size=bid_size if bid_price is not None else old.bid_size,
                ask_size=ask_size if ask_price is not None else old.ask_size,
                tick_cents=old.tick_cents,
            )
            self._record_mid_locked(asset_id, ts_ms)
        return True

    def update_price_change(self, payload: dict[str, Any]) -> bool:
        changed = False
        try:
            ts_ms = int(payload.get("timestamp") or now_ms())
        except Exception:
            ts_ms = now_ms()
        for change in payload.get("price_changes", []) or []:
            if not isinstance(change, dict):
                continue
            asset_id = str(change.get("asset_id") or "")
            if asset_id not in self._books:
                continue
            bid = _parse_optional_float(change.get("best_bid") or change.get("bestBid"))
            ask = _parse_optional_float(change.get("best_ask") or change.get("bestAsk"))
            with self._lock:
                old = self._books[asset_id]
                self._books[asset_id] = BestBidAsk(
                    bid_cents=to_cents(bid) if bid is not None and bid > 0 else old.bid_cents,
                    ask_cents=to_cents(ask) if ask is not None and ask < 1 else old.ask_cents,
                    bid_size=old.bid_size,
                    ask_size=old.ask_size,
                    tick_cents=old.tick_cents,
                )
                self._record_mid_locked(asset_id, ts_ms)
            changed = True
        return changed

    def update_tick_size(self, payload: dict[str, Any]) -> bool:
        asset_id = str(payload.get("asset_id") or "")
        if asset_id not in self._books:
            return False
        tick = _parse_optional_float(payload.get("new_tick_size"))
        if tick is None:
            return False
        with self._lock:
            old = self._books[asset_id]
            self._books[asset_id] = BestBidAsk(
                old.bid_cents,
                old.ask_cents,
                old.bid_size,
                old.ask_size,
                max(1, to_cents(tick)),
            )
        return True


def _parse_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _best_level(levels: Any, side: str) -> tuple[Optional[float], float]:
    best: Optional[float] = None
    size = 0.0
    if not isinstance(levels, list):
        return None, 0.0
    for level in levels:
        if not isinstance(level, dict):
            continue
        price = _parse_optional_float(level.get("price"))
        level_size = _parse_optional_float(level.get("size")) or 0.0
        if price is None:
            continue
        if best is None or (side == "bid" and price > best) or (side == "ask" and price < best):
            best = price
            size = level_size
    return best, size


class PolymarketMarketStream:
    def __init__(
        self,
        *,
        ws_url: str,
        asset_ids: list[str],
        store: OrderBookStore,
        sslopt: Optional[dict[str, Any]] = None,
        on_update: Optional[threading.Event] = None,
        verbose: bool = False,
    ):
        self.ws_url = ws_url.rstrip("/") + "/ws/market"
        self.asset_ids = [str(asset_id) for asset_id in asset_ids]
        self.store = store
        self.sslopt = sslopt
        self.on_update = on_update
        self.verbose = verbose
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ws: Any = None
        self._last_msg_ms = 0

    def start(self) -> None:
        if websocket is None:
            raise RuntimeError("websocket-client is required for live market streams")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass

    def last_msg_ms(self) -> int:
        return self._last_msg_ms

    def reconnect(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass

    def _on_open(self, ws: Any) -> None:
        ws.send(json.dumps({"type": "market", "assets_ids": self.asset_ids}))
        threading.Thread(target=self._ping_loop, args=(ws,), daemon=True).start()
        if self.verbose:
            print(f"[market-ws] subscribed assets={len(self.asset_ids)}", flush=True)

    def _ping_loop(self, ws: Any) -> None:
        while not self._stop.is_set():
            try:
                ws.send("PING")
            except Exception:
                return
            time.sleep(10)

    def _on_message(self, ws: Any, message: str) -> None:
        try:
            data = loads(message)
        except Exception:
            return
        self._last_msg_ms = now_ms()
        handled = False
        if isinstance(data, list):
            for item in data:
                handled = self._handle_one(item) or handled
        else:
            handled = self._handle_one(data)
        if handled and self.on_update is not None:
            self.on_update.set()

    def _handle_one(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        event_type = payload.get("event_type") or payload.get("type")
        if event_type is None and "price_changes" in payload:
            event_type = "price_change"
        if event_type == "book":
            return self.store.update_book(payload)
        if event_type == "price_change":
            return self.store.update_price_change(payload)
        if event_type == "tick_size_change":
            return self.store.update_tick_size(payload)
        return False

    def _on_error(self, ws: Any, error: Any) -> None:
        if self.verbose:
            print(f"[market-ws] error: {error}", flush=True)

    def _on_close(self, ws: Any, code: Any, msg: Any) -> None:
        if self.verbose:
            print(f"[market-ws] closed: {code} {msg}", flush=True)

    def _run(self) -> None:
        backoff_s = 1.0
        while not self._stop.is_set():
            try:
                self._ws = websocket.WebSocketApp(
                    self.ws_url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                kwargs = {"sslopt": self.sslopt} if self.sslopt else {}
                self._ws.run_forever(**kwargs)
                backoff_s = 1.0
            except Exception as exc:
                if self.verbose:
                    print(f"[market-ws] exception: {exc}", flush=True)
            time.sleep(backoff_s)
            backoff_s = min(backoff_s * 1.5, 5.0)


class OrderBookRestPoller:
    def __init__(
        self,
        *,
        clob_host: str,
        asset_ids: list[str],
        store: OrderBookStore,
        poll_ms: int = 150,
        session: Optional[requests.Session] = None,
    ):
        self.clob_host = clob_host.rstrip("/")
        self.asset_ids = [str(asset_id) for asset_id in asset_ids]
        self.store = store
        self.poll_ms = max(50, int(poll_ms))
        self.session = session or requests.Session()
        self._last_poll_ms = 0

    def poll(self) -> None:
        ts = now_ms()
        if ts - self._last_poll_ms < self.poll_ms:
            return
        self._last_poll_ms = ts
        if self._post_books():
            return
        for asset_id in self.asset_ids:
            self._get_book(asset_id)

    def _post_books(self) -> bool:
        try:
            resp = self.session.post(
                f"{self.clob_host}/books",
                json={"token_ids": self.asset_ids},
                timeout=2.0,
            )
            if resp.status_code >= 400:
                return False
            for summary in _iter_book_summaries(resp.json()):
                self.store.update_book(_normalize_book_summary(summary))
            return True
        except Exception:
            return False

    def _get_book(self, asset_id: str) -> bool:
        for param in ("token_id", "asset_id"):
            try:
                resp = self.session.get(f"{self.clob_host}/book", params={param: asset_id}, timeout=2.0)
                if resp.status_code >= 400:
                    continue
                for summary in _iter_book_summaries(resp.json()):
                    self.store.update_book(_normalize_book_summary(summary, asset_id))
                return True
            except Exception:
                continue
        return False


def _iter_book_summaries(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return
    if not isinstance(payload, dict):
        return
    for key in ("orderbooks", "orderbook_summaries", "data", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield item
            return
    yield payload


def _normalize_book_summary(summary: dict[str, Any], fallback_asset_id: Optional[str] = None) -> dict[str, Any]:
    asset_id = (
        summary.get("asset_id")
        or summary.get("token_id")
        or summary.get("tokenId")
        or summary.get("id")
        or fallback_asset_id
    )
    return {
        "asset_id": str(asset_id or ""),
        "bids": summary.get("bids") or summary.get("buy_orders") or summary.get("buys") or [],
        "asks": summary.get("asks") or summary.get("sell_orders") or summary.get("sells") or [],
        "timestamp": str(now_ms()),
    }


class BinanceTradeStream:
    def __init__(
        self,
        *,
        symbol: str = "btcusdt",
        ws_base: str = "wss://stream.binance.com:9443/ws",
        vol_window_s: float = 120.0,
        sslopt: Optional[dict[str, Any]] = None,
        verbose: bool = False,
    ):
        self.symbol = symbol.lower()
        self.ws_base = ws_base.rstrip("/")
        self.sslopt = sslopt
        self.verbose = verbose
        self._vol = RollingVolatility(vol_window_s)
        self._history: Deque[tuple[int, float]] = deque()
        self._history_window_ms = int(max(vol_window_s, 120.0) * 1000)
        self._last: Optional[tuple[int, float]] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ws: Any = None

    def start(self) -> None:
        if websocket is None:
            raise RuntimeError("websocket-client is required for live Binance streams")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass

    def last_price(self) -> Optional[tuple[int, float]]:
        with self._lock:
            return self._last

    def history(self) -> list[tuple[int, float]]:
        with self._lock:
            return list(self._history)

    def sigma_per_s(self) -> Optional[float]:
        with self._lock:
            return self._vol.sigma_per_s()

    def velocity(self, window_ms: int = 2000) -> Optional[float]:
        with self._lock:
            if len(self._history) < 2:
                return None
            end_ts, end_price = self._history[-1]
            cutoff = end_ts - max(1, window_ms)
            start_ts, start_price = self._history[0]
            for ts, price in reversed(self._history):
                if ts <= cutoff:
                    start_ts, start_price = ts, price
                    break
            dt_s = (end_ts - start_ts) / 1000.0
            if dt_s <= 0:
                return None
            return (end_price - start_price) / dt_s

    def _on_message(self, ws: Any, message: str) -> None:
        try:
            data = loads(message)
            price = float(data.get("p"))
            ts_ms = int(data.get("T") or data.get("E") or now_ms())
        except Exception:
            return
        with self._lock:
            self._last = (ts_ms, price)
            self._history.append((ts_ms, price))
            self._vol.update(ts_ms, price)
            cutoff = ts_ms - self._history_window_ms
            while self._history and self._history[0][0] < cutoff:
                self._history.popleft()

    def _on_error(self, ws: Any, error: Any) -> None:
        if self.verbose:
            print(f"[binance] error: {error}", flush=True)

    def _on_close(self, ws: Any, code: Any, msg: Any) -> None:
        if self.verbose:
            print(f"[binance] closed: {code} {msg}", flush=True)

    def _run(self) -> None:
        url = f"{self.ws_base}/{self.symbol}@trade"
        while not self._stop.is_set():
            try:
                self._ws = websocket.WebSocketApp(
                    url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                kwargs = {"ping_interval": 20, "ping_timeout": 10}
                if self.sslopt:
                    kwargs["sslopt"] = self.sslopt
                if self.verbose:
                    print(f"[binance] connecting {url}", flush=True)
                self._ws.run_forever(**kwargs)
            except Exception as exc:
                if self.verbose:
                    print(f"[binance] exception: {exc}", flush=True)
            time.sleep(2)


def nearest_tick_at_or_before(ticks: Iterable[tuple[int, float]], target_ms: int, max_lag_ms: int) -> Optional[float]:
    selected: Optional[tuple[int, float]] = None
    for ts_ms, price in ticks:
        if ts_ms <= target_ms and (selected is None or ts_ms > selected[0]):
            selected = (ts_ms, price)
    if selected is None or target_ms - selected[0] > max_lag_ms:
        return None
    return selected[1]


def realized_velocity(ticks: Iterable[tuple[int, float]], window_ms: int = 2000) -> Optional[float]:
    values = list(ticks)
    if len(values) < 2:
        return None
    end_ts, end_price = values[-1]
    cutoff = end_ts - max(1, window_ms)
    start_ts, start_price = values[0]
    for ts_ms, price in reversed(values):
        if ts_ms <= cutoff:
            start_ts, start_price = ts_ms, price
            break
    dt_s = (end_ts - start_ts) / 1000.0
    if dt_s <= 0 or not math.isfinite(dt_s):
        return None
    return (end_price - start_price) / dt_s
