"""Signal accuracy (analytics-v1): what each paper signal would have done, ideally.

Every strategy signal the paper engine recorded, taken or rejected by the risk rules, is
followed on the completed 5-minute candles that came after it, with the backtest's exit
rules (`strategies.risk.check_exit`: stop first when a candle touches both levels, gaps
fill at the open, time exit at the holding limit). The entry is the open of the first
candle starting at or after the decision. The net result deducts the quoted one-way cost
on both sides; there is no stop slippage, failed swap or fee, so this is the signal's
quality before execution. Comparing it with the paper result of the same signal shows
what execution cost.

This is evaluated after the fact on purpose (it is a report, not a decision), and never
from data the signal could not have had: only candles after the decision are used, and a
signal whose candles are missing is reported as `no_data`, never as a win or a loss.
"""

import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from autolp.strategies.bars import Bar
from autolp.strategies.risk import Position, check_exit, hold_deadline

ANALYTICS_VERSION = "analytics-v1"
# Candles arrive about 10 minutes late; after this long without the candles needed to
# resolve a signal it is reported as `no_data` instead of still open.
DATA_GRACE = timedelta(hours=2)


@dataclass(frozen=True)
class Outcome:
    status: str  # waiting | open | target | stop | time | no_data
    exit_reason: str | None  # check_exit reason: target, target_gap, stop, stop_gap, time
    entry_at: datetime | None
    entry_price: float | None
    exit_at: datetime | None
    exit_price: float | None  # or the last close while open
    gross_pct: float | None
    net_pct: float | None  # after the quoted one-way cost on entry and exit
    best_pct: float | None  # highest high vs. entry, up to the exit
    worst_pct: float | None  # lowest low vs. entry, up to the exit

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("entry_at", "exit_at"):
            d[k] = d[k].isoformat() if d[k] else None
        return d


def net_return_pct(entry: float, exit_: float, cost_pct: float) -> float:
    c = cost_pct / 100
    return (exit_ * (1 - c) / (entry * (1 + c)) - 1) * 100


def signal_outcome(
    decided_at: datetime,
    stop: float,
    target: float,
    max_hold_hours: float,
    cost_pct: float,
    bars: Sequence[Bar],
    now: datetime,
) -> Outcome:
    """Follow one signal on the completed candles `bars` (any order, any range)."""
    after = sorted(
        (b for b in bars if b.start >= decided_at and b.end <= now), key=lambda b: b.start
    )
    if not after:
        return _empty("waiting" if now < decided_at + DATA_GRACE else "no_data")
    entry = after[0]
    exit_by = hold_deadline(entry.start, max_hold_hours)
    pos = Position(
        id=0, strategy="", mint="", signal_at=decided_at, entry_at=entry.start,
        entry_price=entry.open, qty=1.0, size_usd=entry.open, stop=stop, target=target,
        exit_by=exit_by, cost_pct=cost_pct, planned_risk_usd=0.0, checked_until=entry.start,
    )  # fmt: skip
    hi, lo = entry.open, entry.open
    last = entry
    for bar in after:
        if bar.start >= exit_by:
            break
        hi, lo, last = max(hi, bar.high), min(lo, bar.low), bar
        ex = check_exit(pos, bar)
        if ex is not None:
            status = ex.reason.removesuffix("_gap")
            return _outcome(status, ex.reason, entry, ex.at, ex.price, cost_pct, hi, lo)
    if now < exit_by + DATA_GRACE and last.end < exit_by:
        return _outcome("open", None, entry, last.end, last.close, cost_pct, hi, lo)
    # The holding limit passed without the candle that would have closed the trade.
    return _empty("no_data")


def _empty(status: str) -> Outcome:
    return Outcome(status, None, None, None, None, None, None, None, None, None)


def _outcome(
    status: str, reason: str | None, entry: Bar, at: datetime, price: float, cost_pct: float,
    hi: float, lo: float,
) -> Outcome:  # fmt: skip
    return Outcome(
        status,
        reason,
        entry.start,
        entry.open,
        at,
        price,
        (price / entry.open - 1) * 100,
        net_return_pct(entry.open, price, cost_pct),
        (hi / entry.open - 1) * 100,
        (lo / entry.open - 1) * 100,
    )


RESOLVED = ("target", "stop", "time")


def _stats(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    done = [r for r in rows if r["outcome"]["status"] in RESOLVED]
    nets = [r["outcome"]["net_pct"] for r in done]
    paper = [r for r in done if r.get("paper_return_pct") is not None]
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[r["outcome"]["status"]] += 1
    return {
        "signals": len(rows),
        "resolved": len(done),
        "status": dict(sorted(counts.items())),
        "target_hit_pct": (
            sum(1 for r in done if r["outcome"]["status"] == "target") / len(done) * 100
            if done
            else None
        ),
        "positive_pct": sum(1 for x in nets if x > 0) / len(nets) * 100 if nets else None,
        "mean_net_pct": statistics.fmean(nets) if nets else None,
        "median_net_pct": statistics.median(nets) if nets else None,
        # Signals that were also traded: the paper result next to the ideal one.
        "paper_n": len(paper),
        "paper_mean_return_pct": (
            statistics.fmean(r["paper_return_pct"] for r in paper) if paper else None
        ),
        "ideal_mean_net_pct": (
            statistics.fmean(r["outcome"]["net_pct"] for r in paper) if paper else None
        ),
    }


def accuracy(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Accuracy per strategy, split by the risk decision (taken / rejected), and overall."""
    rows = list(rows)
    groups: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        for s in (r["strategy"], "all"):
            groups[s]["all"].append(r)
            groups[s][r["decision"]].append(r)
    return {
        s: {d: _stats(groups[s][d]) for d in ("all", "taken", "rejected")}
        for s in sorted(groups, key=lambda k: (k == "all", k))
    }
