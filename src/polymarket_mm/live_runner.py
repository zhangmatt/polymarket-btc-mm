from __future__ import annotations

import argparse
import ssl
import threading
import time
from typing import Optional

import requests

from .clob import ClobConfig, PolymarketClobExecution, create_authenticated_client, resting_orders_from_open_orders
from .config import LiveConfig
from .execution import RestingOrder, SimulatedExecutionClient, execute_cancel_then_post
from .fair_value import blend_sigma, implied_sigma_from_mid
from .gamma import compute_market_slug, gamma_get_market, parse_market_tokens
from .market_data import (
    BinanceTradeStream,
    OrderBookRestPoller,
    OrderBookStore,
    PolymarketMarketStream,
    nearest_tick_at_or_before,
    now_ms,
)
from .merge import CompleteSetMerger, MergeConfig, build_relayer_client, connect_polygon_web3
from .positions import DataApiPositionTracker, fetch_ctf_positions, fetch_usdc_balance
from .quoting import QuoteConfig
from .risk import Inventory, RiskConfig
from .strategy import MarketMakerStrategy, MarketSnapshot, StrategyConfig


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Run the BTC 15m maker-only quote loop.")
    parser.add_argument("--dry-run", action="store_true", help="simulate order state locally")
    parser.add_argument("--live", action="store_true", help="place live post-only orders")
    parser.add_argument("--market", default=None, help="'current', 'next', or a market slug")
    parser.add_argument("--activity-endpoint", choices=["activity", "trades"], default="activity")
    parser.add_argument("--insecure-ws", action="store_true", help="disable websocket certificate checks")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    config = LiveConfig.from_env()
    if args.market:
        config = _replace(config, market=args.market)
    if args.live:
        config = _replace(config, dry_run=False)
    if args.dry_run:
        config = _replace(config, dry_run=True)
    if not config.dry_run:
        config.validate_for_live_orders()

    session = requests.Session()
    slug = compute_market_slug(time.time(), config.market)
    market = gamma_get_market(slug, gamma_url=config.gamma_url, session=session, retries=30, retry_s=2.0)
    tokens = parse_market_tokens(slug, market)
    print(f"[market] {tokens.slug} condition={tokens.condition_id}", flush=True)

    sslopt = {"cert_reqs": ssl.CERT_NONE} if args.insecure_ws else None
    update_event = threading.Event()
    book_store = OrderBookStore([tokens.up_token, tokens.down_token])
    market_ws = PolymarketMarketStream(
        ws_url=config.market_ws_url,
        asset_ids=[tokens.up_token, tokens.down_token],
        store=book_store,
        sslopt=sslopt,
        on_update=update_event,
        verbose=args.verbose,
    )
    rest_poller = OrderBookRestPoller(
        clob_host=config.clob_host,
        asset_ids=[tokens.up_token, tokens.down_token],
        store=book_store,
        session=session,
    )
    spot_stream = BinanceTradeStream(
        symbol=config.symbol,
        ws_base=config.binance_ws_base,
        vol_window_s=config.vol_window_s,
        sslopt=sslopt,
        verbose=args.verbose,
    )
    market_ws.start()
    spot_stream.start()

    w3 = None
    merger = None
    if not config.dry_run:
        try:
            w3 = connect_polygon_web3()
            relayer = build_relayer_client(config.credentials, MergeConfig())
            if relayer is not None:
                merger = CompleteSetMerger(
                    relayer=relayer,
                    w3=w3,
                    proxy_wallet=config.credentials.funder,
                )
        except Exception as exc:
            print(f"[merge] disabled: {exc}", flush=True)

    tracker = DataApiPositionTracker(
        wallet=config.credentials.funder or "0x0000000000000000000000000000000000000000",
        condition_id=tokens.condition_id,
        up_token=tokens.up_token,
        down_token=tokens.down_token,
        slug=tokens.slug,
        data_api_url=config.data_api_url,
        endpoint=args.activity_endpoint,
        start_ts=tokens.start_ts,
        session=session,
    )
    if w3 is not None and config.credentials.funder:
        onchain = fetch_ctf_positions(w3, config.credentials.funder, tokens.up_token, tokens.down_token)
        if onchain is not None:
            tracker.seed(up=onchain[0], down=onchain[1], usdc=fetch_usdc_balance(w3, config.credentials.funder))

    strategy = MarketMakerStrategy(
        StrategyConfig(
            up_token=tokens.up_token,
            down_token=tokens.down_token,
            quote=QuoteConfig(base_size=config.order_size),
            risk=RiskConfig(
                max_position_shares=config.max_position_shares,
                imbalance_threshold=config.imbalance_threshold,
                cash_threshold_usdc=config.cash_threshold_usdc,
                merge_min_shares=config.merge_min_shares,
            ),
            fast_velocity_threshold=config.fast_velocity_threshold,
            directional_velocity_threshold=config.directional_velocity_threshold,
            allow_pause=config.allow_pause,
            always_requote=config.always_requote,
        )
    )

    if config.dry_run:
        executor = SimulatedExecutionClient()
        clob_executor = None
    else:
        clob = create_authenticated_client(ClobConfig(host=config.clob_host), config.credentials)
        clob_executor = PolymarketClobExecution(clob, post_only=True)
        executor = clob_executor

    s0 = wait_for_start_price(spot_stream, tokens.start_ts, max_lag_ms=10_000)
    print(f"[open] s0={s0:.2f} source=binance", flush=True)

    current_orders: list[RestingOrder] = []
    last_position_poll = 0.0
    last_reconcile = 0.0
    inventory = tracker.inventory()

    try:
        while time.time() < tokens.end_ts:
            update_event.wait(timeout=0.05)
            update_event.clear()
            rest_poller.poll()

            now_s = time.time()
            if now_s - last_position_poll >= config.position_poll_s:
                inventory = tracker.poll()
                if w3 is not None and config.credentials.funder:
                    inventory = _with_usdc(inventory, fetch_usdc_balance(w3, config.credentials.funder))
                    tracker.set_usdc(inventory.usdc)
                last_position_poll = now_s

            if clob_executor is not None and now_s - last_reconcile >= config.reconcile_open_orders_s:
                current_orders = resting_orders_from_open_orders(clob_executor.fetch_open_orders(tokens.condition_id))
                last_reconcile = now_s
            elif isinstance(executor, SimulatedExecutionClient):
                current_orders = list(executor.live.values())

            if now_s < tokens.start_ts + config.post_delay_s:
                continue

            last_price = spot_stream.last_price()
            if last_price is None:
                continue
            _, spot = last_price
            up_book = book_store.snapshot(tokens.up_token)
            down_book = book_store.snapshot(tokens.down_token)
            if up_book.bid_cents is None or up_book.ask_cents is None:
                continue
            if down_book.bid_cents is None or down_book.ask_cents is None:
                continue

            mid_up = (up_book.bid_cents + up_book.ask_cents) / 200.0
            implied = implied_sigma_from_mid(
                p_up=mid_up,
                s0=s0,
                spot=spot,
                time_remaining_s=max(0.0, tokens.end_ts - now_s),
            )
            sigma = blend_sigma(spot_stream.sigma_per_s(), implied, implied_weight=1.0, min_sigma=config.min_sigma)
            if sigma is None:
                continue

            decision = strategy.decide(
                MarketSnapshot(
                    ts_ms=now_ms(),
                    s0=s0,
                    spot=spot,
                    time_remaining_s=max(0.0, tokens.end_ts - now_s),
                    sigma_per_s=sigma,
                    up_book=up_book,
                    down_book=down_book,
                    velocity=spot_stream.velocity(),
                ),
                inventory,
                current_orders,
            )
            if decision.execution.changed:
                new_ids = execute_cancel_then_post(executor, decision.execution)
                print(
                    "[quote] "
                    f"regime={decision.quotes.regime} "
                    f"up={decision.quotes.up.price_cents if decision.quotes.up else None} "
                    f"down={decision.quotes.down.price_cents if decision.quotes.down else None} "
                    f"new_ids={len(new_ids)} imbalance={inventory.imbalance:.2f}",
                    flush=True,
                )
                if clob_executor is None:
                    current_orders = list(executor.live.values())

            if decision.risk.should_merge and merger is not None:
                if merger.merge(condition_id=tokens.condition_id, shares=decision.risk.merge_size):
                    print(f"[merge] merged {decision.risk.merge_size:.2f} complete sets", flush=True)
    finally:
        market_ws.stop()
        spot_stream.stop()


def wait_for_start_price(stream: BinanceTradeStream, start_ts: int, max_lag_ms: int) -> float:
    target_ms = start_ts * 1000
    while now_ms() < target_ms:
        time.sleep(0.01)
    deadline = time.time() + 10.0
    while time.time() < deadline:
        price = nearest_tick_at_or_before(stream.history(), target_ms, max_lag_ms)
        if price is not None:
            return price
        time.sleep(0.05)
    last = stream.last_price()
    if last is None:
        raise RuntimeError("no Binance price available for market start")
    return last[1]


def _with_usdc(inventory: Inventory, usdc: Optional[float]) -> Inventory:
    return Inventory(up=inventory.up, down=inventory.down, usdc=usdc)


def _replace(config: LiveConfig, **kwargs: object) -> LiveConfig:
    data = config.__dict__.copy()
    data.update(kwargs)
    return LiveConfig(**data)


if __name__ == "__main__":
    main()
