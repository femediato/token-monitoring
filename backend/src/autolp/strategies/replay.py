"""Deterministic decision replay over stored data (strat-v1).

Runs the paper decision engine on a 5-minute grid over [start, end] using only point-in-time
inputs, and prints a summary with a SHA-256 digest of every decision. Running it twice over
the same stored data gives the same digest. This is a decision check, not the Phase 6
backtest: no baselines, delay or failed-swap modelling, and no performance claims.

    python -m autolp.strategies.replay --hours 24 [--out decisions.jsonl]
"""

import argparse
import asyncio
import json
from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autolp.config import get_settings
from autolp.db.session import make_engine, make_sessionmaker
from autolp.strategies.bars import FIVE_MIN
from autolp.strategies.data import History, load_history
from autolp.strategies.engine import Engine
from autolp.strategies.params import PARAMS, STRATEGY_VERSION, StrategyParams


def grid(start: datetime, end: datetime) -> list[datetime]:
    t = start.replace(second=0, microsecond=0)
    t += timedelta(minutes=-t.minute % 5)
    out = []
    while t <= end:
        out.append(t)
        t += FIVE_MIN
    return out


def run(history: History, start: datetime, end: datetime, p: StrategyParams = PARAMS) -> Engine:
    engine = Engine(p)
    for t in grid(start, end):
        held = [x.mint for x in engine.positions] + [x.setup.mint for x in engine.pending]
        engine.step(t, history.views(t, held))
    return engine


async def replay(
    factory: async_sessionmaker[AsyncSession],
    start: datetime,
    end: datetime,
    p: StrategyParams = PARAMS,
) -> Engine:
    async with factory() as session:
        history = await load_history(session, start, end, p)
    return run(history, start, end, p)


def summary(engine: Engine, start: datetime, end: datetime) -> str:
    actions = Counter(d.action for d in engine.decisions)
    rejects = Counter(d.reason for d in engine.decisions if d.action == "reject")
    exits = Counter(d.reason for d in engine.decisions if d.action == "exit")
    by_strategy = Counter(d.strategy for d in engine.decisions if d.action == "signal")
    lines = [
        f"Decision replay {STRATEGY_VERSION}: {start:%Y-%m-%d %H:%M} -> {end:%Y-%m-%d %H:%M} UTC",
        f"  decisions: {dict(sorted(actions.items()))}",
        f"  setups by strategy: {dict(sorted(by_strategy.items()))}",
        f"  rejections: {dict(sorted(rejects.items(), key=str))}",
        f"  exits: {dict(sorted(exits.items(), key=str))}",
        f"  closed trades: {len(engine.closed)}, open positions: {len(engine.positions)}",
        f"  paper equity (marked): {engine.equity():,.2f} USD",
        f"  digest: {engine.digest()}",
        "  (decision check only; not a backtest - see docs/STRATEGIES.md)",
    ]
    return "\n".join(lines)


async def _replay(start: datetime, end: datetime) -> Engine:
    db = make_engine(get_settings())
    try:
        return await replay(make_sessionmaker(db), start, end)
    finally:
        await db.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=24, help="window ending at --end")
    parser.add_argument("--end", type=datetime.fromisoformat, default=None)
    parser.add_argument("--out", default=None, help="write decisions as JSON lines")
    args = parser.parse_args()
    end = args.end or datetime.now(UTC).replace(second=0, microsecond=0)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    start = end - timedelta(hours=args.hours)
    engine = asyncio.run(_replay(start, end))
    print(summary(engine, start, end))
    if args.out:
        with open(args.out, "w") as fh:
            for d in engine.decisions:
                fh.write(json.dumps(d.as_dict(), sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
