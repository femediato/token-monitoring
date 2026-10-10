"""Read-only data explorer API over the collected market data.

Every number returned here comes straight from stored observations; nothing is
interpolated or filled in. Missing values are returned as null.
"""

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from autolp.collector.schedule import expected_minutes
from autolp.config import get_settings
from autolp.db.base import utcnow
from autolp.monitoring.health import freshness, job_health

router = APIRouter(prefix="/api", tags=["explorer"])

# How far back to look for a token's "current" market snapshot. Active tokens refresh every
# 5 minutes and dormant ones hourly; anything older is reported as missing, not current.
LATEST_LOOKBACK = timedelta(hours=3)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(request.app.state.engine) as session:
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]


def _rows(rows: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in rows.mappings().all()]


# ---------------------------------------------------------------- token list

TOKEN_COLUMNS = """
    t.mint, t.symbol, t.name, t.classification, t.classification_reason,
    t.tracking_status, t.tracking_reason, t.tracking_changed_at, t.first_seen_at,
    t.pair_created_at, t.launchpad, t.tags, t.primary_pool,
    (extract(epoch FROM now() - t.pair_created_at) / 86400)::float8 AS age_days,
    s.observed_at, s.source_updated_at, s.price_usd, s.market_cap, s.fdv, s.liquidity_usd,
    s.holder_count, s.organic_score, s.top_holders_pct,
    w24.volume_usd AS volume_24h, w24.price_change_pct AS price_change_24h,
    w24.num_traders AS traders_24h, w24.num_buys + w24.num_sells AS trades_24h,
    w24.buy_volume_usd / nullif(w24.volume_usd, 0) * 100 AS buy_share_24h,
    w1.volume_usd AS volume_1h, w1.price_change_pct AS price_change_1h,
    w1.buy_volume_usd / nullif(w1.volume_usd, 0) * 100 AS buy_share_1h,
    sec.status AS security_status, sec.risk_flags,
    hold.top10_pct_adjusted, q.slippage_pct AS sell_slippage_pct, q.error AS quote_error
"""

TOKEN_JOINS = """
    LEFT JOIN LATERAL (
        SELECT * FROM market_snapshots ms
        WHERE ms.token_mint = t.mint AND ms.source = 'jupiter' AND ms.observed_at >= :since
        ORDER BY ms.observed_at DESC LIMIT 1
    ) s ON true
    LEFT JOIN market_window_stats w24 ON w24.token_mint = t.mint AND w24.source = s.source
        AND w24.observed_at = s.observed_at AND w24."window" = '24h'
    LEFT JOIN market_window_stats w1 ON w1.token_mint = t.mint AND w1.source = s.source
        AND w1.observed_at = s.observed_at AND w1."window" = '1h'
    LEFT JOIN LATERAL (
        SELECT status, risk_flags FROM token_security ts WHERE ts.token_mint = t.mint
        ORDER BY ts.checked_at DESC LIMIT 1
    ) sec ON true
    LEFT JOIN LATERAL (
        SELECT top10_pct_adjusted FROM holder_snapshots hs
        WHERE hs.token_mint = t.mint AND hs.error IS NULL
        ORDER BY hs.observed_at DESC LIMIT 1
    ) hold ON true
    LEFT JOIN LATERAL (
        SELECT slippage_pct, error FROM swap_quotes sq WHERE sq.token_mint = t.mint
        ORDER BY sq.quoted_at DESC LIMIT 1
    ) q ON true
"""

SORTABLE = {
    "symbol": "t.symbol",
    "first_seen_at": "t.first_seen_at",
    "age_days": "age_days",
    "price_usd": "s.price_usd",
    "market_cap": "s.market_cap",
    "liquidity_usd": "s.liquidity_usd",
    "volume_24h": "volume_24h",
    "volume_1h": "volume_1h",
    "price_change_1h": "price_change_1h",
    "price_change_24h": "price_change_24h",
    "traders_24h": "traders_24h",
    "trades_24h": "trades_24h",
    "buy_share_24h": "buy_share_24h",
    "holder_count": "s.holder_count",
    "top10_pct_adjusted": "hold.top10_pct_adjusted",
    "sell_slippage_pct": "sell_slippage_pct",
    "organic_score": "s.organic_score",
}


@router.get("/tokens")
async def list_tokens(
    session: Session,
    status: Literal["active", "dormant", "excluded", "all"] = "active",
    classification: str | None = None,
    security: Literal["ok", "risky", "unknown", "unchecked"] | None = None,
    q: str | None = Query(None, max_length=64, description="symbol, name or mint prefix"),
    min_liquidity: float | None = None,
    min_volume_24h: float | None = None,
    sort: str = "liquidity_usd",
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    if sort not in SORTABLE:
        raise HTTPException(400, f"sort must be one of {sorted(SORTABLE)}")
    where: list[str] = ["true"]
    params: dict[str, Any] = {"since": utcnow() - LATEST_LOOKBACK}
    if status != "all":
        where.append("t.tracking_status = :status")
        params["status"] = status
    if classification:
        where.append("t.classification = :classification")
        params["classification"] = classification
    if security == "unchecked":
        where.append("sec.status IS NULL")
    elif security:
        where.append("sec.status = :security")
        params["security"] = security
    if q:
        where.append("(t.symbol ILIKE :q OR t.name ILIKE :q OR t.mint LIKE :prefix)")
        params["q"], params["prefix"] = f"%{q}%", f"{q}%"
    if min_liquidity is not None:
        where.append("s.liquidity_usd >= :min_liq")
        params["min_liq"] = min_liquidity
    if min_volume_24h is not None:
        where.append("w24.volume_usd >= :min_vol")
        params["min_vol"] = min_volume_24h
    base = f"FROM tokens t {TOKEN_JOINS} WHERE {' AND '.join(where)}"
    total = await session.scalar(text(f"SELECT count(*) {base}"), params)
    rows = await session.execute(
        text(
            f"SELECT {TOKEN_COLUMNS} {base} "
            f"ORDER BY {SORTABLE[sort]} {order} NULLS LAST, t.mint LIMIT :limit OFFSET :offset"
        ),
        {**params, "limit": limit, "offset": offset},
    )
    return {"total": total, "limit": limit, "offset": offset, "items": _rows(rows)}


# ---------------------------------------------------------------- token detail


@router.get("/tokens/{mint}")
async def token_detail(session: Session, mint: str) -> dict[str, Any]:
    params = {"mint": mint, "since": utcnow() - LATEST_LOOKBACK}
    token = (
        (
            await session.execute(
                text(f"SELECT {TOKEN_COLUMNS} FROM tokens t {TOKEN_JOINS} WHERE t.mint = :mint"),
                params,
            )
        )
        .mappings()
        .first()
    )
    if token is None:
        raise HTTPException(404, "token not found")
    token_d: dict[str, Any] = dict(token)
    token_d["tags"] = token_d.get("tags") or []

    windows = _rows(
        await session.execute(
            text(
                """
                SELECT w.* FROM market_window_stats w
                WHERE w.token_mint = :mint AND w.source = 'jupiter' AND w.observed_at = (
                    SELECT max(observed_at) FROM market_snapshots
                    WHERE token_mint = :mint AND source = 'jupiter'
                )
                ORDER BY array_position(ARRAY['5m','1h','6h','24h'], w."window"::text)
                """
            ),
            params,
        )
    )
    pools = _rows(
        await session.execute(
            text(
                """
                SELECT p.address, p.dex_id, p.quote_symbol, p.quote_mint, p.labels,
                       p.pair_created_at, ps.observed_at, ps.liquidity_usd, ps.reserve_base,
                       ps.reserve_quote, ps.price_usd, ps.volume_24h, ps.buys_24h, ps.sells_24h,
                       p.address = t.primary_pool AS is_primary
                FROM pools p
                JOIN tokens t ON t.mint = p.token_mint
                LEFT JOIN LATERAL (
                    SELECT * FROM pool_snapshots x WHERE x.pool_address = p.address
                    ORDER BY x.observed_at DESC LIMIT 1
                ) ps ON true
                WHERE p.token_mint = :mint
                ORDER BY ps.liquidity_usd DESC NULLS LAST
                """
            ),
            params,
        )
    )
    security = (
        (
            await session.execute(
                text(
                    """
                SELECT checked_at, status, token_program, mint_authority, freeze_authority,
                       supply_raw::text AS supply_raw, decimals, extensions, risk_flags
                FROM token_security WHERE token_mint = :mint ORDER BY checked_at DESC LIMIT 1
                """
                ),
                params,
            )
        )
        .mappings()
        .first()
    )
    holders = (
        (
            await session.execute(
                text(
                    """
                SELECT observed_at, method_version, supply_ui, top10_pct_raw, top10_pct_adjusted,
                       excluded_pct, accounts, error
                FROM holder_snapshots WHERE token_mint = :mint
                ORDER BY observed_at DESC LIMIT 1
                """
                ),
                params,
            )
        )
        .mappings()
        .first()
    )
    discovery = (
        (
            await session.execute(
                text(
                    """
                SELECT count(*) AS appearances, min(discovered_at) AS first_at,
                       max(discovered_at) AS last_at, min(rank) AS best_rank,
                       array_agg(DISTINCT list_name ORDER BY list_name) AS lists
                FROM token_discoveries WHERE token_mint = :mint
                """
                ),
                params,
            )
        )
        .mappings()
        .first()
    )
    events = _rows(
        await session.execute(
            text(
                "SELECT at, from_status, to_status, reason FROM tracking_events "
                "WHERE token_mint = :mint ORDER BY at DESC LIMIT 50"
            ),
            params,
        )
    )
    return {
        "token": token_d,
        "windows": windows,
        "pools": pools,
        "security": dict(security) if security else None,
        "holders": _holder_payload(holders),
        "discovery": dict(discovery) if discovery else None,
        "tracking_events": events,
    }


def _holder_payload(row: RowMapping | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    # amount_raw can exceed JS safe integers; send as string.
    d["accounts"] = [{**a, "amount_raw": str(a.get("amount_raw"))} for a in d["accounts"] or []]
    return d


# ---------------------------------------------------------------- time series


@router.get("/tokens/{mint}/series")
async def token_series(
    session: Session, mint: str, hours: float = Query(24, gt=0, le=24 * 30)
) -> dict[str, Any]:
    """Snapshot time series. Raw 5-minute points up to 48h; hourly buckets (last value)
    beyond that to keep payloads small."""
    bucket = timedelta(minutes=5) if hours <= 48 else timedelta(hours=1)
    rows = await session.execute(
        text(
            """
            SELECT time_bucket(:bucket, s.observed_at) AS t,
                   last(s.price_usd, s.observed_at) AS price_usd,
                   last(s.market_cap, s.observed_at) AS market_cap,
                   last(s.liquidity_usd, s.observed_at) AS liquidity_usd,
                   last(s.holder_count, s.observed_at) AS holder_count,
                   last(w.buy_volume_usd, s.observed_at) AS buy_volume_1h,
                   last(w.sell_volume_usd, s.observed_at) AS sell_volume_1h,
                   last(w.num_traders, s.observed_at) AS traders_1h,
                   last(w.num_net_buyers, s.observed_at) AS net_buyers_1h
            FROM market_snapshots s
            LEFT JOIN market_window_stats w ON w.token_mint = s.token_mint
                AND w.source = s.source AND w.observed_at = s.observed_at AND w."window" = '1h'
            WHERE s.token_mint = :mint AND s.source = 'jupiter' AND s.observed_at >= :since
            GROUP BY 1 ORDER BY 1
            """
        ),
        {"mint": mint, "since": utcnow() - timedelta(hours=hours), "bucket": bucket},
    )
    return {"bucket_seconds": int(bucket.total_seconds()), "points": _rows(rows)}


@router.get("/tokens/{mint}/candles")
async def token_candles(
    session: Session, mint: str, hours: float = Query(48, gt=0, le=24 * 30)
) -> dict[str, Any]:
    """5-minute OHLCV for the token's primary pool (GeckoTerminal)."""
    pool = await session.scalar(
        text("SELECT primary_pool FROM tokens WHERE mint = :m"), {"m": mint}
    )
    if pool is None:
        return {"pool": None, "candles": []}
    rows = await session.execute(
        text(
            """
            SELECT bucket AS t, open, high, low, close, volume_usd, is_complete
            FROM candles_5m WHERE pool_address = :pool AND bucket >= :since ORDER BY bucket
            """
        ),
        {"pool": pool, "since": utcnow() - timedelta(hours=hours)},
    )
    return {"pool": pool, "candles": _rows(rows)}


@router.get("/tokens/{mint}/quotes")
async def token_quotes(
    session: Session, mint: str, limit: int = Query(100, ge=1, le=1000)
) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            """
            SELECT quoted_at, side, notional_usd, ref_price_usd, out_usd, slippage_pct,
                   price_impact_pct, route_labels, error
            FROM swap_quotes WHERE token_mint = :mint ORDER BY quoted_at DESC LIMIT :limit
            """
        ),
        {"mint": mint, "limit": limit},
    )
    return _rows(rows)


# ---------------------------------------------------------------- discovery feed


@router.get("/events")
async def tracking_feed(
    session: Session,
    hours: float = Query(24, gt=0, le=24 * 30),
    to_status: Literal["active", "dormant", "excluded"] | None = None,
    limit: int = Query(200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """Tracking-status changes (new tokens, exclusions, dormancy, reactivation)."""
    where = "e.at >= :since" + (" AND e.to_status = :to_status" if to_status else "")
    rows = await session.execute(
        text(
            f"""
            SELECT e.at, e.token_mint, t.symbol, t.name, e.from_status, e.to_status, e.reason,
                   t.classification
            FROM tracking_events e JOIN tokens t ON t.mint = e.token_mint
            WHERE {where} ORDER BY e.at DESC LIMIT :limit
            """
        ),
        {"since": utcnow() - timedelta(hours=hours), "to_status": to_status, "limit": limit},
    )
    return _rows(rows)


# ---------------------------------------------------------------- overview


@router.get("/overview")
async def overview(session: Session) -> dict[str, Any]:
    settings = get_settings()
    since_24h = utcnow() - timedelta(hours=24)
    by_class = _rows(
        await session.execute(
            text(
                "SELECT tracking_status, classification, count(*) AS n FROM tokens "
                "GROUP BY 1, 2 ORDER BY 1, 2"
            )
        )
    )
    # approximate_row_count is a cheap Timescale estimate for large hypertables.
    rows = _rows(
        await session.execute(
            text(
                """
                SELECT 'raw_observations' AS table, approximate_row_count('raw_observations') AS n
                UNION ALL SELECT 'market_snapshots', approximate_row_count('market_snapshots')
                UNION ALL SELECT 'market_window_stats', approximate_row_count('market_window_stats')
                UNION ALL SELECT 'pool_snapshots', approximate_row_count('pool_snapshots')
                UNION ALL SELECT 'candles_5m', approximate_row_count('candles_5m')
                UNION ALL SELECT 'token_security', (SELECT count(*) FROM token_security)
                UNION ALL SELECT 'holder_snapshots', (SELECT count(*) FROM holder_snapshots)
                UNION ALL SELECT 'swap_quotes', (SELECT count(*) FROM swap_quotes)
                UNION ALL SELECT 'tracking_events', (SELECT count(*) FROM tracking_events)
                """
            )
        )
    )
    requests_24h = _rows(
        await session.execute(
            text(
                "SELECT job, sum(api_credits_used) AS requests, count(*) AS runs "
                "FROM collector_runs WHERE started_at >= :since GROUP BY job ORDER BY job"
            ),
            {"since": since_24h},
        )
    )
    collecting_since = await session.scalar(
        text("SELECT min(started_at) FROM collector_runs WHERE job <> 'heartbeat'")
    )
    jobs = await job_health(session, expected_minutes(settings), since_24h)
    fresh = await freshness(session, settings)
    return {
        "collecting_since": collecting_since,
        "universe": by_class,
        "rows": rows,
        "requests_24h": requests_24h,
        "jobs": [j.as_dict() for j in jobs],
        "freshness": fresh.as_dict(),
    }
