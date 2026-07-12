# Core system notes

## What this branch keeps

The working system in the research repo was the RTDS/GBM maker path:

- Binance BTC ticks supplied the spot input and rolling volatility sample.
- `S0` was the market-start BTC price.
- GBM priced `P(BTC_end > S0)` for the Up token and `1 - P(up)` for Down.
- The maker loop posted post-only BUY orders on Up/Down, never intentional taker orders.
- Quotes were cancel/replaced when fair value, regime, size, or inventory changed.
- Inventory risk limited per-side exposure and quoted farther back on the overweight side.
- Complete-set logic tracked `min(up, down)` as mergeable inventory when cash fell below a threshold.
- Step 1 validation logs fed calibration, markout, and volatility-model sweeps.

## What was left out

The private repo also had taker experiments, copy-trading scripts, wallet trackers, generated plots,
raw JSONL logs, notebooks-by-script, old README drafts, and several dead-end volatility/microprice tests.
Those are useful research history but make a poor public codebase, so this branch omits them.

## Known limitations

The public package is not a drop-in live bot. It is the strategy core. A live runner still needs:

- market discovery and token-id resolution,
- CLOB websocket/orderbook ingestion,
- authenticated order placement,
- on-chain or data-api inventory reconciliation,
- complete-set merge/redeem integration,
- rate-limit and reconnect handling.

Those pieces were present in the private monolithic script, but they mixed credentials, logs, and private
operational details with strategy code. The public version favors reviewability over live deployment.

