from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Iterable, Optional

from .fair_value import gbm_binary_fair_value
from .quoting import BestBidAsk, QuoteConfig, build_maker_quotes
from .risk import Inventory, RiskConfig, evaluate_quote_risk


@dataclass(frozen=True)
class FairLogRow:
    ts_ms: int
    s0: float
    spot: float
    t_rem_s: float
    sigma: float
    p_up: float
    up_bid_c: Optional[int]
    up_ask_c: Optional[int]
    down_bid_c: Optional[int]
    down_ask_c: Optional[int]


@dataclass(frozen=True)
class CalibrationResult:
    rows: int
    windows: int
    brier_fair: float
    brier_mid: Optional[float]
    mean_abs_fair_vs_mid: Optional[float]


@dataclass(frozen=True)
class Fill:
    ts_ms: int
    asset: str
    price_cents: int
    size: float


@dataclass(frozen=True)
class MakerBacktestResult:
    rows: int
    fills: list[Fill]
    gross_cost: float
    terminal_value: float
    pnl: float
    final_inventory: Inventory


def _optional_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except Exception:
        return None


def iter_jsonl(path: str | Path) -> Iterable[dict]:
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def load_fair_log(path: str | Path) -> list[FairLogRow]:
    rows: list[FairLogRow] = []
    for raw in iter_jsonl(path):
        try:
            ts_ms = int(raw["ts_ms"])
            s0 = float(raw["s0"])
            spot = float(raw.get("s") or raw.get("spot") or raw.get("bn_price"))
            t_rem = float(raw["t_rem_s"])
            sigma = float(raw["sigma"])
            p_up = float(raw.get("p_up") or raw.get("fair_up"))
        except Exception:
            continue
        rows.append(
            FairLogRow(
                ts_ms=ts_ms,
                s0=s0,
                spot=spot,
                t_rem_s=t_rem,
                sigma=sigma,
                p_up=p_up,
                up_bid_c=_optional_int(raw.get("up_bid_c")),
                up_ask_c=_optional_int(raw.get("up_ask_c")),
                down_bid_c=_optional_int(raw.get("down_bid_c")),
                down_ask_c=_optional_int(raw.get("down_ask_c")),
            )
        )
    rows.sort(key=lambda row: row.ts_ms)
    return rows


def resolved_up(rows: list[FairLogRow]) -> Optional[bool]:
    if not rows:
        return None
    last = min(rows, key=lambda row: row.t_rem_s)
    if last.s0 <= 0 or last.spot <= 0:
        return None
    return last.spot >= last.s0


def calibration(rows_by_window: Iterable[list[FairLogRow]]) -> CalibrationResult:
    fair_errors: list[float] = []
    mid_errors: list[float] = []
    fair_mid_diffs: list[float] = []
    windows = 0
    for rows in rows_by_window:
        outcome_bool = resolved_up(rows)
        if outcome_bool is None:
            continue
        windows += 1
        outcome = 1.0 if outcome_bool else 0.0
        for row in rows:
            fair_errors.append((row.p_up - outcome) ** 2)
            if row.up_bid_c is not None and row.up_ask_c is not None:
                mid = ((row.up_bid_c + row.up_ask_c) / 2.0) / 100.0
                mid_errors.append((mid - outcome) ** 2)
                fair_mid_diffs.append(abs(row.p_up - mid))

    return CalibrationResult(
        rows=len(fair_errors),
        windows=windows,
        brier_fair=mean(fair_errors) if fair_errors else float("nan"),
        brier_mid=mean(mid_errors) if mid_errors else None,
        mean_abs_fair_vs_mid=mean(fair_mid_diffs) if fair_mid_diffs else None,
    )


def simulate_maker_quotes(
    rows: list[FairLogRow],
    *,
    up_token: str = "UP",
    down_token: str = "DOWN",
    quote_config: QuoteConfig = QuoteConfig(),
    risk_config: RiskConfig = RiskConfig(),
) -> MakerBacktestResult:
    """
    Simulate resting maker bids from fair log snapshots.

    A fill occurs when the next observed ask is at or below the resting bid.
    This is intentionally conservative: it does not assume queue priority and
    does not model partial fills.
    """
    if len(rows) < 2:
        return MakerBacktestResult(len(rows), [], 0.0, 0.0, 0.0, Inventory())

    inventory = Inventory()
    total_cost = 0.0
    fills: list[Fill] = []

    for current, nxt in zip(rows, rows[1:]):
        risk = evaluate_quote_risk(inventory, risk_config)
        fair = gbm_binary_fair_value(
            s0=current.s0,
            spot=current.spot,
            sigma_per_s=current.sigma,
            time_remaining_s=current.t_rem_s,
        )
        quotes = build_maker_quotes(
            up_token=up_token,
            down_token=down_token,
            fair_up=fair.up,
            up_book=BestBidAsk(current.up_bid_c, current.up_ask_c),
            down_book=BestBidAsk(current.down_bid_c, current.down_ask_c),
            regime="BOTH",
            config=quote_config,
            allow_up=risk.allow_up,
            allow_down=risk.allow_down,
            up_max_size=risk.up_max_size,
            down_max_size=risk.down_max_size,
            up_extra_back_cents=risk.up_extra_back_cents,
            down_extra_back_cents=risk.down_extra_back_cents,
        )

        for quote, next_ask in ((quotes.up, nxt.up_ask_c), (quotes.down, nxt.down_ask_c)):
            if quote is None or next_ask is None or next_ask > quote.price_cents:
                continue
            fills.append(Fill(nxt.ts_ms, quote.asset, quote.price_cents, quote.size))
            total_cost += quote.size * quote.price
            if quote.asset == up_token:
                inventory = Inventory(inventory.up + quote.size, inventory.down, inventory.usdc)
            else:
                inventory = Inventory(inventory.up, inventory.down + quote.size, inventory.usdc)

    outcome = resolved_up(rows)
    terminal = 0.0 if outcome is None else (inventory.up if outcome else inventory.down)
    return MakerBacktestResult(
        rows=len(rows),
        fills=fills,
        gross_cost=total_cost,
        terminal_value=terminal,
        pnl=terminal - total_cost,
        final_inventory=inventory,
    )


def write_fills_csv(fills: list[Fill], path: str | Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ts_ms", "asset", "price_cents", "size"])
        writer.writeheader()
        for fill in fills:
            writer.writerow(fill.__dict__)

