"""Read-only API for stored backtest reports (docs/BACKTESTING.md)."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from autolp.api.explorer import Session, _rows
from autolp.backtest.params import BACKTEST_VERSION, BT, UNIVERSE_LABELS

router = APIRouter(prefix="/api/backtests", tags=["backtests"])


@router.get("")
async def backtests(session: Session, limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    """Stored runs, newest first, with the headline numbers of our selection."""
    items = _rows(
        await session.execute(
            text(
                """
                SELECT id, created_at, version, strategy_version, model_version, window_start,
                       window_end, sealed, overrides, input_digest, result_digest,
                       report->'data' AS data, report->'gates' AS gates,
                       (SELECT r->'metrics'->'all' FROM jsonb_array_elements(report->'runs') r
                        WHERE r->>'universe' = 'selection' AND r->>'scenario' = 'base') AS selection
                FROM backtest_runs ORDER BY id DESC LIMIT :limit
                """
            ),
            {"limit": limit},
        )
    )
    return {
        "version": BACKTEST_VERSION,
        "universes": UNIVERSE_LABELS,
        "scenarios": list(BT.scenarios),
        "items": items,
    }


@router.get("/{run_id}")
async def backtest(session: Session, run_id: int) -> dict[str, Any]:
    """One full report: parameters, splits, every run's metrics, trades and equity curve."""
    row = (
        await session.execute(
            text("SELECT id, created_at, report FROM backtest_runs WHERE id = :id"), {"id": run_id}
        )
    ).first()
    if row is None:
        raise HTTPException(404, "backtest run not found")
    report: dict[str, Any] = row.report
    mints = sorted({t["mint"] for r in report["runs"] for t in r["trades"]})
    symbols = {
        m: s
        for m, s in await session.execute(
            text("SELECT mint, symbol FROM tokens WHERE mint = ANY(:m)"), {"m": mints}
        )
    }
    return {"id": row.id, "created_at": row.created_at, "symbols": symbols, **report}
