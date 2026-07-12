from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional

import requests

from .market_data import now_ms
from .risk import Inventory

try:
    from web3 import Web3  # type: ignore
except Exception:  # pragma: no cover - live dependency
    Web3 = None  # type: ignore


DATA_API_BASE = "https://data-api.polymarket.com"
POLYGON_USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_CONTRACT_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

ERC20_BALANCE_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    }
]

CTF_BALANCE_ABI = [
    {
        "inputs": [
            {"name": "account", "type": "address"},
            {"name": "id", "type": "uint256"},
        ],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]


@dataclass(frozen=True)
class TradeEvent:
    asset: str
    side: str
    size: float
    timestamp: int
    transaction_hash: str
    condition_id: Optional[str] = None
    slug: str = ""
    outcome: str = ""
    price: Optional[float] = None

    @property
    def key(self) -> str:
        return "|".join(
            [
                self.transaction_hash,
                str(self.timestamp),
                self.side,
                self.condition_id or "",
                self.slug,
                self.outcome,
                self.asset,
                str(self.size),
                str(self.price or ""),
            ]
        )


class DataApiPositionTracker:
    """
    Maintain live inventory from Polymarket Data API trade events.

    The websocket user feed is useful for immediacy, but this tracker is the
    reconciliation path: it re-queries a short lookback, de-dupes fills, and
    keeps inventory aligned with Data API activity.
    """

    def __init__(
        self,
        *,
        wallet: str,
        condition_id: str,
        up_token: str,
        down_token: str,
        slug: Optional[str] = None,
        data_api_url: str = DATA_API_BASE,
        endpoint: str = "activity",
        start_ts: Optional[int] = None,
        lookback_s: int = 3,
        limit: int = 500,
        session: Optional[requests.Session] = None,
    ):
        if endpoint not in {"activity", "trades"}:
            raise ValueError("endpoint must be 'activity' or 'trades'")
        self.wallet = wallet.lower()
        self.condition_id = condition_id
        self.up_token = str(up_token)
        self.down_token = str(down_token)
        self.slug = slug
        self.data_api_url = data_api_url.rstrip("/")
        self.endpoint = endpoint
        self.cursor_ts = int(start_ts if start_ts is not None else time.time())
        self.lookback_s = max(0, int(lookback_s))
        self.limit = max(1, min(10000, int(limit)))
        self.session = session or requests.Session()
        self._up = 0.0
        self._down = 0.0
        self._usdc: Optional[float] = None
        self._seen: deque[str] = deque(maxlen=50_000)
        self._seen_set: set[str] = set()
        self.last_poll_ms = 0

    def seed(self, *, up: float, down: float, usdc: Optional[float] = None) -> None:
        self._up = float(up)
        self._down = float(down)
        self._usdc = usdc

    def set_usdc(self, usdc: Optional[float]) -> None:
        self._usdc = usdc

    def inventory(self) -> Inventory:
        return Inventory(up=self._up, down=self._down, usdc=self._usdc)

    def poll(self) -> Inventory:
        max_ts = self.cursor_ts
        for event in self._fetch_events():
            if event.timestamp > max_ts:
                max_ts = event.timestamp
            if event.key in self._seen_set:
                continue
            self._remember(event.key)
            if event.asset == self.up_token:
                self._up += event.size if event.side == "BUY" else -event.size
            elif event.asset == self.down_token:
                self._down += event.size if event.side == "BUY" else -event.size
        self._up = max(0.0, self._up)
        self._down = max(0.0, self._down)
        self.cursor_ts = max(self.cursor_ts, max_ts)
        self.last_poll_ms = now_ms()
        return self.inventory()

    def _remember(self, key: str) -> None:
        if len(self._seen) == self._seen.maxlen:
            old = self._seen.popleft()
            self._seen_set.discard(old)
        self._seen.append(key)
        self._seen_set.add(key)

    def _fetch_events(self) -> list[TradeEvent]:
        if self.endpoint == "activity":
            return self._fetch_activity_events()
        return self._fetch_trade_events()

    def _fetch_activity_events(self) -> list[TradeEvent]:
        start = max(0, self.cursor_ts - self.lookback_s)
        events: list[TradeEvent] = []
        offset = 0
        while True:
            params: dict[str, Any] = {
                "user": self.wallet,
                "type": "TRADE",
                "start": start,
                "limit": self.limit,
                "offset": offset,
                "sortBy": "TIMESTAMP",
                "sortDirection": "ASC",
            }
            rows = self._get_rows("/activity", params)
            if not rows:
                break
            events.extend(self._parse_rows(rows, start))
            if len(rows) < self.limit:
                break
            offset += self.limit
        return events

    def _fetch_trade_events(self) -> list[TradeEvent]:
        params: dict[str, Any] = {
            "user": self.wallet,
            "market": self.condition_id,
            "takerOnly": "false",
            "limit": self.limit,
            "offset": 0,
        }
        rows = self._get_rows("/trades", params)
        start = max(0, self.cursor_ts - self.lookback_s)
        return self._parse_rows(rows, start)

    def _get_rows(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            resp = self.session.get(f"{self.data_api_url}{path}", params=params, timeout=10)
            if resp.status_code == 429:
                return []
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict):
            for key in ("data", "results", "trades", "activity"):
                rows = data.get(key)
                if isinstance(rows, list):
                    return [row for row in rows if isinstance(row, dict)]
        return []

    def _parse_rows(self, rows: list[dict[str, Any]], start_ts: int) -> list[TradeEvent]:
        events: list[TradeEvent] = []
        for row in rows:
            event = _parse_trade_event(row)
            if event is None or event.timestamp < start_ts:
                continue
            if event.condition_id and event.condition_id != self.condition_id:
                continue
            if not event.condition_id and self.slug and event.slug and event.slug != self.slug:
                continue
            if event.asset not in {self.up_token, self.down_token}:
                continue
            events.append(event)
        events.sort(key=lambda event: event.timestamp)
        return events


def _parse_trade_event(row: dict[str, Any]) -> Optional[TradeEvent]:
    try:
        asset = str(row.get("asset") or row.get("asset_id") or "")
        side = str(row.get("side") or "").upper()
        size = float(row.get("size"))
        timestamp = int(row.get("timestamp") or 0)
    except Exception:
        return None
    if not asset or side not in {"BUY", "SELL"} or size <= 0 or timestamp <= 0:
        return None
    price = None
    try:
        if row.get("price") is not None:
            price = float(row.get("price"))
    except Exception:
        price = None
    return TradeEvent(
        asset=asset,
        side=side,
        size=size,
        timestamp=timestamp,
        transaction_hash=str(row.get("transactionHash") or row.get("transaction_hash") or ""),
        condition_id=row.get("conditionId") or row.get("condition_id") or row.get("market"),
        slug=str(row.get("slug") or ""),
        outcome=str(row.get("outcome") or ""),
        price=price,
    )


def fetch_usdc_balance(w3: Any, wallet: str) -> Optional[float]:
    if w3 is None or Web3 is None:
        return None
    try:
        contract = w3.eth.contract(address=Web3.to_checksum_address(POLYGON_USDC_ADDRESS), abi=ERC20_BALANCE_ABI)
        raw = contract.functions.balanceOf(Web3.to_checksum_address(wallet)).call()
        return float(raw) / 1_000_000.0
    except Exception:
        return None


def fetch_ctf_positions(w3: Any, wallet: str, up_token: str, down_token: str) -> Optional[tuple[float, float]]:
    if w3 is None or Web3 is None:
        return None
    try:
        contract = w3.eth.contract(address=Web3.to_checksum_address(CTF_CONTRACT_ADDRESS), abi=CTF_BALANCE_ABI)
        owner = Web3.to_checksum_address(wallet)
        up = contract.functions.balanceOf(owner, int(up_token)).call()
        down = contract.functions.balanceOf(owner, int(down_token)).call()
        return float(up) / 1_000_000.0, float(down) / 1_000_000.0
    except Exception:
        return None
