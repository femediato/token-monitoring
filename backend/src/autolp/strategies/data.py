"""Point-in-time inputs for the strategies.

`load_history` reads completed candles and selection-model evaluations for a period once;
`History.view(mint, at)` then exposes only what was known at `at`: candles that had
closed by `at` and evaluations made at or before `at`. The live signal job and the replay
use the same code, so a replay of a period reproduces the live decisions.
"""

import bisect
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from autolp.selection.params import MODEL_VERSION
from autolp.strategies.bars import FIVE_MIN, Bar
from autolp.strategies.params import PARAMS, StrategyParams
from autolp.strategies.rules import Evaluation, MarketView


@dataclass
class History:
    p: StrategyParams = PARAMS
    # Candles reach the signal job this long after they close (0 = as soon as closed). The
    # backtest models the live delay; fills and exits use `price_bars`, which has none.
    candle_latency: timedelta = timedelta(0)
    bars: dict[str, list[Bar]] = field(default_factory=dict)
    evals: dict[str, list[Evaluation]] = field(default_factory=dict)
    _keys: dict[tuple[str, str], list[datetime]] = field(default_factory=dict, repr=False)
    _breadth: dict[datetime, float | None] = field(default_factory=dict, repr=False)

    def _times(self, kind: str, mint: str) -> list[datetime]:
        key = (kind, mint)
        if key not in self._keys:
            src = self.bars.get(mint, []) if kind == "bars" else self.evals.get(mint, [])
            self._keys[key] = [x.start if isinstance(x, Bar) else x.at for x in src]
        return self._keys[key]

    def _bars_at(self, mint: str, at: datetime, n: int | None = None) -> tuple[Bar, ...]:
        bars = self.bars.get(mint, [])
        hi = bisect.bisect_right(self._times("bars", mint), at - FIVE_MIN)  # closed by `at`
        return tuple(bars[max(0, hi - (n or self.p.data.candles_needed)) : hi])

    def price_bars(self, mint: str, at: datetime, n: int = 48) -> tuple[Bar, ...]:
        """The last `n` candles closed by `at`, without candle latency (for executions)."""
        return self._bars_at(mint, at, n)

    def _eval_at(self, mint: str, at: datetime) -> Evaluation | None:
        evs = self.evals.get(mint, [])
        i = bisect.bisect_right(self._times("evals", mint), at)
        return evs[i - 1] if i else None

    def view(self, mint: str, at: datetime) -> MarketView:
        ev = self._eval_at(mint, at)
        prev = None
        if ev is not None:
            lo, hi = self.p.data.prev_evaluation_minutes
            prev = self._eval_at(mint, ev.at - timedelta(minutes=lo))
            if prev is not None and prev.at < ev.at - timedelta(minutes=hi):
                prev = None
        bars = self._bars_at(mint, at - self.candle_latency)
        return MarketView(mint, at, bars, ev, prev, self.breadth(at))

    def breadth(self, at: datetime) -> float | None:
        """Share (%) of tokens eligible in their latest fresh evaluation at `at` whose 4h
        return in that evaluation is positive; None with fewer than min_breadth_tokens."""
        if at not in self._breadth:
            fresh = timedelta(minutes=self.p.data.max_evaluation_age_minutes)
            rets = []
            for mint in self.evals:
                ev = self._eval_at(mint, at)
                if ev is None or not ev.eligible or at - ev.at > fresh:
                    continue
                r = ev.features.get("ret_4h_pct")
                if r is not None:
                    rets.append(r)
            ok = len(rets) >= self.p.filters.min_breadth_tokens
            self._breadth[at] = sum(r > 0 for r in rets) / len(rets) * 100 if ok else None
        return self._breadth[at]

    def universe(self, at: datetime) -> list[str]:
        """Tokens eligible in their latest fresh evaluation at `at`."""
        fresh = timedelta(minutes=self.p.data.max_evaluation_age_minutes)
        out = []
        for mint in sorted(self.evals):
            ev = self._eval_at(mint, at)
            if ev is not None and ev.eligible and at - ev.at <= fresh:
                out.append(mint)
        return out

    def views(self, at: datetime, extra: Iterable[str] = ()) -> dict[str, MarketView]:
        mints = set(self.universe(at)) | set(extra)
        return {m: self.view(m, at) for m in sorted(mints)}


async def load_history(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    p: StrategyParams = PARAMS,
    mints: Iterable[str] = (),
) -> History:
    """Inputs for every token that was eligible at some evaluation in [start - 2h, end],
    plus `mints` (e.g. tokens with open positions)."""
    lookback = start - timedelta(hours=2)
    params = {"lo": lookback, "hi": end, "v": MODEL_VERSION, "extra": list(mints)}
    rows = await session.execute(
        text(
            """
            WITH u AS (
                SELECT DISTINCT token_mint FROM score_evaluations
                WHERE evaluated_at BETWEEN :lo AND :hi AND model_version = :v AND eligible
                UNION SELECT unnest(CAST(:extra AS text[]))
            )
            SELECT e.token_mint, e.evaluated_at, e.eligible, e.buying, e.features
            FROM score_evaluations e JOIN u USING (token_mint)
            WHERE e.evaluated_at BETWEEN :lo AND :hi AND e.model_version = :v
            ORDER BY e.token_mint, e.evaluated_at
            """
        ),
        params,
    )
    h = History(p)
    evals: dict[str, list[Evaluation]] = defaultdict(list)
    for r in rows.mappings():
        evals[r["token_mint"]].append(
            Evaluation(r["evaluated_at"], r["eligible"], r["buying"], r["features"])
        )
    h.evals = dict(evals)
    if not h.evals:
        return h
    bar_lo = start - FIVE_MIN * (p.data.candles_needed + 1)
    rows = await session.execute(
        text(
            """
            SELECT c.token_mint, c.bucket, c.open, c.high, c.low, c.close, c.volume_usd
            FROM candles_5m c JOIN tokens t ON t.primary_pool = c.pool_address
            WHERE c.token_mint = ANY(:mints) AND c.is_complete
              AND c.bucket >= :lo AND c.bucket + interval '5 minutes' <= :hi
            ORDER BY c.token_mint, c.bucket
            """
        ),
        {"mints": sorted(h.evals), "lo": bar_lo, "hi": end},
    )
    bars: dict[str, list[Bar]] = defaultdict(list)
    for r in rows.mappings():
        bars[r["token_mint"]].append(
            Bar(r["bucket"], r["open"], r["high"], r["low"], r["close"], r["volume_usd"])
        )
    h.bars = dict(bars)
    return h
