# Polymarket BTC 15m market maker

Maker-only trading engine for Polymarket BTC 15-minute Up/Down binary markets.

The system combines a GBM fair-value model with live market-data adapters, post-only CLOB
execution, cancel/replace planning, inventory controls, Data API reconciliation, and complete-set
merge handling. Runtime logs, generated plots, cache files, and credentials are excluded.

## Architecture
Data flow is split into small modules:
- `gamma.py`: BTC 15m market discovery and Up/Down token extraction.
- `market_data.py`: Polymarket market websocket parsing, reconnects, REST book fallback, Binance spot stream.
- `fair_value.py`: rolling volatility, GBM binary pricing, implied-vol inversion.
- `quoting.py`: quote regimes, half-cent fair rounding, post-only bid construction.
- `clob.py`: Polymarket CLOB authentication, signed order creation, batch posts, cancels, open-order reads.
- `execution.py`: cancel/replace planner and execution-client protocol.
- `positions.py`: Data API trade polling, local fill de-dupe, inventory state, onchain balance reads.
- `merge.py`: conditional-token complete-set merge adapter through the builder relayer.
- `attribution.py`: taker markouts and maker fill decomposition.
- `strategy.py`: one decision step wiring fair value, risk, quoting, and execution planning.
- `backtest.py`: fair-log loader, calibration metrics, and maker-fill simulation.
- `live_runner.py`: operational loop that connects the adapters and runs the strategy.

## Fair Value
Each market is priced as a short-dated binary option:
`P(up) = Phi(log(S / S0) / (sigma * sqrt(T)))`

`S0` is the latest Binance tick at or just before market start, `S` is current BTC spot, `T` is
seconds to expiry, and `sigma` is per-second volatility. The runner estimates realized volatility
from Binance ticks and can blend it with implied volatility from the Polymarket mid.

## Maker-Only Quoting
The strategy posts `postOnly=true` buy orders instead of crossing the spread. Fair probability is
converted to cents, rounded to the nearest half-cent, and quoted behind fair: whole-cent fair values
quote one cent back; half-cent fair values quote half a cent back. Quotes are capped below the
current ask so they rest on the book.

The quote loop supports `BOTH`, `UP_ONLY`, `DOWN_ONLY`, and `PAUSE` regimes from short-horizon BTC
velocity. Inventory risk widens the overweight side, enforces max exposure, and can trigger
complete-set merges when cash drops below a configured threshold.

## Run
```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest
```

Run the fair-log backtest on local logs:
```bash
python -m polymarket_mm.backtest_cli data/step1/runs/*/fair/rtds_fair_*.jsonl
```

Run the live loop in dry-run mode:
```bash
DRY_RUN=1 polymarket-mm-live --market next
```

Live order placement requires `POLY_PRIVATE_KEY`, `POLY_FUNDER`, and the optional relayer variables
shown in `.env.example`.

The strategy was not reliably profitable in the analyzed artifacts. The main issues were fees,
adverse selection, queue position, and stale quotes during fast BTC moves. The maker-only design was
the engineering response: avoid taker crossing, keep post-only orders, cap inventory, and use
complete-set merges to recycle capital.
