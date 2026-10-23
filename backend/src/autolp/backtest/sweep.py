"""Strategy research sweep (bt-v2): many variants side by side on the same stored data.

    python -m autolp.backtest.sweep --grid ../research/grids/round1.json [--end ...]

A grid file names variants as parameter overrides (the same `strategy.x.y` keys as
`backtest.run --set`). The data is loaded once; every variant then runs the decision
engine on its universe (default `selection`) under each execution scenario, in parallel processes.
The simulation is pure, so the same grid over the same data gives the same digests
whether it runs serially or in parallel.

Multiple-testing discipline (docs/BACKTESTING.md, section 11):

- variants are ranked on the development split only;
- validation is shown next to it, never used for the ranking;
- the holdout stays sealed;
- each variant is compared with a random-entry baseline under comparable exits;
- each grid point shows its worst neighbour (one step along one axis), so a lone spike
  stands out from a broad plateau.

Grid format:

    {"name": "round1", "description": "...",
     "scenarios": ["base", "paper", "stress"], "rank_scenario": "paper",
     "min_trades": 10, "universe": "selection",                   # default universe
     "base": {"strategy.risk.daily_loss_stop_pct": 3.0},          # applied to every variant
     "variants": [{"name": "...", "set": {...}, "baseline": "...", "universe": "liquid"}],
     "groups": [{"prefix": "...", "set": {...}, "baseline": "...", "baseline_group": "...",
                 "universe": "...", "grid": {"strategy.momentum.exit.atr_mult": [2, 3], ...}}]}

A variant trades one universe (backtest/params.py UNIVERSE_LABELS; `early` excluded):
`selection` by default, or e.g. `liquid` / `random` to test the token selection itself.

A group expands into the cartesian product of its `grid` axes. With `baseline_group`, each
point's baseline is the point of that group with the same axis labels (e.g. a random-entry
group sweeping the same exit values), so a strategy is compared with random entries under
exactly its own exits.
"""

import argparse
import asyncio
import hashlib
import itertools
import json
import multiprocessing as mp
import statistics
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autolp.backtest.data import BacktestData, first_evaluation_at, load
from autolp.backtest.metrics import summarize
from autolp.backtest.params import BACKTEST_VERSION, BT, UNIVERSE_LABELS
from autolp.backtest.run import apply
from autolp.backtest.runner import run_all, splits
from autolp.config import get_settings
from autolp.db.session import make_engine, make_sessionmaker
from autolp.strategies.params import PARAMS, STRATEGY_VERSION

SWEEP_VERSION = "sweep-v2"
# Inputs the loaded data depends on: a variant cannot change them.
FROZEN_PREFIXES = ("strategy.data.", "strategy.filters.min_breadth_tokens", "backtest.")


@dataclass
class Variant:
    name: str
    overrides: dict[str, Any]
    group: str | None = None
    baseline: str | None = None
    axes: dict[str, int] = field(default_factory=dict)  # axis -> index in the group grid
    universe: str = "selection"


def _short(key: str) -> str:
    return key.rsplit(".", 1)[-1]


def _fmt_value(v: Any) -> str:
    return "+".join(map(str, v)) if isinstance(v, list) else str(v)


def expand(grid: dict[str, Any]) -> list[Variant]:
    """The variants of a grid file, in file order; names are unique."""
    base = dict(grid.get("base", {}))
    out: list[Variant] = []
    for v in grid.get("variants", []):
        out.append(
            Variant(
                v["name"],
                {**base, **v.get("set", {})},
                None,
                v.get("baseline"),
                universe=v.get("universe", grid.get("universe", "selection")),
            )
        )
    for g in grid.get("groups", []):
        axes = list(g.get("grid", {}).items())
        for idx in itertools.product(*(range(len(vals)) for _, vals in axes)):
            point = {k: vals[i] for (k, vals), i in zip(axes, idx, strict=True)}
            label = " ".join(f"{_short(k)}={_fmt_value(v)}" for k, v in point.items())
            name = f"{g['prefix']} {label}".strip()
            twin = f"{g['baseline_group']} {label}".strip() if "baseline_group" in g else None
            out.append(
                Variant(
                    name,
                    {**base, **g.get("set", {}), **point},
                    g["prefix"],
                    twin or g.get("baseline"),
                    {k: i for (k, _), i in zip(axes, idx, strict=True)},
                    g.get("universe", grid.get("universe", "selection")),
                )
            )
    names = [v.name for v in out]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise SystemExit(f"duplicate variant names: {dupes}")
    for v in out:
        frozen = [k for k in v.overrides if k.startswith(FROZEN_PREFIXES)]
        if frozen:
            raise SystemExit(f"{v.name}: a sweep cannot change {frozen} (reload the data)")
        if v.universe not in UNIVERSE_LABELS or v.universe == "early":
            raise SystemExit(f"{v.name}: unknown universe {v.universe!r}")
        if v.baseline is not None and v.baseline not in names:
            raise SystemExit(f"{v.name}: unknown baseline {v.baseline!r}")
    return out


# The loaded data is shared with worker processes by fork (read-only).
_DATA: BacktestData | None = None


def run_variant(v: Variant, scenarios: Sequence[str]) -> dict[str, Any]:
    assert _DATA is not None
    data = _DATA
    sp, bp = apply(v.overrides)
    parts = splits(data.start, data.end, bp)[:2]  # holdout sealed
    specs = [(v.universe, s) for s in scenarios]
    results = run_all(data, specs, data.start, parts[1].end, sp, bp)
    out: dict[str, Any] = {"name": v.name, "universe": v.universe, "scenarios": {}}
    for (_, sname), res in results.items():
        eng = res.engine
        metrics = {s.name: summarize(eng.closed, res.curve, s.start, s.end) for s in parts}
        for s in parts:
            ts = [t for t in eng.closed if s.start.isoformat() <= t["entry_at"] < s.end.isoformat()]
            metrics[s.name]["median_return_pct"] = (
                statistics.median(t["return_pct"] for t in ts) if ts else None
            )
        out["scenarios"][sname] = {
            "digest": eng.digest(),
            "metrics": metrics,
            "signals": sum(1 for d in eng.decisions if d.action == "signal"),
            "rejections": _count(d.reason for d in eng.decisions if d.action == "reject"),
            "cancels": _count(d.reason for d in eng.decisions if d.action == "cancel"),
            "trades": eng.closed,
        }
    return out


def _count(xs: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in xs:
        out[str(x)] = out.get(str(x), 0) + 1
    return dict(sorted(out.items()))


def run_sweep(
    data: BacktestData, variants: list[Variant], scenarios: Sequence[str], workers: int
) -> list[dict[str, Any]]:
    global _DATA
    _DATA = data
    try:
        if workers <= 1:
            return [run_variant(v, scenarios) for v in variants]
        ctx = mp.get_context("fork")
        with ProcessPoolExecutor(workers, mp_context=ctx) as pool:
            return list(pool.map(run_variant, variants, itertools.repeat(scenarios)))
    finally:
        _DATA = None


def _metric(r: dict[str, Any], scenario: str, split: str, key: str) -> Any:
    return r["scenarios"][scenario]["metrics"][split][key]


def rank(
    variants: list[Variant],
    results: list[dict[str, Any]],
    scenario: str,
    min_trades: int,
) -> list[dict[str, Any]]:
    """One row per variant: development metrics (the ranking), validation alongside,
    the stress result, the uplift over its baseline and the worst neighbour."""
    by_name = {r["name"]: r for r in results}
    rows: list[dict[str, Any]] = []
    for v in variants:
        r = by_name[v.name]
        dev = r["scenarios"][scenario]["metrics"]["development"]
        val = r["scenarios"][scenario]["metrics"]["validation"]
        row: dict[str, Any] = {
            "name": v.name,
            "group": v.group,
            "universe": v.universe,
            "dev_trades": dev["trades"],
            "dev_return_pct": dev["net_return_pct"],
            "dev_pf": dev["profit_factor"],
            "dev_expectancy_pct": dev["expectancy_pct"],
            "dev_win_rate_pct": dev["win_rate_pct"],
            "dev_max_dd_pct": dev["max_drawdown_pct"],
            "dev_exits": dev["exits"],
            "val_trades": val["trades"],
            "val_return_pct": val["net_return_pct"],
            "val_pf": val["profit_factor"],
            "stress_dev_return_pct": (
                _metric(r, "stress", "development", "net_return_pct")
                if "stress" in r["scenarios"]
                else None
            ),
            "enough_trades": dev["trades"] >= min_trades,
            "baseline": v.baseline,
            "uplift_expectancy_pct": None,
            "worst_neighbour_return_pct": None,
        }
        if v.baseline is not None:
            b = by_name[v.baseline]["scenarios"][scenario]["metrics"]["development"]
            if dev["expectancy_pct"] is not None and b["expectancy_pct"] is not None:
                row["uplift_expectancy_pct"] = dev["expectancy_pct"] - b["expectancy_pct"]
        if v.group is not None and v.axes:
            nbrs = [
                w
                for w in variants
                if w.group == v.group
                and w is not v
                and sum(abs(w.axes[k] - v.axes[k]) for k in v.axes) == 1
            ]
            vals = [
                by_name[w.name]["scenarios"][scenario]["metrics"]["development"]["net_return_pct"]
                for w in nbrs
            ]
            vals = [x for x in vals if x is not None]
            row["worst_neighbour_return_pct"] = min(vals) if vals else None
        rows.append(row)
    rows.sort(
        key=lambda x: (
            not x["enough_trades"],
            -(x["dev_return_pct"] if x["dev_return_pct"] is not None else -1e9),
            x["name"],
        )
    )
    for i, x in enumerate(rows, 1):
        x["rank"] = i
    return rows


def _f(v: Any, spec: str = "{:+.2f}") -> str:
    return "-" if v is None else spec.format(v)


def markdown(report: dict[str, Any]) -> str:
    w = report["window"]
    lines = [
        f"# Strategy sweep {report['grid']['name']} ({SWEEP_VERSION}, {BACKTEST_VERSION}, "
        f"{STRATEGY_VERSION})",
        "",
        report["grid"].get("description", ""),
        "",
        f"Window {w['start'][:16]} -> {w['end'][:16]} UTC ({w['days']} days). "
        f"Development {w['development'][0][:16]} -> {w['development'][1][:16]}, "
        f"validation -> {w['validation'][1][:16]}; holdout sealed.",
        f"Input digest {report['input_digest'][:16]}..., sweep digest "
        f"{report['sweep_digest'][:16]}...; {len(report['ranking'])} variants, "
        f"ranked on development, scenario `{report['rank_scenario']}`.",
        "",
        "**Read with care:** "
        f"{w['days']} days of data, many variants. The top of this table is partly luck; "
        "trust a variant only if its neighbours, its validation result and the stress "
        "scenario agree, and it beats its random-entry baseline.",
        "",
        "| # | Variant | Universe | Dev trades | Dev return % | Dev PF | Dev exp. % | Dev max DD % | "
        "vs random (exp. pp) | Worst neighbour % | Stress dev % | Val trades | Val return % |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for x in report["ranking"]:
        flag = "" if x["enough_trades"] else " (few trades)"
        lines.append(
            f"| {x['rank']} | {x['name']}{flag} | {x['universe']} | {x['dev_trades']} | {_f(x['dev_return_pct'])} | "
            f"{_f(x['dev_pf'], '{:.2f}')} | {_f(x['dev_expectancy_pct'])} | "
            f"{_f(x['dev_max_dd_pct'], '{:.1f}')} | {_f(x['uplift_expectancy_pct'])} | "
            f"{_f(x['worst_neighbour_return_pct'])} | {_f(x['stress_dev_return_pct'])} | "
            f"{x['val_trades']} | {_f(x['val_return_pct'])} |"
        )
    lines += ["", "Method and limitations: docs/BACKTESTING.md, section 11."]
    return "\n".join(lines)


def build_report(
    grid: dict[str, Any], data: BacktestData, variants: list[Variant], results: list[dict[str, Any]]
) -> dict[str, Any]:
    scenario = grid.get("rank_scenario", "paper")
    parts = splits(data.start, data.end, BT)
    h = hashlib.sha256(data.input_digest.encode())
    for v, r in zip(variants, results, strict=True):
        h.update(json.dumps([v.name, v.universe, v.overrides], sort_keys=True).encode())
        for sname in sorted(r["scenarios"]):
            h.update(f"|{sname}|{r['scenarios'][sname]['digest']}\n".encode())
    return {
        "version": SWEEP_VERSION,
        "backtest_version": BACKTEST_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "grid": grid,
        "rank_scenario": scenario,
        "window": {
            "start": data.start.isoformat(),
            "end": data.end.isoformat(),
            "days": round((data.end - data.start).total_seconds() / 86400, 2),
            "development": [parts[0].start.isoformat(), parts[0].end.isoformat()],
            "validation": [parts[1].start.isoformat(), parts[1].end.isoformat()],
        },
        "input_digest": data.input_digest,
        "sweep_digest": h.hexdigest(),
        "variants": [
            {"name": v.name, "universe": v.universe, "overrides": v.overrides} for v in variants
        ],
        "ranking": rank(variants, results, scenario, int(grid.get("min_trades", 10))),
        "results": results,
    }


async def _load(start: datetime | None, end: datetime | None) -> BacktestData:
    db = make_engine(get_settings())
    try:
        async with make_sessionmaker(db)() as session:
            first = await first_evaluation_at(session)
            if first is None:
                raise SystemExit("no model evaluations stored yet")
            end = end or datetime.now(UTC).replace(second=0, microsecond=0)
            start = max(start or first, first)
            return await load(session, start, end, PARAMS, BT, research=True)
    finally:
        await db.dispose()


def _utc(s: str) -> datetime:
    t = datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--grid", type=Path, required=True)
    ap.add_argument("--start", type=_utc)
    ap.add_argument("--end", type=_utc)
    ap.add_argument("--workers", type=int, default=max(1, (mp.cpu_count() or 2) - 1))
    ap.add_argument("--out", type=Path, default=Path("../reports/sweeps"))
    args = ap.parse_args()
    grid = json.loads(args.grid.read_text())
    variants = expand(grid)
    scenarios = list(grid.get("scenarios", ["base", "paper", "stress"]))
    unknown = [s for s in scenarios if s not in BT.scenarios]
    if unknown or grid.get("rank_scenario", "paper") not in scenarios:
        raise SystemExit(f"unknown or missing scenarios: {unknown or grid.get('rank_scenario')}")
    data = asyncio.run(_load(args.start, args.end))
    print(f"{len(variants)} variants x {len(scenarios)} scenarios, {args.workers} workers ...")
    results = run_sweep(data, variants, scenarios, args.workers)
    report = build_report(grid, data, variants, results)
    md = markdown(report)
    print(md)
    stamp = data.end.strftime("%Y%m%dT%H%M")
    out = args.out / f"{grid['name']}-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True, default=str))
    (out / "report.md").write_text(md + "\n")
    print(f"\nWritten to {out}/")


if __name__ == "__main__":
    main()
