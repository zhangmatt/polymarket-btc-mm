from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, Iterable, Optional, Tuple


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def gaussian_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def inv_gaussian_cdf(p: float) -> float:
    """Acklam inverse-normal approximation."""
    if p <= 0.0:
        return -float("inf")
    if p >= 1.0:
        return float("inf")

    a = [-39.69683028665376, 220.9460984245205, -275.9285104469687,
         138.3577518672690, -30.66479806614716, 2.506628277459239]
    b = [-54.47609879822406, 161.5858368580409, -155.6989798598866,
         66.80131188771972, -13.28068155288572]
    c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838,
         -2.549732539343734, 4.374664141464968, 2.938163982698783]
    d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996,
         3.754408661907416]

    plow = 0.02425
    phigh = 1.0 - plow
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        numerator = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
        denominator = ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
        return numerator / denominator
    if p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        numerator = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
        denominator = ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
        return -numerator / denominator

    q = p - 0.5
    r = q * q
    numerator = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
    denominator = (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    return numerator / denominator


@dataclass(frozen=True)
class BinaryFairValue:
    up: float
    down: float
    z: float
    z_spot: float
    z_drift: float


class RollingVolatility:
    """Per-second realized volatility from rolling log returns."""

    def __init__(self, window_s: float = 120.0):
        if window_s <= 0:
            raise ValueError("window_s must be positive")
        self.window_ms = int(window_s * 1000)
        self.prices: Deque[Tuple[int, float]] = deque()

    def update(self, ts_ms: int, price: float) -> None:
        if price <= 0:
            return
        self.prices.append((int(ts_ms), float(price)))
        cutoff = int(ts_ms) - self.window_ms
        while self.prices and self.prices[0][0] < cutoff:
            self.prices.popleft()

    def sigma_per_s(self) -> Optional[float]:
        if len(self.prices) < 3:
            return None
        variances = []
        for (ts0, p0), (ts1, p1) in zip(self.prices, list(self.prices)[1:]):
            dt_s = (ts1 - ts0) / 1000.0
            if dt_s <= 0 or p0 <= 0 or p1 <= 0:
                continue
            ret = math.log(p1 / p0)
            variances.append((ret * ret) / dt_s)
        if not variances:
            return None
        return math.sqrt(sum(variances) / len(variances))


def estimate_sigma_from_ticks(ticks: Iterable[Tuple[int, float]], window_s: float = 120.0) -> Optional[float]:
    estimator = RollingVolatility(window_s)
    for ts_ms, price in ticks:
        estimator.update(ts_ms, price)
    return estimator.sigma_per_s()


def gbm_binary_fair_value(
    *,
    s0: float,
    spot: float,
    sigma_per_s: float,
    time_remaining_s: float,
    drift_per_s: float = 0.0,
    drift_gate_z: float = 0.08,
    drift_cap_z: float = 0.35,
    min_probability: float = 0.01,
    max_probability: float = 0.99,
) -> BinaryFairValue:
    """Price a BTC up/down binary as P(S_T > S0) under a short-horizon GBM."""
    if time_remaining_s <= 0:
        up = 1.0 if spot >= s0 else 0.0
        return BinaryFairValue(up=up, down=1.0 - up, z=math.inf if up else -math.inf, z_spot=0.0, z_drift=0.0)
    if s0 <= 0 or spot <= 0 or sigma_per_s <= 0:
        return BinaryFairValue(up=0.5, down=0.5, z=0.0, z_spot=0.0, z_drift=0.0)

    denom = sigma_per_s * math.sqrt(time_remaining_s)
    z_spot = math.log(spot / s0) / denom
    z_drift_raw = drift_per_s * math.sqrt(time_remaining_s) / sigma_per_s
    z_drift = 0.0 if abs(z_drift_raw) < drift_gate_z else clamp(z_drift_raw, -drift_cap_z, drift_cap_z)
    z = z_spot + z_drift
    up = clamp(gaussian_cdf(z), min_probability, max_probability)
    return BinaryFairValue(up=up, down=1.0 - up, z=z, z_spot=z_spot, z_drift=z_drift)


def implied_sigma_from_mid(
    *,
    p_up: float,
    s0: float,
    spot: float,
    time_remaining_s: float,
    drift_per_s: float = 0.0,
) -> Optional[float]:
    """Invert the GBM binary model to infer sigma from a market mid probability."""
    if time_remaining_s <= 0 or s0 <= 0 or spot <= 0:
        return None
    p = clamp(p_up, 0.01, 0.99)
    z = inv_gaussian_cdf(p)
    if abs(z) < 1e-9:
        return None
    numerator = math.log(spot / s0) + drift_per_s * time_remaining_s
    denominator = z * math.sqrt(time_remaining_s)
    sigma = abs(numerator / denominator)
    return sigma if sigma > 0 else None


def blend_sigma(
    realized_sigma: Optional[float],
    implied_sigma: Optional[float],
    implied_weight: float,
    min_sigma: float,
) -> Optional[float]:
    if realized_sigma is None and implied_sigma is None:
        return None
    weight = clamp(implied_weight, 0.0, 1.0)
    if realized_sigma is None:
        sigma = implied_sigma
    elif implied_sigma is None:
        sigma = realized_sigma
    else:
        sigma = (1.0 - weight) * realized_sigma + weight * implied_sigma
    return max(float(min_sigma), float(sigma))
