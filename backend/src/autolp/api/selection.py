"""Read-only API for the selection model: watchlist, evaluations, score history."""

from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Query
from sqlalchemy import text

from autolp.api.explorer import Session, _rows
from autolp.db.base import utcnow
from autolp.selection.params import CATEGORY_WEIGHTS, ELIGIBILITY, MODEL_VERSION, WATCHLIST
from autolp.selection.scoring import COMPONENTS, REASON_CODES

router = APIRouter(prefix="/api", tags=["selection"])

LATEST_RUN = """
    SELECT max(evaluated_at) FROM score_evaluations WHERE model_version = :v
"""
LATEST_HOURLY = """
    SELECT max(evaluated_at) FROM score_evaluations WHERE model_version = :v AND hourly
"""


@router.get("/model")
async def model() -> dict[str, Any]:
    """The active scoring model: weights, components, thresholds and watchlist rules."""
    return {
        "version": MODEL_VERSION,
        "category_weights": CATEGORY_WEIGHTS,
        "components": {
            cat: [{"name": n, "weight": w} for n, w, _ in comps]
            for cat, comps in COMPONENTS.items()
        },
        "reason_codes": REASON_CODES,
        "eligibility": {k: v for k, v in vars(ELIGIBILITY).items()},
        "watchlist": {k: v for k, v in vars(WATCHLIST).items()},
    }


@router.get("/watchlist")
async def get_watchlist(session: Session) -> dict[str, Any]:
    v = {"v": MODEL_VERSION}
    latest = await session.scalar(text(LATEST_RUN), v)
    hourly = await session.scalar(text(LATEST_HOURLY), v)
    members = _rows(
        await session.execute(
            text(
                """
                SELECT m.token_mint AS mint, t.symbol, t.name, m."group", m.joined_at,
                       m.entry_score, e.total, e.activity, e.momentum, e.liquidity, e.buying,
                       e.risk, e.coverage, e.eligible, e.reasons, e.evaluated_at,
                       h.rank, h.total AS hourly_total, p.total AS prev_hourly_total,
                       (e.features->>'price_usd')::float8 AS price_usd,
                       (e.features->>'market_cap')::float8 AS market_cap,
                       (e.features->>'liquidity_usd')::float8 AS liquidity_usd,
                       (e.features->>'volume_24h')::float8 AS volume_24h
                FROM watchlist_members m
                JOIN tokens t ON t.mint = m.token_mint
                LEFT JOIN score_evaluations e ON e.token_mint = m.token_mint
                    AND e.evaluated_at = :latest AND e.model_version = :v
                LEFT JOIN score_evaluations h ON h.token_mint = m.token_mint
                    AND h.evaluated_at = :hourly AND h.model_version = :v
                LEFT JOIN LATERAL (
                    SELECT total FROM score_evaluations x
                    WHERE x.token_mint = m.token_mint AND x.model_version = :v AND x.hourly
                      AND x.evaluated_at < :hourly
                    ORDER BY x.evaluated_at DESC LIMIT 1
                ) p ON true
                ORDER BY h.rank NULLS LAST, e.total DESC NULLS LAST
                """
            ),
            {**v, "latest": latest, "hourly": hourly},
        )
    )
    return {
        "model_version": MODEL_VERSION,
        "evaluated_at": latest,
        "hourly_evaluated_at": hourly,
        "size": WATCHLIST.size,
        "entry_score": WATCHLIST.entry_score,
        "retain_score": WATCHLIST.retain_score,
        "members": members,
    }


@router.get("/watchlist/events")
async def watchlist_events(
    session: Session, hours: float = Query(24 * 7, gt=0, le=24 * 90)
) -> list[dict[str, Any]]:
    return _rows(
        await session.execute(
            text(
                """
                SELECT e.at, e.token_mint AS mint, t.symbol, e.action, e."group", e.reason,
                       e.score, e.model_version
                FROM watchlist_events e JOIN tokens t ON t.mint = e.token_mint
                WHERE e.at >= :since ORDER BY e.at DESC, e.id DESC LIMIT 500
                """
            ),
            {"since": utcnow() - timedelta(hours=hours)},
        )
    )


@router.get("/evaluations")
async def evaluations(
    session: Session,
    view: Literal["eligible", "ineligible", "all"] = "eligible",
    reason: str | None = None,
) -> dict[str, Any]:
    """Latest evaluation of every active token, with a reason-code funnel."""
    v = {"v": MODEL_VERSION}
    at = await session.scalar(text(LATEST_RUN), v)
    if at is None:
        return {"evaluated_at": None, "counts": {}, "reasons": [], "items": []}
    where = {"eligible": "e.eligible", "ineligible": "NOT e.eligible", "all": "true"}[view]
    params: dict[str, Any] = {**v, "at": at}
    if reason:
        where += " AND e.reasons ? :reason"
        params["reason"] = reason
    items = _rows(
        await session.execute(
            text(
                f"""
                SELECT e.token_mint AS mint, t.symbol, t.name, e.eligible, e.critical, e.reasons,
                       e.total, e.activity, e.momentum, e.liquidity, e.buying, e.risk,
                       e.coverage, e."group", e.rank,
                       (e.features->>'market_cap')::float8 AS market_cap,
                       (e.features->>'liquidity_usd')::float8 AS liquidity_usd,
                       (e.features->>'volume_24h')::float8 AS volume_24h,
                       m.token_mint IS NOT NULL AS member
                FROM score_evaluations e
                JOIN tokens t ON t.mint = e.token_mint
                LEFT JOIN watchlist_members m ON m.token_mint = e.token_mint
                WHERE e.evaluated_at = :at AND e.model_version = :v AND {where}
                ORDER BY e.total DESC NULLS LAST, (e.features->>'liquidity_usd')::float8 DESC NULLS LAST
                LIMIT 600
                """
            ),
            params,
        )
    )
    counts = (
        (
            await session.execute(
                text(
                    """
                    SELECT count(*) AS evaluated, count(*) FILTER (WHERE eligible) AS eligible,
                           count(*) FILTER (WHERE critical) AS critical,
                           count(*) FILTER (WHERE total >= :entry) AS above_entry
                    FROM score_evaluations WHERE evaluated_at = :at AND model_version = :v
                    """
                ),
                {**params, "entry": WATCHLIST.entry_score},
            )
        )
        .mappings()
        .one()
    )
    reasons = _rows(
        await session.execute(
            text(
                """
                SELECT r AS reason, count(*) AS n
                FROM score_evaluations, jsonb_array_elements_text(reasons) r
                WHERE evaluated_at = :at AND model_version = :v
                GROUP BY r ORDER BY n DESC
                """
            ),
            {**v, "at": at},
        )
    )
    return {"evaluated_at": at, "counts": dict(counts), "reasons": reasons, "items": items}


@router.get("/tokens/{mint}/scores")
async def token_scores(
    session: Session, mint: str, hours: float = Query(72, gt=0, le=24 * 30)
) -> dict[str, Any]:
    v = {"v": MODEL_VERSION, "mint": mint}
    history = _rows(
        await session.execute(
            text(
                """
                SELECT evaluated_at AS t, eligible, total, activity, momentum, liquidity, buying,
                       risk, coverage, rank, hourly
                FROM score_evaluations
                WHERE token_mint = :mint AND model_version = :v AND evaluated_at >= :since
                ORDER BY evaluated_at
                """
            ),
            {**v, "since": utcnow() - timedelta(hours=hours)},
        )
    )
    latest = (
        (
            await session.execute(
                text(
                    """
                    SELECT evaluated_at, eligible, critical, reasons, features, components,
                           total, activity, momentum, liquidity, buying, risk, coverage,
                           "group", rank
                    FROM score_evaluations WHERE token_mint = :mint AND model_version = :v
                    ORDER BY evaluated_at DESC LIMIT 1
                    """
                ),
                v,
            )
        )
        .mappings()
        .first()
    )
    events = _rows(
        await session.execute(
            text(
                'SELECT at, action, "group", reason, score FROM watchlist_events '
                "WHERE token_mint = :mint ORDER BY at DESC LIMIT 50"
            ),
            v,
        )
    )
    return {
        "model_version": MODEL_VERSION,
        "latest": dict(latest) if latest else None,
        "history": history,
        "watchlist_events": events,
    }


@router.get("/holders/infrastructure-candidates")
async def infrastructure_candidates(
    session: Session, min_tokens: int = Query(5, ge=2, le=100)
) -> dict[str, Any]:
    """Counted (non-excluded) owners that appear among the largest holders of many tokens:
    likely exchange or custody wallets. Verify a label before adding one to
    reference/infrastructure_wallets.json."""
    analysed = await session.scalar(
        text(
            "SELECT count(DISTINCT token_mint) FROM holder_snapshots "
            "WHERE error IS NULL AND method_version = 'v2'"
        )
    )
    rows = _rows(
        await session.execute(
            text(
                """
                WITH h AS (
                    SELECT DISTINCT ON (token_mint) token_mint, accounts
                    FROM holder_snapshots WHERE error IS NULL AND method_version = 'v2'
                    ORDER BY token_mint, observed_at DESC
                ), a AS (
                    SELECT h.token_mint, x->>'owner' AS owner,
                           (x->>'pct_of_supply')::float8 AS pct, x->>'excluded' AS excluded
                    FROM h, jsonb_array_elements(h.accounts) x
                )
                SELECT owner, count(DISTINCT token_mint) AS tokens,
                       avg(pct) AS avg_pct, max(pct) AS max_pct,
                       bool_or(excluded IS NOT NULL) AS excluded
                FROM a WHERE owner IS NOT NULL
                GROUP BY owner HAVING count(DISTINCT token_mint) >= :min_tokens
                ORDER BY tokens DESC, max_pct DESC LIMIT 50
                """
            ),
            {"min_tokens": min_tokens},
        )
    )
    return {"tokens_analysed": analysed, "candidates": rows}
