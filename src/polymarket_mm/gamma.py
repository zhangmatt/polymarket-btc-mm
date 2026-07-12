from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests


@dataclass(frozen=True)
class MarketTokens:
    condition_id: str
    up_token: str
    down_token: str
    slug: str
    start_ts: int
    end_ts: int


def compute_market_slug(ts: float, which: str = "current") -> str:
    if which not in {"current", "next"}:
        return which
    slot_start = (int(ts) // 900) * 900
    if which == "next":
        slot_start += 900
    return f"btc-updown-15m-{slot_start}"


def market_start_ts_from_slug(slug: str) -> int:
    return int(slug.rsplit("-", 1)[-1])


def market_end_ts_from_slug(slug: str) -> int:
    return market_start_ts_from_slug(slug) + 900


def gamma_get_market(
    slug: str,
    *,
    gamma_url: str = "https://gamma-api.polymarket.com",
    session: Optional[requests.Session] = None,
    retries: int = 30,
    retry_s: float = 2.0,
) -> dict[str, Any]:
    sess = session or requests.Session()
    base = gamma_url.rstrip("/")
    urls = [
        f"{base}/markets?slug={slug}",
        f"{base}/markets?limit=1&slug={slug}",
    ]
    last_error: Optional[Exception] = None
    for attempt in range(max(1, retries)):
        for url in urls:
            try:
                resp = sess.get(url, timeout=10)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                last_error = exc
                continue
            if isinstance(data, list) and data:
                return data[0]
            if isinstance(data, dict) and isinstance(data.get("data"), list) and data["data"]:
                return data["data"][0]
        if attempt + 1 < max(1, retries):
            time.sleep(max(0.1, retry_s))
    if last_error is not None:
        raise RuntimeError(f"Gamma market fetch failed for {slug}: {last_error}")
    raise RuntimeError(f"Gamma market fetch failed for {slug}: empty response")


def _maybe_json_list(value: Any) -> Any:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                return json.loads(stripped)
            except Exception:
                return value
    return value


def extract_condition_id(market: dict[str, Any]) -> str:
    condition_id = market.get("conditionId") or market.get("condition_id")
    if not condition_id:
        condition_ids = market.get("conditionIds") or market.get("condition_ids")
        if isinstance(condition_ids, list) and condition_ids:
            condition_id = condition_ids[0]
    if not isinstance(condition_id, str) or not condition_id.startswith("0x"):
        raise ValueError("could not extract conditionId from Gamma payload")
    return condition_id


def extract_token_ids(market: dict[str, Any]) -> tuple[str, str]:
    outcomes = _maybe_json_list(market.get("outcomes"))
    clob_ids = _maybe_json_list(market.get("clobTokenIds") or market.get("clob_token_ids"))

    if isinstance(outcomes, list) and isinstance(clob_ids, list) and len(outcomes) == len(clob_ids) >= 2:
        by_outcome = {str(outcomes[i]).strip().lower(): str(clob_ids[i]) for i in range(len(outcomes))}
        up = by_outcome.get("up")
        down = by_outcome.get("down")
        if up and down:
            return up, down
        return str(clob_ids[0]), str(clob_ids[1])

    tokens = _maybe_json_list(market.get("tokens"))
    if isinstance(tokens, list):
        up_token = None
        down_token = None
        fallback: list[str] = []
        for token in tokens:
            if not isinstance(token, dict):
                continue
            outcome = str(token.get("outcome") or token.get("name") or token.get("label") or "").strip().lower()
            token_id = (
                token.get("clobTokenId")
                or token.get("clob_token_id")
                or token.get("tokenId")
                or token.get("token_id")
                or token.get("id")
            )
            if not token_id:
                continue
            if outcome == "up":
                up_token = str(token_id)
            elif outcome == "down":
                down_token = str(token_id)
            else:
                fallback.append(str(token_id))
        if up_token and down_token:
            return up_token, down_token
        if len(fallback) >= 2:
            return fallback[0], fallback[1]

    for key in ("clobTokenIds", "clob_token_ids", "tokenIds", "token_ids"):
        token_ids = _maybe_json_list(market.get(key))
        if isinstance(token_ids, list) and len(token_ids) >= 2:
            return str(token_ids[0]), str(token_ids[1])

    raise ValueError("could not extract token ids from Gamma payload")


def parse_market_tokens(slug: str, market: dict[str, Any]) -> MarketTokens:
    up_token, down_token = extract_token_ids(market)
    return MarketTokens(
        condition_id=extract_condition_id(market),
        up_token=up_token,
        down_token=down_token,
        slug=slug,
        start_ts=market_start_ts_from_slug(slug),
        end_ts=market_end_ts_from_slug(slug),
    )
