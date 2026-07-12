# Polymarket BTC 15m market maker

Market-making engine for Polymarket BTC 15-minute Up/Down binary markets.

This is a cleaned public version of a live trading research project. It keeps the core strategy:
GBM fair value, post-only quote construction, cancel/replace planning, inventory skew, and a
fair-log backtest harness. Credentials, private logs, generated plots, and scratch research scripts
are excluded.

## Architecture
Data flows through a small set of modules:
- `fair_value.py`: rolling volatility, GBM binary pricing, implied-vol inversion.
- `quoting.py`: quote regimes, half-cent fair rounding, post-only bid construction.
- `execution.py`: cancel/replace planner and execution-client protocol.
- `risk.py`: inventory limits, imbalance skew, complete-set merge trigger.
- `strategy.py`: one decision step wiring fair value, risk, quoting, and execution planning.
- `backtest.py`: fair-log loader, calibration metrics, and maker-fill simulation.

The package is adapter-light by design. A live runner supplies market discovery, CLOB orderbook
events, authenticated order placement, wallet state, and secret management.

## Fair value
The model prices each market as a short-dated binary option:
`P(up) = Phi(log(S / S0) / (sigma * sqrt(T)))`

`S0` is BTC at market start, `S` is current BTC spot, `T` is seconds to expiry, and `sigma`
is per-second volatility. The original runner estimated realized volatility from Binance ticks
and optionally blended it with implied volatility from the Polymarket mid.

## Maker-only quoting
The strategy posts `postOnly=true` buy orders instead of crossing the spread. Fair probability is
converted to cents, rounded to the nearest half-cent, and quoted behind fair: whole-cent fair values
quote one cent back; half-cent fair values quote half a cent back. Quotes are capped below the
current ask so they remain maker orders.

The quote loop supports `BOTH`, `UP_ONLY`, `DOWN_ONLY`, and `PAUSE` regimes based on BTC velocity.
Inventory risk widens the overweight side and enforces per-side max exposure. When both sides are
quoted, bid sums are capped below $1.00 to preserve complete-set margin.

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

## Validation artifacts
Research artifacts from the private development repo:
- 20 fair-log files across 22 Step 1 collection runs.
- Volatility-model grid: 301 parameter rows over 11 resolved windows.
- Fixed-bid simulation: 85,475 quote samples; Down side filled 484 times (`0.566%`) with
  500ms markout of `-5.66c`; Up side had zero fills.
- Single-run calibration check: base GBM brier `0.056722`, market mid brier `0.056908`,
  pro-vol attempt brier `0.069436`.
- Rust pricing-path benchmark: Python `21.64us` per quote decision, Rust `2.80us`.

## Notes
This repo focuses on the strategy core. The private runner also handled websocket reconnects,
Polymarket CLOB authentication, order placement, position reconciliation, and complete-set merges.
Those operational components are intentionally separated from the public code.

