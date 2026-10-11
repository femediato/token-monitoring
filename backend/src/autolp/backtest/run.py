"""Backtest CLI (bt-v1): run, print, store and optionally write the report files.

    python -m autolp.backtest.run                      # whole history, holdout sealed
    python -m autolp.backtest.run --days 90 --unseal-holdout --out reports/bt
    python -m autolp.backtest.run --set strategy.range.max_hold_hours=6 --no-store

The default window starts at the first model v1.1 evaluation (no earlier point-in-time
selection data exists) and ends now. The holdout (the last third) is not simulated unless
--unseal-holdout is given; unseal it once, after development and validation are final.
"""

import argparse
import asyncio
import json
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from autolp.backtest import report as rep
from autolp.backtest.data import first_evaluation_at, load
from autolp.backtest.params import BT, UNIVERSE_LABELS, BacktestParams
from autolp.backtest.runner import backtest
from autolp.config import get_settings
from autolp.db.session import make_engine, make_sessionmaker
from autolp.strategies.params import PARAMS, StrategyParams


def override(obj: Any, path: str, value: Any) -> Any:
    """Return a copy of a (nested, frozen) dataclass with `path` (a.b.c) set to value."""
    head, _, rest = path.partition(".")
    if isinstance(obj, dict):
        return {**obj, head: override(obj[head], rest, value) if rest else value}
    if not is_dataclass(obj) or head not in {f.name for f in fields(obj)}:
        raise SystemExit(f"unknown parameter: {head}")
    cur = getattr(obj, head)
    return replace(obj, **{head: override(cur, rest, value) if rest else value})  # type: ignore[type-var]


def parse_sets(sets: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for s in sets:
        key, _, raw = s.partition("=")
        try:
            out[key] = json.loads(raw)
        except json.JSONDecodeError:
            out[key] = raw
    return out


def apply(overrides: dict[str, Any]) -> tuple[StrategyParams, BacktestParams]:
    sp, bp = PARAMS, BT
    for key, value in overrides.items():
        root, _, rest = key.partition(".")
        if root == "strategy":
            sp = override(sp, rest, value)
        elif root == "backtest":
            bp = override(bp, rest, value)
        else:
            raise SystemExit(f"--set keys start with strategy. or backtest.: {key}")
    return sp, bp


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    overrides = parse_sets(args.set)
    sp, bp = apply(overrides)
    db = make_engine(get_settings())
    factory = make_sessionmaker(db)
    try:
        async with factory() as session:
            first = await first_evaluation_at(session)
            if first is None:
                raise SystemExit("no model evaluations stored yet")
            end = args.end or datetime.now(UTC).replace(second=0, microsecond=0)
            start = args.start or (end - timedelta(days=args.days) if args.days else first)
            start = max(start, first)
            data = await load(session, start, end, sp, bp)
        result = backtest(
            data,
            sealed=not args.unseal_holdout,
            sp=sp,
            bp=bp,
            universes=tuple(args.universes) if args.universes else None,
            overrides=overrides,
        )
        if not args.no_store:
            async with factory() as session, session.begin():
                result["id"] = await rep.store(session, result)
        return result
    finally:
        await db.dispose()


def _utc(s: str) -> datetime:
    t = datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--start", type=_utc, help="window start (UTC); default: first evaluation")
    ap.add_argument("--end", type=_utc, help="window end (UTC); default: now")
    ap.add_argument("--days", type=float, help="window length ending at --end")
    ap.add_argument("--unseal-holdout", action="store_true", help="also simulate the holdout")
    ap.add_argument("--universes", nargs="*", choices=list(UNIVERSE_LABELS))
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--out", type=Path, help="write report.md/json, trades.csv, equity.csv")
    ap.add_argument("--no-store", action="store_true", help="do not save the run in the database")
    args = ap.parse_args()
    result = asyncio.run(_run(args))
    print(rep.markdown(result))
    if "id" in result:
        print(f"\nStored as backtest run #{result['id']} (Backtests page, /api/backtests).")
    if args.out:
        rep.write_files(result, args.out)
        print(f"Files written to {args.out}/")


if __name__ == "__main__":
    main()
