"""Live strategy signal job: every entry check for every eligible token, stored as-is.

Reads stored candles and evaluations only (never calls a provider) and places no orders.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autolp.collector.runs import JobResult
from autolp.db.models import StrategySignal
from autolp.log import get_logger
from autolp.strategies import rules
from autolp.strategies.data import load_history
from autolp.strategies.params import PARAMS, STRATEGY_VERSION, StrategyParams

log = get_logger(__name__)


def _json(v: Any) -> Any:
    return round(v, 8) if isinstance(v, float) else v


async def run_strategy_signals(
    factory: async_sessionmaker[AsyncSession],
    run_id: int,
    at: datetime | None = None,
    p: StrategyParams = PARAMS,
) -> JobResult:
    at = (at or datetime.now(UTC)).replace(microsecond=0)
    async with factory() as session:
        history = await load_history(session, at, at, p)
    rows = []
    for mint, view in history.views(at).items():
        for sig in rules.evaluate(view, p):
            s = sig.setup
            rows.append(
                {
                    "token_mint": mint,
                    "evaluated_at": at,
                    "strategy": sig.strategy,
                    "version": STRATEGY_VERSION,
                    "setup": s is not None,
                    "checks": sig.checks,
                    "failed": sig.failed,
                    "values": {k: _json(v) for k, v in sig.values.items()},
                    "last_candle_at": view.bars[-1].start if view.bars else None,
                    "entry_ref": s.entry_ref if s else None,
                    "stop": s.stop if s else None,
                    "target": s.target if s else None,
                    "max_hold_hours": s.max_hold_hours if s else None,
                    "cost_pct": s.cost_pct if s else None,
                }
            )
    if rows:
        async with factory() as session, session.begin():
            await session.execute(insert(StrategySignal).values(rows))
    setups = [(r["strategy"], r["token_mint"]) for r in rows if r["setup"]]
    log.info("strategy_signals_done", evaluated=len(rows), setups=setups)
    return JobResult(rows_written=len(rows))
