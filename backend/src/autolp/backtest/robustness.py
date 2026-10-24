"""Robustness checks for research sweeps (sweep-v3, docs/BACKTESTING.md, section 11).

Pure functions of closed trades (the engine's `closed` dicts, with `entry_at` ISO strings,
`return_pct` and `pnl_usd`):

- a day-block bootstrap confidence interval for the expectancy, since trades on the same
  day share one market and are not independent;
- the market regime at entry, from breadth (share of eligible tokens up over 4h);
- walk-forward folds: rank on a fitting window, then measure what the chosen variant did on
  the following, unseen window; rolled forward over the unsealed data;
- the bar a variant must clear before it can be considered for a paper account.
"""

import hashlib
import random
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

Trade = Mapping[str, Any]

REGIME_UP_PCT = 55.0  # breadth above this: rising market
REGIME_DOWN_PCT = 45.0  # below this: falling market


def regime(breadth: float | None) -> str:
    if breadth is None:
        return "unknown"
    if breadth > REGIME_UP_PCT:
        return "up"
    return "down" if breadth < REGIME_DOWN_PCT else "flat"


def _in(t: Trade, start: datetime, end: datetime) -> bool:
    return bool(start.isoformat() <= t["entry_at"] < end.isoformat())


def bootstrap_ci(
    trades: Sequence[Trade],
    start: datetime,
    end: datetime,
    *,
    n: int = 2000,
    seed: str = "bootstrap",
    level: float = 0.95,
) -> tuple[float, float] | None:
    """Confidence interval of the mean return per trade (%), resampling whole UTC days of
    trades with replacement. None with fewer than 2 days of trades. Deterministic."""
    days: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        if _in(t, start, end):
            days[t["entry_at"][:10]].append(float(t["return_pct"]))
    groups = [days[d] for d in sorted(days)]
    if len(groups) < 2:
        return None
    rng = random.Random(int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big"))
    means = []
    for _ in range(n):
        xs = [x for _ in groups for x in groups[rng.randrange(len(groups))]]
        means.append(sum(xs) / len(xs))
    means.sort()
    tail = (1 - level) / 2
    return means[int(tail * n)], means[min(n - 1, int((1 - tail) * n))]


def by_regime(trades: Sequence[Trade], start: datetime, end: datetime) -> dict[str, Any]:
    """Trades and mean return per regime at entry (trades carry a `regime` key)."""
    out: dict[str, Any] = {}
    for r in ("up", "flat", "down", "unknown"):
        xs = [float(t["return_pct"]) for t in trades if _in(t, start, end) and t["regime"] == r]
        out[r] = {"trades": len(xs), "expectancy_pct": statistics.mean(xs) if xs else None}
    return out


@dataclass(frozen=True)
class Fold:
    fit_start: datetime
    fit_end: datetime  # = test start
    test_end: datetime


def folds(start: datetime, end: datetime, fit_days: float, test_days: float) -> list[Fold]:
    """Rolling folds: fit on `fit_days`, test on the next `test_days`, step `test_days`;
    only folds whose test window ends by `end`."""
    fit, test = timedelta(days=fit_days), timedelta(days=test_days)
    out, s = [], start
    while s + fit + test <= end:
        out.append(Fold(s, s + fit, s + fit + test))
        s += test
    return out


def pnl_pct(trades: Sequence[Trade], start: datetime, end: datetime, equity: float) -> float:
    """Sum of trade P/L entered in the window, as % of the starting equity."""
    return sum(float(t["pnl_usd"]) for t in trades if _in(t, start, end)) / equity * 100


def walk_forward(
    trades: Mapping[str, Sequence[Trade]],
    fold_list: Sequence[Fold],
    *,
    equity: float,
    min_trades: int,
) -> dict[str, Any]:
    """For each fold, the variant with the best fitting-window P/L (at least `min_trades`
    trades there) and its P/L on the test window. Two procedures:
    `best`: always trade the best variant; `best_if_positive`: trade it only if its fitting
    P/L was positive, otherwise hold cash. Their summed test P/L is what picking from the
    sweep would actually have earned out of sample."""
    rows: list[dict[str, Any]] = []
    for f in fold_list:
        fit = {
            name: pnl_pct(ts, f.fit_start, f.fit_end, equity)
            for name, ts in sorted(trades.items())
            if sum(_in(t, f.fit_start, f.fit_end) for t in ts) >= min_trades
        }
        if not fit:
            rows.append({"fit_start": f.fit_start.isoformat(), "chosen": None})
            continue
        best = max(fit, key=lambda k: (fit[k], k))
        test = pnl_pct(trades[best], f.fit_end, f.test_end, equity)
        rows.append(
            {
                "fit_start": f.fit_start.isoformat(),
                "test_start": f.fit_end.isoformat(),
                "test_end": f.test_end.isoformat(),
                "chosen": best,
                "fit_pnl_pct": fit[best],
                "test_pnl_pct": test,
                "test_trades": sum(_in(t, f.fit_end, f.test_end) for t in trades[best]),
                "traded": fit[best] > 0,
            }
        )
    done = [r for r in rows if r["chosen"] is not None]
    return {
        "folds": rows,
        "best_test_pnl_pct": sum(r["test_pnl_pct"] for r in done),
        "best_if_positive_test_pnl_pct": sum(r["test_pnl_pct"] for r in done if r["traded"]),
    }


def fold_consistency(
    trades: Sequence[Trade], fold_list: Sequence[Fold], equity: float
) -> float | None:
    """Share (%) of test windows with positive P/L (windows without trades excluded)."""
    vals = [
        pnl_pct(trades, f.fit_end, f.test_end, equity)
        for f in fold_list
        if any(_in(t, f.fit_end, f.test_end) for t in trades)
    ]
    return sum(v > 0 for v in vals) / len(vals) * 100 if vals else None


@dataclass(frozen=True)
class Bar:
    """What a variant must show before it is proposed for a paper account."""

    min_oos_trades: int = 100
    stress_positive: bool = True


BAR = Bar()


def verdict(row: Mapping[str, Any], bar: Bar = BAR) -> list[str]:
    """The reasons a ranked sweep row fails the bar; empty = passes."""
    fails = []

    def pos(key: str) -> bool:
        v = row.get(key)
        return v is not None and v > 0

    if not pos("dev_return_pct"):
        fails.append("development return <= 0")
    if (row.get("val_trades") or 0) < bar.min_oos_trades:
        fails.append(f"fewer than {bar.min_oos_trades} out-of-sample trades")
    if not pos("val_return_pct"):
        fails.append("validation return <= 0")
    ci = row.get("val_ci_pct")
    if ci is None or ci[0] <= 0:
        fails.append("validation 95% CI includes 0")
    if row.get("baseline") is not None and not pos("uplift_expectancy_pct"):
        fails.append("not better than its random baseline")
    if row.get("group") is not None and not pos("worst_neighbour_return_pct"):
        fails.append("a neighbour loses")
    if bar.stress_positive and not pos("stress_dev_return_pct"):
        fails.append("loses at 1.5x costs")
    return fails
