"""Load point-in-time backtest inputs from the database.

Universes are built from score_evaluations rows (rankings at the time, including tokens
that later died), the recorded watchlist events and early alerts. Evaluations and candles
are then loaded for every token that was a member of any universe. `input_digest` hashes
everything loaded, so two runs over the same stored data can be shown to share inputs.
"""

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from autolp.backtest.params import BT, BacktestParams
from autolp.backtest.universes import (
    Candidate,
    Timeline,
    fixed_timeline,
    random_timeline,
    ranked_timeline,
    watchlist_timeline,
)
from autolp.selection.params import MODEL_VERSION
from autolp.selection.scoring import score
from autolp.strategies.bars import FIVE_MIN, Bar
from autolp.strategies.data import History
from autolp.strategies.params import PARAMS, StrategyParams
from autolp.strategies.rules import Evaluation


@dataclass(frozen=True)
class BtEvaluation(Evaluation):
    critical: bool = False


@dataclass
class BacktestData:
    start: datetime
    end: datetime
    history: History
    timelines: dict[str, Timeline]
    alerts: dict[str, list[datetime]]
    first_evaluation_at: datetime | None
    input_digest: str = ""
    counts: dict[str, int] = field(default_factory=dict)


def _f(v: Any) -> Any:
    return round(v, 10) if isinstance(v, float) else v


class _Digest:
    def __init__(self) -> None:
        self.h = hashlib.sha256()

    def add(self, *parts: Any) -> None:
        self.h.update(json.dumps([_f(p) for p in parts], sort_keys=True, default=str).encode())
        self.h.update(b"\n")

    def hex(self) -> str:
        return self.h.hexdigest()


async def first_evaluation_at(session: AsyncSession) -> datetime | None:
    at: datetime | None = await session.scalar(
        text("SELECT min(evaluated_at) FROM score_evaluations WHERE model_version = :v"),
        {"v": MODEL_VERSION},
    )
    return at


async def load(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    sp: StrategyParams = PARAMS,
    bp: BacktestParams = BT,
    *,
    research: bool = False,
) -> BacktestData:
    """With `research`, also the `liquid` universe (research sweeps only, so stored
    backtests keep their inputs and digests)."""
    v, n = MODEL_VERSION, bp.universe.size
    lo = start - timedelta(hours=1)  # so every universe has a set at `start`

    # Our selection: top N eligible by total score at every evaluation (as the live job).
    sel: dict[datetime, list[str]] = defaultdict(list)
    rows = await session.execute(
        text(
            """
            SELECT evaluated_at, token_mint FROM (
                SELECT evaluated_at, token_mint, row_number() OVER (
                    PARTITION BY evaluated_at ORDER BY total DESC NULLS LAST, token_mint
                ) AS rn
                FROM score_evaluations
                WHERE model_version = :v AND eligible AND evaluated_at BETWEEN :lo AND :hi
            ) r WHERE rn <= :n ORDER BY evaluated_at, rn
            """
        ),
        {"v": v, "lo": lo, "hi": end, "n": n},
    )
    for at, mint in rows:
        sel[at].append(mint)

    # Baseline candidates at hourly evaluations: not critical, above the size floors.
    cands: dict[datetime, list[Candidate]] = defaultdict(list)
    volume: dict[datetime, list[tuple[float, str]]] = defaultdict(list)
    rows = await session.execute(
        text(
            """
            SELECT evaluated_at, token_mint,
                   (features->>'market_cap')::float AS mc,
                   (features->>'liquidity_usd')::float AS liq,
                   (features->>'volume_24h')::float AS vol
            FROM score_evaluations
            WHERE model_version = :v AND hourly AND NOT critical
              AND evaluated_at BETWEEN :lo AND :hi
              AND (features->>'market_cap')::float >= :mc
              AND (features->>'liquidity_usd')::float >= :liq
            ORDER BY evaluated_at, token_mint
            """
        ),
        {
            "v": v,
            "lo": lo,
            "hi": end,
            "mc": bp.universe.candidate_min_market_cap,
            "liq": bp.universe.candidate_min_liquidity_usd,
        },
    )
    for at, mint, mc, liq, vol in rows:
        cands[at].append(Candidate(mint, mc, liq))
        if vol is not None:
            volume[at].append((vol, mint))

    def top(key: dict[datetime, list[tuple[float, str]]]) -> dict[datetime, list[str]]:
        return {t: [m for _, m in sorted(xs, key=lambda x: (-x[0], x[1]))] for t, xs in key.items()}

    mcap = top({t: [(c.market_cap, c.mint) for c in cs] for t, cs in cands.items()})
    selection = ranked_timeline(sel, n)
    timelines = {
        "selection": selection,
        "fixed": fixed_timeline(selection, start),
        "mcap": ranked_timeline(mcap, n),
        "volume": ranked_timeline(top(volume), n),
        "random": random_timeline(selection, cands, bp),
        "early": selection,
    }
    if research:
        # Every baseline candidate (eligible or not) with 5-minute candles in the window:
        # does the eligibility filter help, or would any liquid token do as well?
        with_candles = set(
            await session.scalars(
                text(
                    "SELECT DISTINCT token_mint FROM candles_5m "
                    "WHERE is_complete AND bucket BETWEEN :lo AND :hi"
                ),
                {"lo": lo, "hi": end},
            )
        )
        liquid = {
            t: sorted(c.mint for c in cs if c.mint in with_candles) for t, cs in cands.items()
        }
        timelines["liquid"] = ranked_timeline(liquid, 10**6)
    events = await session.execute(
        text(
            """
            SELECT at, token_mint, action FROM watchlist_events
            WHERE model_version = :v AND at <= :hi ORDER BY at, id
            """
        ),
        {"v": v, "hi": end},
    )
    timelines["watchlist"] = watchlist_timeline([(a, m, act) for a, m, act in events])

    alerts: dict[str, list[datetime]] = defaultdict(list)
    rows = await session.execute(
        text(
            "SELECT token_mint, at FROM early_alerts WHERE at BETWEEN :lo AND :hi "
            "ORDER BY token_mint, at"
        ),
        {"lo": start, "hi": end},
    )
    for mint, at in rows:
        alerts[mint].append(at)

    mints = sorted(set().union(*(tl.all_members() for tl in timelines.values())))
    history = History(sp, timedelta(minutes=bp.candle_latency_minutes))
    evals: dict[str, list[Evaluation]] = defaultdict(list)
    rows = await session.execute(
        text(
            """
            SELECT token_mint, evaluated_at, eligible, critical, buying, features
            FROM score_evaluations
            WHERE model_version = :v AND token_mint = ANY(:m)
              AND evaluated_at BETWEEN :lo AND :hi
            ORDER BY token_mint, evaluated_at
            """
        ),
        {"v": v, "m": mints, "lo": start - timedelta(hours=2), "hi": end},
    )
    d = _Digest()
    n_evals = 0
    for mint, at, eligible, critical, buying, features in rows:
        if buying is None:  # ineligible rows carry no scores; baselines need the category
            buying = score(features).categories["buying"]
        evals[mint].append(BtEvaluation(at, eligible, buying, features, critical))
        d.add("e", mint, at, eligible, critical, buying, features)
        n_evals += 1
    history.evals = dict(evals)

    bars: dict[str, list[Bar]] = defaultdict(list)
    rows = await session.execute(
        text(
            """
            SELECT c.token_mint, c.bucket, c.open, c.high, c.low, c.close, c.volume_usd
            FROM candles_5m c JOIN tokens t ON t.primary_pool = c.pool_address
            WHERE c.token_mint = ANY(:m) AND c.is_complete
              AND c.bucket >= :lo AND c.bucket + interval '5 minutes' <= :hi
            ORDER BY c.token_mint, c.bucket
            """
        ),
        {"m": mints, "lo": start - FIVE_MIN * (sp.data.candles_needed + 1), "hi": end},
    )
    n_bars = 0
    for mint, bucket, o, h, low, c, vol in rows:
        bars[mint].append(Bar(bucket, o, h, low, c, vol))
        d.add("b", mint, bucket, o, h, low, c, vol)
        n_bars += 1
    history.bars = dict(bars)
    for name in sorted(timelines):
        tl = timelines[name]
        d.add("u", name, [(t, sorted(s)) for t, s in zip(tl.times, tl.sets, strict=True)])
    d.add("a", {m: alerts[m] for m in sorted(alerts)})

    return BacktestData(
        start,
        end,
        history,
        timelines,
        dict(alerts),
        await first_evaluation_at(session),
        d.hex(),
        {"tokens": len(mints), "evaluations": n_evals, "candles": n_bars},
    )
