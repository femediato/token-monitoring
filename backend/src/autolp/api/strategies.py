"""Read-only API for the trading strategies (paper decisions only)."""

from dataclasses import asdict
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import text

from autolp.api.explorer import Session, _rows
from autolp.db.base import utcnow
from autolp.strategies.params import PARAMS, STRATEGY_VERSION

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


@router.get("")
async def strategies() -> dict[str, Any]:
    """Active strategy version and every parameter."""
    return {"version": STRATEGY_VERSION, "params": asdict(PARAMS)}


@router.get("/signals")
async def signals(session: Session) -> dict[str, Any]:
    """Every entry check of the latest live evaluation, one row per token and strategy."""
    at = await session.scalar(
        text("SELECT max(evaluated_at) FROM strategy_signals WHERE version = :v"),
        {"v": STRATEGY_VERSION},
    )
    if at is None:
        return {"version": STRATEGY_VERSION, "evaluated_at": None, "items": []}
    items = _rows(
        await session.execute(
            text(
                """
                SELECT s.token_mint AS mint, t.symbol, s.strategy, s.setup, s.checks, s.failed,
                       s."values", s.last_candle_at, s.entry_ref, s.stop, s.target,
                       s.max_hold_hours, s.cost_pct
                FROM strategy_signals s JOIN tokens t ON t.mint = s.token_mint
                WHERE s.evaluated_at = :at AND s.version = :v
                ORDER BY t.symbol NULLS LAST, s.token_mint, s.strategy
                """
            ),
            {"at": at, "v": STRATEGY_VERSION},
        )
    )
    return {"version": STRATEGY_VERSION, "evaluated_at": at, "items": items}


@router.get("/setups")
async def setups(session: Session, days: float = Query(7, gt=0, le=90)) -> dict[str, Any]:
    """Live setups (every check passed) and how often each check blocked an entry."""
    since = utcnow() - timedelta(days=days)
    params = {"since": since, "v": STRATEGY_VERSION}
    items = _rows(
        await session.execute(
            text(
                """
                SELECT s.evaluated_at AS at, s.token_mint AS mint, t.symbol, s.strategy,
                       s.entry_ref, s.stop, s.target, s.max_hold_hours, s.cost_pct, s."values"
                FROM strategy_signals s JOIN tokens t ON t.mint = s.token_mint
                WHERE s.setup AND s.evaluated_at >= :since AND s.version = :v
                ORDER BY s.evaluated_at DESC LIMIT 300
                """
            ),
            params,
        )
    )
    blockers = _rows(
        await session.execute(
            text(
                """
                SELECT strategy, f AS "check", count(*) AS n
                FROM strategy_signals, jsonb_array_elements_text(failed) f
                WHERE evaluated_at >= :since AND version = :v
                GROUP BY strategy, f ORDER BY strategy, n DESC
                """
            ),
            params,
        )
    )
    totals = _rows(
        await session.execute(
            text(
                """
                SELECT strategy, count(*) AS evaluated, count(*) FILTER (WHERE setup) AS setups
                FROM strategy_signals WHERE evaluated_at >= :since AND version = :v
                GROUP BY strategy ORDER BY strategy
                """
            ),
            params,
        )
    )
    return {"days": days, "totals": totals, "blockers": blockers, "items": items}
