# Metrics

Generated from local development artifacts that are excluded from the public repo:
`logs/`, `data/step1/runs/`, and `backtest_step1_out/results.csv`.

## Calibration And Backtest Coverage

- Fair-value log files: `20`
- Fair-value rows: `41,376`
- Valid in-window calibration sample rows: `39,978`
- Unique BTC 15m market windows in Step 1 logs: `13`
- Resolved windows used in the Step 1 grid: `11`
- Step 1 grid rows: `300` parameter/model rows

The claim "validated calibration over 10,000+ windows" is not supported by the available
artifacts. The defensible wording is: "validated calibration over 39,978 fair-value samples across
13 BTC 15m windows, with 11 resolved windows in the parameter grid."

Best Step 1 grid row by total simulated PnL:

- Model: `ewma_mr`
- Total simulated PnL: `-38002.75`
- Windows: `11`
- Simulated fills: `31005`
- Brier: `0.20012`

## Taker Execution Diagnosis

Aggregated across `19` non-empty taker trade logs:

- Execution rows: `2,424`
- Buys: `1,973`
- Sells: `451`
- Round trips: `155`
- Realized PnL: `-5106.064627` USDC

Mean markout per event:

- `300ms`: `-0.111593` USDC, `2374` samples
- `1000ms`: `-0.142744` USDC, `2374` samples
- `3000ms`: `-0.169152` USDC, `2374` samples
- `10000ms`: `-0.245744` USDC, `2374` samples

This supports the diagnosis that taker execution had adverse markouts in the analyzed logs.
Fees were not separately modeled in this artifact; the measured markouts were already negative
before adding any fee drag.

## Maker-Only Quote Simulation

Simple top-of-book crossing simulator over Step 1 quote logs.

Logged dry-run targets:

- Up: `393,677` samples, `0` simulated crosses
- Down: `393,620` samples, `0` simulated crosses

Fair-rounding quote rule, using the public strategy's nearest-half-cent/whole-cent bid logic:

- Up: `393,677` samples, `4,079` fills, `1.0361%` fill rate
- Down: `393,678` samples, `81,076` fills, `20.5945%` fill rate

Fill-time and 500ms attribution:

- Up fill-mid minus bid: `-2.404388c`; 500ms post-fill mid change: `+0.006006c`;
  500ms net future-mid minus bid: `-2.398382c`
- Down fill-mid minus bid: `-10.956387c`; 500ms post-fill mid change: `-0.057730c`;
  500ms net future-mid minus bid: `-11.014116c`

This supports the shift toward maker-only execution as a risk-control and experimentation path,
but it does not show a stable spread-capture edge in the available simulation.

## Quote-Loop Latency

Measured in the local Rust benchmark artifact:

- Python full pricing cycle: `21.64us`
- Rust full pricing cycle: `2.80us`
- Speedup: `7.7x`

This measures local pricing-path latency only. It excludes websocket parsing, JSON decode,
network round trips, exchange matching, and order acknowledgment latency.
