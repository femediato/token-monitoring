"""Point-in-time feature calculation.

`load_contexts` reads only rows observed at or before the evaluation time `at`, so the same
code can replay history without look-ahead. `compute_features` is a pure function of that
context; every value it returns is stored with the evaluation.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

HISTORY = timedelta(hours=5)  # enough for EMA50 on 5-minute samples and a 4h lookback
BUCKETS = 12  # 5-minute buckets in the last hour


@dataclass
class Snapshot:
    at: datetime
    price: float | None
    market_cap: float | None
    liquidity: float | None
    organic_label: str | None
    windows: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class TokenContext:
    mint: str
    pair_created_at: datetime | None
    pool_created_at: datetime | None
    snapshots: list[Snapshot]  # ascending by time, within HISTORY
    depth_now: float | None  # sqrt(base reserve * quote reserve) of the primary pool
    depth_24h: float | None
    depth_6h: float | None
    security: dict[str, Any] | None
    holders: dict[str, Any] | None
    quote: dict[str, Any] | None


def _ema(values: list[float], n: int) -> float:
    k = 2 / (n + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def _ratio(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _nearest(snaps: list[Snapshot], target: datetime, tolerance: timedelta) -> Snapshot | None:
    best = min(snaps, key=lambda s: abs(s.at - target), default=None)
    return best if best is not None and abs(best.at - target) <= tolerance else None


def _trades(w: dict[str, Any] | None) -> float | None:
    if not w or w.get("num_buys") is None or w.get("num_sells") is None:
        return None
    return float(w["num_buys"] + w["num_sells"])


def compute_features(ctx: TokenContext, at: datetime) -> dict[str, Any]:
    f: dict[str, Any] = {}
    snaps = [s for s in ctx.snapshots if s.at <= at]
    last = snaps[-1] if snaps else None
    f["snapshot_age_minutes"] = (at - last.at).total_seconds() / 60 if last else None
    f["age_days"] = (
        (at - ctx.pair_created_at).total_seconds() / 86400 if ctx.pair_created_at else None
    )
    f["pool_age_hours"] = (
        (at - ctx.pool_created_at).total_seconds() / 3600 if ctx.pool_created_at else None
    )
    w1 = last.windows.get("1h") if last else None
    w24 = last.windows.get("24h") if last else None
    f["market_cap"] = last.market_cap if last else None
    f["liquidity_usd"] = last.liquidity if last else None
    f["price_usd"] = last.price if last else None
    f["volume_24h"] = w24.get("volume_usd") if w24 else None
    f["trades_24h"] = _trades(w24)
    f["traders_24h"] = w24.get("num_traders") if w24 else None
    f["organic_label"] = last.organic_label if last else None

    # ---- safety inputs
    sec = ctx.security
    f["security_status"] = sec["status"] if sec else None
    f["risk_flags"] = list(sec["risk_flags"]) if sec else None
    f["security_age_hours"] = (at - sec["checked_at"]).total_seconds() / 3600 if sec else None
    h = ctx.holders
    f["top10_pct"] = h["top10_pct_adjusted"] if h else None
    f["top1_pct"] = h["top1_pct_adjusted"] if h else None
    f["holders_age_hours"] = (at - h["observed_at"]).total_seconds() / 3600 if h else None
    q = ctx.quote
    f["sell_cost_pct"] = q["cost_pct"] if q else None
    f["quote_error"] = q["error"] if q else None
    f["quote_age_minutes"] = (at - q["quoted_at"]).total_seconds() / 60 if q else None

    # ---- activity
    prev = _nearest(snaps, at - timedelta(hours=1), timedelta(minutes=10)) if last else None
    p1 = prev.windows.get("1h") if prev else None
    v1 = w1.get("volume_usd") if w1 else None
    v24 = f["volume_24h"]
    f["volume_1h"] = v1
    f["relvol_ratio"] = _ratio(v1, v24 / 24 if v24 else None)
    f["trader_growth_ratio"] = _ratio(
        w1.get("num_traders") if w1 else None, p1.get("num_traders") if p1 else None
    )
    f["tx_accel_ratio"] = _ratio(_trades(w1), _trades(p1))
    recent = [s for s in snaps if s.at > at - timedelta(minutes=60)]
    five = [s.windows["5m"] for s in recent if "5m" in s.windows][-BUCKETS:]
    f["buckets_observed"] = len(five)
    if v1 and five:
        f["active_buckets"] = sum(1 for w in five if (w.get("volume_usd") or 0) >= v1 / 24)
    else:
        f["active_buckets"] = None
    f["positive_buckets"] = (
        sum(1 for w in five if (w.get("buy_volume_usd") or 0) > (w.get("sell_volume_usd") or 0))
        if five
        else None
    )

    # ---- momentum (from our own 5-minute price samples, identical for every token)
    f["ret_1h_pct"] = w1.get("price_change_pct") if w1 else None
    p4 = _nearest(snaps, at - timedelta(hours=4), timedelta(minutes=10))
    f["ret_4h_pct"] = (
        (last.price / p4.price - 1) * 100 if last and p4 and last.price and p4.price else None
    )
    prices = [s.price for s in snaps if s.price]
    f["prices_n"] = len(prices)
    f["ema_ratio"] = _ema(prices, 20) / _ema(prices, 50) - 1 if len(prices) >= 50 else None
    f["ema20_gap"] = prices[-1] / _ema(prices, 20) - 1 if len(prices) >= 20 else None
    hi = [s.price for s in snaps if s.price and s.at >= at - timedelta(hours=4)]
    f["dist_from_4h_high_pct"] = (
        (1 - last.price / max(hi)) * 100 if last and last.price and hi else None
    )

    # ---- liquidity
    f["vol_liq_ratio"] = _ratio(v24, f["liquidity_usd"])
    f["depth_ratio_24h"] = _ratio(ctx.depth_now, ctx.depth_24h)
    f["depth_ratio_6h"] = _ratio(ctx.depth_now, ctx.depth_6h)

    # ---- buying pressure (1h)
    bv = w1.get("buy_volume_usd") if w1 else None
    sv = w1.get("sell_volume_usd") if w1 else None
    f["buy_share_1h"] = (
        _ratio(bv, (bv or 0) + (sv or 0)) if bv is not None and sv is not None else None
    )
    f["net_buy_liq_1h"] = (
        _ratio(bv - sv, f["liquidity_usd"]) if bv is not None and sv is not None else None
    )
    return f


def _depth(base: float | None, quote: float | None) -> float | None:
    if not base or not quote or base <= 0 or quote <= 0:
        return None
    return math.sqrt(base * quote)


async def load_contexts(session: AsyncSession, at: datetime) -> dict[str, TokenContext]:
    """Contexts for every active token, using only data observed at or before `at`."""
    params = {"at": at, "since": at - HISTORY}
    tokens = (
        await session.execute(
            text(
                """
                SELECT t.mint, t.pair_created_at, t.primary_pool, p.pair_created_at AS pool_created
                FROM tokens t LEFT JOIN pools p ON p.address = t.primary_pool
                WHERE t.tracking_status = 'active'
                """
            )
        )
    ).all()
    snaps: dict[str, dict[datetime, Snapshot]] = defaultdict(dict)
    rows = await session.execute(
        text(
            """
            SELECT s.token_mint, s.observed_at, s.price_usd, s.market_cap, s.liquidity_usd,
                   s.organic_score_label, w."window", w.volume_usd, w.buy_volume_usd,
                   w.sell_volume_usd, w.num_buys, w.num_sells, w.num_traders, w.price_change_pct
            FROM market_snapshots s
            LEFT JOIN market_window_stats w ON w.token_mint = s.token_mint
                AND w.source = s.source AND w.observed_at = s.observed_at
                AND w."window" IN ('5m', '1h', '24h')
            WHERE s.source = 'jupiter' AND s.observed_at > :since AND s.observed_at <= :at
            ORDER BY s.observed_at
            """
        ),
        params,
    )
    for r in rows.mappings():
        snap = snaps[r["token_mint"]].get(r["observed_at"])
        if snap is None:
            snap = Snapshot(
                r["observed_at"],
                r["price_usd"],
                r["market_cap"],
                r["liquidity_usd"],
                r["organic_score_label"],
            )
            snaps[r["token_mint"]][r["observed_at"]] = snap
        if r["window"]:
            snap.windows[r["window"]] = dict(r)

    async def latest(sql: str) -> dict[str, dict[str, Any]]:
        res = await session.execute(text(sql), params)
        return {r["token_mint"]: dict(r) for r in res.mappings()}

    security = await latest(
        """SELECT DISTINCT ON (token_mint) token_mint, status, risk_flags, checked_at
           FROM token_security WHERE checked_at <= :at ORDER BY token_mint, checked_at DESC"""
    )
    holders = await latest(
        """SELECT DISTINCT ON (token_mint) token_mint, top10_pct_adjusted, top1_pct_adjusted,
                  observed_at
           FROM holder_snapshots
           WHERE observed_at <= :at AND error IS NULL AND method_version = 'v2'
           ORDER BY token_mint, observed_at DESC"""
    )
    quotes = await latest(
        """SELECT DISTINCT ON (token_mint) token_mint, cost_pct, error, quoted_at
           FROM swap_quotes
           WHERE quoted_at <= :at AND side = 'sell'
             -- paired quotes only (pre-v1.1 rows have neither a cost nor an error)
             AND (cost_pct IS NOT NULL OR error IS NOT NULL)
           ORDER BY token_mint, quoted_at DESC"""
    )
    depth: dict[tuple[str, str], float | None] = {}
    for label, target in (
        ("now", at),
        ("6h", at - timedelta(hours=6)),
        ("24h", at - timedelta(hours=24)),
    ):
        res = await session.execute(
            text(
                """
                SELECT DISTINCT ON (ps.pool_address) ps.pool_address, ps.reserve_base,
                       ps.reserve_quote
                FROM pool_snapshots ps JOIN tokens t ON t.primary_pool = ps.pool_address
                WHERE ps.observed_at BETWEEN :lo AND :hi
                ORDER BY ps.pool_address, abs(extract(epoch FROM ps.observed_at - :target))
                """
            ),
            {
                "lo": target - timedelta(minutes=30),
                "hi": min(target + timedelta(minutes=30), at),
                "target": target,
            },
        )
        for r in res.mappings():
            depth[(r["pool_address"], label)] = _depth(r["reserve_base"], r["reserve_quote"])

    out = {}
    for t in tokens:
        pool = t.primary_pool
        out[t.mint] = TokenContext(
            mint=t.mint,
            pair_created_at=t.pair_created_at,
            pool_created_at=t.pool_created,
            snapshots=sorted(snaps.get(t.mint, {}).values(), key=lambda s: s.at),
            depth_now=depth.get((pool, "now")) if pool else None,
            depth_24h=depth.get((pool, "24h")) if pool else None,
            depth_6h=depth.get((pool, "6h")) if pool else None,
            security=security.get(t.mint),
            holders=holders.get(t.mint),
            quote=quotes.get(t.mint),
        )
    return out
