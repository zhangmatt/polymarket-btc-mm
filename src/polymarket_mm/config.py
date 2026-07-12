from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping, Optional


@dataclass(frozen=True)
class Credentials:
    private_key: str = field(default="", repr=False)
    funder: str = ""
    signature_type: int = 2
    relayer_api_key: str = field(default="", repr=False)
    relayer_api_secret: str = field(default="", repr=False)
    relayer_passphrase: str = field(default="", repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Credentials":
        return cls(
            private_key=env.get("POLY_PRIVATE_KEY", ""),
            funder=env.get("POLY_FUNDER", ""),
            signature_type=int(env.get("POLY_SIGNATURE_TYPE", "2")),
            relayer_api_key=env.get("POLY_RELAYER_API_KEY", ""),
            relayer_api_secret=env.get("POLY_RELAYER_API_SECRET", ""),
            relayer_passphrase=env.get("POLY_RELAYER_PASSPHRASE", ""),
        )

    def missing_clob_fields(self) -> list[str]:
        missing = []
        if not self.private_key:
            missing.append("POLY_PRIVATE_KEY")
        if not self.funder:
            missing.append("POLY_FUNDER")
        return missing

    def has_relayer_credentials(self) -> bool:
        return bool(self.relayer_api_key and self.relayer_api_secret and self.relayer_passphrase and self.private_key)


@dataclass(frozen=True)
class LiveConfig:
    clob_host: str = "https://clob.polymarket.com"
    gamma_url: str = "https://gamma-api.polymarket.com"
    data_api_url: str = "https://data-api.polymarket.com"
    market_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com"
    binance_ws_base: str = "wss://stream.binance.com:9443/ws"
    symbol: str = "btcusdt"
    market: str = "current"
    dry_run: bool = True
    order_size: float = 5.0
    max_position_shares: float = 5000.0
    imbalance_threshold: float = 20.0
    cash_threshold_usdc: float = 20.0
    merge_min_shares: float = 1.0
    position_poll_s: float = 0.25
    reconcile_open_orders_s: float = 1.0
    post_delay_s: float = 60.0
    vol_window_s: float = 120.0
    min_sigma: float = 1e-5
    fast_velocity_threshold: float = 9.5
    directional_velocity_threshold: float = 1.0
    allow_pause: bool = True
    always_requote: bool = False
    credentials: Credentials = field(default_factory=Credentials.from_env)

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "LiveConfig":
        def get_bool(name: str, default: bool) -> bool:
            raw = env.get(name)
            if raw is None:
                return default
            return raw.strip().lower() in {"1", "true", "yes", "on"}

        return cls(
            clob_host=env.get("CLOB_HOST", cls.clob_host),
            gamma_url=env.get("GAMMA_URL", cls.gamma_url),
            data_api_url=env.get("DATA_API_URL", cls.data_api_url),
            market_ws_url=env.get("POLYMARKET_MARKET_WS_URL", cls.market_ws_url),
            binance_ws_base=env.get("BINANCE_WS_BASE", cls.binance_ws_base),
            symbol=env.get("BINANCE_SYMBOL", cls.symbol),
            market=env.get("MARKET", env.get("WHICH", cls.market)),
            dry_run=get_bool("DRY_RUN", cls.dry_run),
            order_size=float(env.get("ORDER_SIZE", str(cls.order_size))),
            max_position_shares=float(env.get("MAX_POSITION_SHARES", str(cls.max_position_shares))),
            imbalance_threshold=float(env.get("IMBALANCE_THRESHOLD", str(cls.imbalance_threshold))),
            cash_threshold_usdc=float(env.get("CASH_THRESHOLD_USDC", str(cls.cash_threshold_usdc))),
            merge_min_shares=float(env.get("MERGE_MIN_SHARES", str(cls.merge_min_shares))),
            position_poll_s=float(env.get("POSITION_POLL_S", str(cls.position_poll_s))),
            reconcile_open_orders_s=float(env.get("RECONCILE_OPEN_ORDERS_S", str(cls.reconcile_open_orders_s))),
            post_delay_s=float(env.get("POST_DELAY_S", str(cls.post_delay_s))),
            vol_window_s=float(env.get("VOL_WINDOW_S", str(cls.vol_window_s))),
            min_sigma=float(env.get("MIN_SIGMA", str(cls.min_sigma))),
            fast_velocity_threshold=float(env.get("MOMENTUM_FAST_THRESHOLD", str(cls.fast_velocity_threshold))),
            directional_velocity_threshold=float(
                env.get("MOMENTUM_DIRECTIONAL_THRESHOLD", str(cls.directional_velocity_threshold))
            ),
            allow_pause=not get_bool("DISABLE_MOMENTUM_PAUSE", False),
            always_requote=get_bool("ALWAYS_REQUOTE", cls.always_requote),
            credentials=Credentials.from_env(env),
        )

    def validate_for_live_orders(self) -> None:
        missing = self.credentials.missing_clob_fields()
        if missing:
            raise ValueError("missing live order credentials: " + ", ".join(missing))


def mask_secret(value: Optional[str], keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}...{value[-keep:]}"
