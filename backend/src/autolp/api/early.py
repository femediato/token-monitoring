"""Read-only API for early-opportunity detection."""

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import text

from autolp.api.explorer import Session, _rows
from autolp.db.base import utcnow
from autolp.selection.early import EARLY, EARLY_MODEL_VERSION, SIGNALS
from autolp.selection.early_report import build_report

router = APIRouter(prefix="/api/early", tags=["early"])


@router.get("/board")
async def board(session: Session, min_score: float = Query(50, ge=0, le=100)) -> dict[str, Any]:
    """Latest early-detection scores at or above `min_score`, with the signals that fired."""
    v = {"v": EARLY_MODEL_VERSION}
    at = await session.scalar(
        text("SELECT max(evaluated_at) FROM early_scores WHERE model_version = :v"), v
    )
    if at is None:
        return {"evaluated_at": None, "states": {}, "items": []}
    items = _rows(
        await session.execute(
            text(
                """
                SELECT s.token_mint AS mint, t.symbol, t.name, s.score, s.state, s.signals,
                       s.eligible,
                       (e.features->>'price_usd')::float8 AS price_usd,
                       (e.features->>'ret_1h_pct')::float8 AS ret_1h_pct,
                       (e.features->>'market_cap')::float8 AS market_cap,
                       (e.features->>'liquidity_usd')::float8 AS liquidity_usd,
                       (e.features->>'volume_1h')::float8 AS volume_1h
                FROM early_scores s
                JOIN tokens t ON t.mint = s.token_mint
                LEFT JOIN score_evaluations e ON e.token_mint = s.token_mint
                    AND e.evaluated_at = s.evaluated_at
                WHERE s.evaluated_at = :at AND s.model_version = :v AND s.score >= :min
                ORDER BY s.score DESC, s.eligible DESC, (e.features->>'liquidity_usd')::float8 DESC NULLS LAST
                LIMIT 300
                """
            ),
            {**v, "at": at, "min": min_score},
        )
    )
    states: dict[str, int] = dict(
        (
            await session.execute(
                text(
                    "SELECT state, count(*) FROM early_scores "
                    "WHERE evaluated_at = :at AND model_version = :v GROUP BY state"
                ),
                {**v, "at": at},
            )
        ).all()
    )
    return {
        "model_version": EARLY_MODEL_VERSION,
        "evaluated_at": at,
        "signals": SIGNALS,
        "alert_min_score": EARLY.alert_min_score,
        "states": states,
        "items": items,
    }


@router.get("/alerts")
async def alerts(session: Session, days: float = Query(14, gt=0, le=180)) -> list[dict[str, Any]]:
    return _rows(
        await session.execute(
            text(
                """
                SELECT a.id, a.at, a.token_mint AS mint, t.symbol, t.name, a.score, a.state,
                       a.signals, a.price_usd, a.market_cap, a.liquidity_usd,
                       a.pre_return_1h_pct, a.pre_return_4h_pct, a.entry_cost_pct,
                       a.entry_error, a.entry_quoted_at,
                       o.ret_1h_pct, o.ret_4h_pct, o.ret_12h_pct, o.ret_24h_pct,
                       o.mfe_24h_pct, o.mae_24h_pct, o.missing, o.completed_at,
                       (SELECT count(*) FROM alert_observations c
                        WHERE c.alert_id = a.id AND c.role = 'control') AS controls,
                       (SELECT avg(c.ret_24h_pct) FROM alert_observations c
                        WHERE c.alert_id = a.id AND c.role = 'control') AS control_ret_24h_pct
                FROM early_alerts a
                JOIN tokens t ON t.mint = a.token_mint
                LEFT JOIN alert_observations o ON o.alert_id = a.id AND o.role = 'alert'
                WHERE a.at >= :since
                ORDER BY a.at DESC LIMIT 500
                """
            ),
            {"since": utcnow() - timedelta(days=days)},
        )
    )


@router.get("/report")
async def report(session: Session, days: float = Query(90, gt=0, le=365)) -> dict[str, Any]:
    params = {"since": utcnow() - timedelta(days=days)}
    alert_rows = _rows(
        await session.execute(
            text(
                """
                SELECT id, pre_return_1h_pct, pre_return_4h_pct, entry_cost_pct,
                       entry_quoted_at, entry_error
                FROM early_alerts WHERE at >= :since
                """
            ),
            params,
        )
    )
    obs = _rows(
        await session.execute(
            text(
                """
                SELECT o.alert_id, o.role, o.ret_1h_pct, o.ret_4h_pct, o.ret_12h_pct,
                       o.ret_24h_pct, o.mfe_24h_pct, o.mae_24h_pct
                FROM alert_observations o JOIN early_alerts a ON a.id = o.alert_id
                WHERE a.at >= :since
                """
            ),
            params,
        )
    )
    return {"model_version": EARLY_MODEL_VERSION, "days": days, **build_report(obs, alert_rows)}
