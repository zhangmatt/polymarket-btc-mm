from __future__ import annotations

import argparse
from pathlib import Path

from .backtest import calibration, load_fair_log, simulate_maker_quotes, write_fills_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest fair-log JSONL files from the market maker.")
    parser.add_argument("fair_logs", nargs="+", help="paths to rtds_fair_*.jsonl files")
    parser.add_argument("--fills-csv", default="", help="optional output path for simulated fills")
    args = parser.parse_args()

    windows = [load_fair_log(path) for path in args.fair_logs]
    windows = [rows for rows in windows if rows]
    cal = calibration(windows)

    total_pnl = 0.0
    total_fills = []
    total_rows = 0
    for rows in windows:
        result = simulate_maker_quotes(rows)
        total_rows += result.rows
        total_pnl += result.pnl
        total_fills.extend(result.fills)

    print(f"windows={cal.windows}")
    print(f"rows={total_rows}")
    print(f"brier_fair={cal.brier_fair:.6f}")
    if cal.brier_mid is not None:
        print(f"brier_mid={cal.brier_mid:.6f}")
    if cal.mean_abs_fair_vs_mid is not None:
        print(f"mean_abs_fair_vs_mid={cal.mean_abs_fair_vs_mid:.6f}")
    print(f"simulated_fills={len(total_fills)}")
    print(f"simulated_pnl={total_pnl:.4f}")

    if args.fills_csv:
        out = Path(args.fills_csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        write_fills_csv(total_fills, out)


if __name__ == "__main__":
    main()

