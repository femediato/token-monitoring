"""Point-in-time trading universes: our selection and the comparison baselines.

Each universe is a `Timeline` of membership sets; `Timeline.at(t)` is the latest set
decided at or before `t`, so no universe can use information from after `t`. Every
ranking input comes from score_evaluations rows made at that time, which include tokens
that later became inactive or failed (no survivorship filter).
"""

import bisect
import hashlib
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from autolp.backtest.params import BT, BacktestParams
from autolp.strategies.engine import Rule
from autolp.strategies.params import StrategyParams
from autolp.strategies.rules import MarketView, Setup, Signal, _common


@dataclass
class Timeline:
    times: list[datetime] = field(default_factory=list)
    sets: list[frozenset[str]] = field(default_factory=list)

    def add(self, t: datetime, members: Iterable[str]) -> None:
        assert not self.times or t > self.times[-1], "timeline must be built in order"
        self.times.append(t)
        self.sets.append(frozenset(members))

    def at(self, t: datetime) -> frozenset[str]:
        i = bisect.bisect_right(self.times, t)
        return self.sets[i - 1] if i else frozenset()

    def all_members(self) -> set[str]:
        return set().union(*self.sets) if self.sets else set()


@dataclass(frozen=True)
class Candidate:
    mint: str
    market_cap: float
    liquidity_usd: float


def ranked_timeline(batches: Mapping[datetime, Sequence[str]], size: int) -> Timeline:
    tl = Timeline()
    for t in sorted(batches):
        tl.add(t, batches[t][:size])
    return tl


def fixed_timeline(selection: Timeline, start: datetime) -> Timeline:
    """The selection as it stood at `start`, never rebalanced."""
    tl = Timeline()
    if selection.times:
        i = max(0, bisect.bisect_right(selection.times, start) - 1)
        tl.add(selection.times[i], selection.sets[i])
    return tl


def _distance(a: Candidate, b: Candidate) -> float:
    return abs(math.log10(a.market_cap) - math.log10(b.market_cap)) + abs(
        math.log10(a.liquidity_usd) - math.log10(b.liquidity_usd)
    )


def _pick(seed: str, t: datetime, mint: str, n: int) -> int:
    h = hashlib.sha256(f"{seed}|{t.isoformat()}|{mint}".encode()).digest()
    return int.from_bytes(h[:8], "big") % n


def random_matched(
    selected: Sequence[str],
    candidates: Sequence[Candidate],
    t: datetime,
    p: BacktestParams = BT,
) -> list[str]:
    """For each selected token (in mint order) one randomly chosen token among the
    `random_nearest` closest non-selected candidates, each used once. Deterministic: the
    choice is a hash of the seed, the time and the selected token."""
    by_mint = {c.mint: c for c in candidates}
    chosen = set(selected)
    out: list[str] = []
    for mint in selected:
        me = by_mint.get(mint)
        if me is None:
            continue
        pool = sorted(
            (c for c in candidates if c.mint not in chosen),
            key=lambda c: (_distance(me, c), c.mint),
        )[: p.universe.random_nearest]
        if not pool:
            break
        pick = pool[_pick(p.seed, t, mint, len(pool))]
        chosen.add(pick.mint)
        out.append(pick.mint)
    return out


def random_timeline(
    selection: Timeline,
    candidates: Mapping[datetime, Sequence[Candidate]],
    p: BacktestParams = BT,
) -> Timeline:
    tl = Timeline()
    for t in sorted(candidates):
        tl.add(t, random_matched(sorted(selection.at(t)), candidates[t], t, p))
    return tl


def watchlist_timeline(events: Sequence[tuple[datetime, str, str]]) -> Timeline:
    """Membership replayed from recorded watchlist events (time, mint, add|remove)."""
    tl = Timeline()
    members: set[str] = set()
    for t, mint, action in sorted(events):
        if action == "add":
            members.add(mint)
        else:
            members.discard(mint)
        if tl.times and tl.times[-1] == t:
            tl.sets[-1] = frozenset(members)
        else:
            tl.add(t, members)
    return tl


def early_rule(alerts: Mapping[str, Sequence[datetime]], p: BacktestParams = BT) -> Rule:
    """Backtest-only entry rule: an early-detection alert raised in the 5 minutes up to the
    decision, with the shared checks (fresh data, universe membership, cost)."""
    window = timedelta(minutes=5)
    ex = p.early.exit

    def rule(view: MarketView, sp: StrategyParams) -> Signal:
        sig = Signal("early", view.mint, view.at)
        _common(sig, view, sp)
        times = alerts.get(view.mint, ())
        i = bisect.bisect_right(times, view.at)
        sig.checks["early_alert"] = bool(i) and view.at - times[i - 1] < window
        if all(v is True for v in sig.checks.values()):
            last = view.bars[-1]
            sig.setup = Setup(
                "early",
                view.mint,
                view.at,
                last.start,
                last.close,
                last.close * (1 - ex.stop_loss_pct / 100),
                last.close * (1 + ex.take_profit_pct / 100),
                ex.max_hold_hours,
                sig.values["cost_pct"],
            )
        return sig

    return rule
