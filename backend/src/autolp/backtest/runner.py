"""Run the strat-v1 decision engine over each universe and execution scenario.

Pure given `BacktestData`: the same data and parameters always give the same report and
`result_digest`. Each run steps a 5-minute grid; at every step the engine sees, for the
universe's members (and tokens it holds), only candles that had closed and reached the
signal job (`candle_latency_minutes`) and evaluations made by then. The token's
`eligible` check becomes "member of this universe and not critical", so the same rules
and risk limits trade every universe.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from autolp.backtest.data import BacktestData, BtEvaluation
from autolp.backtest.metrics import Point, forward_summary, summarize
from autolp.backtest.params import BACKTEST_VERSION, BT, GATES, UNIVERSE_LABELS, BacktestParams
from autolp.backtest.universes import Timeline, early_rule
from autolp.selection.params import MODEL_VERSION
from autolp.strategies import rules
from autolp.strategies.bars import Bar
from autolp.strategies.engine import Engine
from autolp.strategies.params import PARAMS, STRATEGY_VERSION, StrategyParams
from autolp.strategies.replay import grid
from autolp.strategies.rules import MarketView, Signal

MIN_OOS_TRADES = 30  # below this a gate is reported as "insufficient data", not pass/fail


@dataclass(frozen=True)
class Split:
    name: str
    start: datetime
    end: datetime


def splits(start: datetime, end: datetime, bp: BacktestParams = BT) -> list[Split]:
    """Development / validation / holdout in the plan's 45/15/30 proportions."""
    s = bp.split
    total = end - start
    dev_end = start + total * (s.development_days / s.target_days)
    val_end = dev_end + total * (s.validation_days / s.target_days)
    out = [Split("development", start, dev_end), Split("validation", dev_end, val_end)]
    out.append(Split("holdout", val_end, end))
    return [replace(x, start=_floor5(x.start), end=_floor5(x.end)) for x in out]


def _floor5(t: datetime) -> datetime:
    return t.replace(second=0, microsecond=0) - timedelta(minutes=t.minute % 5)


@dataclass
class RunResult:
    universe: str
    scenario: str
    engine: Engine
    curve: list[Point] = field(default_factory=list)
    member_steps: int = 0
    fresh_steps: int = 0


def run_all(
    data: BacktestData,
    specs: list[tuple[str, str]],
    start: datetime,
    end: datetime,
    sp: StrategyParams = PARAMS,
    bp: BacktestParams = BT,
) -> dict[tuple[str, str], RunResult]:
    """Step every (universe, scenario) run in lockstep. Within a step, views and rule
    results depend only on the token and whether it is tradeable in that universe, so they
    are computed once and shared between runs (signals are never mutated)."""
    runs: dict[tuple[str, str], RunResult] = {}
    signal_cache: dict[tuple[str, bool], list[Signal]] = {}

    def cached(view: MarketView, p: StrategyParams) -> list[Signal]:
        ev = view.evaluation
        key = (view.mint, bool(ev and ev.eligible))
        if key not in signal_cache:
            signal_cache[key] = rules.evaluate(view, p)
        return signal_cache[key]

    for u, sname in specs:
        extra = {"early": early_rule(data.alerts, bp)} if u == "early" else None
        runs[(u, sname)] = RunResult(
            u, sname, Engine(sp, bp.scenarios[sname], extra, evaluate=cached)
        )
    max_age = timedelta(minutes=sp.data.max_candle_age_minutes)
    for t in grid(start, end):
        signal_cache.clear()
        base_views: dict[str, MarketView] = {}
        prices: dict[str, tuple[Bar, ...]] = {}
        for res in runs.values():
            eng = res.engine
            members = data.timelines[res.universe].at(t)
            held = {x.mint for x in eng.positions} | {x.setup.mint for x in eng.pending}
            views: dict[str, MarketView] = {}
            for m in sorted(members | held):
                if m not in base_views:
                    base_views[m] = data.history.view(m, t)
                    prices[m] = data.history.price_bars(m, t)
                views[m] = _tradeable(base_views[m], m in members)
            for m in members:
                res.member_steps += 1
                bars = views[m].bars
                res.fresh_steps += bool(bars) and t - bars[-1].end <= max_age
            eng.step(t, views, {m: prices[m] for m in views})
            equity = eng.equity()
            res.curve.append(Point(t, equity, equity - eng.cash))
    return runs


def _tradeable(v: MarketView, member: bool) -> MarketView:
    """The `eligible` check reads "member of this universe and not critical"."""
    ev = v.evaluation
    if ev is None:
        return v
    ok = member and not (isinstance(ev, BtEvaluation) and ev.critical)
    return v if ev.eligible == ok else replace(v, evaluation=replace(ev, eligible=ok))


def forward_returns(
    data: BacktestData, timeline: Timeline, hours: int, start: datetime, end: datetime
) -> tuple[list[float], list[float], int]:
    """Equal-weight forward returns of members at every whole hour in [start, end - h],
    from evaluation prices (available for every evaluated token, alive or not)."""
    rets: list[float] = []
    nets: list[float] = []
    missing = 0
    fresh = timedelta(minutes=data.history.p.data.max_evaluation_age_minutes)
    t = start.replace(minute=0, second=0, microsecond=0)
    if t < start:
        t += timedelta(hours=1)
    h = timedelta(hours=hours)
    while t + h <= end:
        for m in sorted(timeline.at(t)):
            e0 = data.history._eval_at(m, t)
            e1 = data.history._eval_at(m, t + h)
            p0 = e0.features.get("price_usd") if e0 and t - e0.at <= fresh else None
            p1 = e1.features.get("price_usd") if e1 and t + h - e1.at <= fresh else None
            if not p0 or p1 is None:
                missing += 1
                continue
            r = (p1 / p0 - 1) * 100
            rets.append(r)
            cost = e0.features.get("sell_cost_pct") if e0 else None
            if cost is not None:
                nets.append(r - 2 * cost)
        t += timedelta(hours=1)
    return rets, nets, missing


def _gate(value: float | None, ok: bool | None, enough: bool, threshold: str) -> dict[str, Any]:
    status = "insufficient_data" if not enough or ok is None else ("pass" if ok else "fail")
    return {"value": value, "threshold": threshold, "status": status}


def backtest(
    data: BacktestData,
    *,
    sealed: bool = True,
    sp: StrategyParams = PARAMS,
    bp: BacktestParams = BT,
    universes: tuple[str, ...] | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parts = splits(data.start, data.end, bp)
    sim_end = parts[1].end if sealed else parts[2].end
    shown = parts[:2] if sealed else parts
    names = [u for u in (universes or tuple(UNIVERSE_LABELS)) if u in data.timelines]

    specs = [(u, sname) for u in names for sname in bp.scenarios]
    results = run_all(data, specs, data.start, sim_end, sp, bp)
    runs: list[dict[str, Any]] = []
    for (u, sname), res in results.items():
        eng = res.engine
        open_positions = [
            {"mint": x.mint, "strategy": x.strategy, "entry_at": x.entry_at.isoformat()}
            for x in eng.positions
        ]
        runs.append(
            {
                "universe": u,
                "label": UNIVERSE_LABELS[u],
                "scenario": sname,
                "decision_digest": eng.digest(),
                "decisions": _count(d.action for d in eng.decisions),
                "rejections": _count(d.reason for d in eng.decisions if d.action == "reject"),
                "metrics": {s.name: summarize(eng.closed, res.curve, s.start, s.end) for s in shown}
                | {"all": summarize(eng.closed, res.curve, data.start, sim_end)},
                "members_avg": res.member_steps / max(1, len(res.curve)),
                "candle_coverage_pct": (
                    res.fresh_steps / res.member_steps * 100 if res.member_steps else None
                ),
                "equity": [
                    [p.at.isoformat(), round(p.equity, 2), round(p.exposure_usd, 2)]
                    for p in res.curve
                    if p.at.minute == 0 or p is res.curve[-1]
                ],
                "trades": eng.closed,
                "open_positions": open_positions,
            }
        )

    forward: dict[str, Any] = {}
    for hrs in bp.forward_hours:
        per: dict[str, Any] = {}
        for u in names:
            if u == "early":  # same members as selection
                continue
            per[u] = {}
            for s in [*shown, Split("all", data.start, sim_end)]:
                rets, nets, missing = forward_returns(data, data.timelines[u], hrs, s.start, s.end)
                per[u][s.name] = forward_summary(rets, nets, missing)
        for u in per:
            for s, row in per[u].items():
                base = per.get("selection", {}).get(s, {}).get("mean_pct")
                row["uplift_pct"] = (
                    base - row["mean_pct"]
                    if u != "selection" and base is not None and row["mean_pct"] is not None
                    else None
                )
        forward[str(hrs)] = per

    gates = _gates(results, shown, sim_end, data.start)
    days = (data.end - data.start).total_seconds() / 86400
    report = {
        "version": BACKTEST_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "model_version": MODEL_VERSION,
        "window": {
            "start": data.start.isoformat(),
            "end": data.end.isoformat(),
            "simulated_until": sim_end.isoformat(),
            "sealed": sealed,
            "days": round(days, 2),
        },
        "splits": [
            {"name": s.name, "start": s.start.isoformat(), "end": s.end.isoformat()} for s in parts
        ],
        "data": {
            **data.counts,
            "first_evaluation_at": (
                data.first_evaluation_at.isoformat() if data.first_evaluation_at else None
            ),
            "input_digest": data.input_digest,
            "days_available": round(days, 2),
            "days_target": bp.split.target_days,
            "sufficient": days >= bp.split.target_days,
        },
        "params": {
            "strategy": asdict(sp),
            "backtest": asdict(bp),
            "overrides": overrides or {},
        },
        "runs": runs,
        "forward": forward,
        "gates": gates,
    }
    h = hashlib.sha256()
    h.update(json.dumps([report["window"], report["params"]], sort_keys=True, default=str).encode())
    for r in runs:
        h.update(f"{r['universe']}|{r['scenario']}|{r['decision_digest']}\n".encode())
    h.update(data.input_digest.encode())
    report["result_digest"] = h.hexdigest()
    return report


def _count(xs: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in xs:
        out[str(x)] = out.get(str(x), 0) + 1
    return dict(sorted(out.items()))


def _gates(
    results: dict[tuple[str, str], RunResult],
    shown: list[Split],
    sim_end: datetime,
    start: datetime,
) -> list[dict[str, Any]]:
    base, stress = results.get(("selection", "base")), results.get(("selection", "stress"))
    if base is None:
        return []
    oos = [s for s in shown if s.name != "development"]
    oos_start, oos_end = oos[0].start, oos[-1].end
    m = summarize(base.engine.closed, base.curve, oos_start, oos_end)
    whole = summarize(base.engine.closed, base.curve, start, sim_end)
    enough = m["trades"] >= MIN_OOS_TRADES
    pf = m["profit_factor"]
    out = [
        {
            "gate": "Out-of-sample profit factor",
            **_gate(pf, None if pf is None else pf > GATES.min_oos_profit_factor, enough, "> 1.3"),
        },
        {
            "gate": "Maximum drawdown",
            **_gate(
                whole["max_drawdown_pct"],
                whole["max_drawdown_pct"] < GATES.max_drawdown_pct,
                enough,
                "< 15%",
            ),
        },
    ]
    if stress is not None:
        sm = summarize(stress.engine.closed, stress.curve, oos_start, oos_end)
        ret = sm["net_return_pct"]
        out.append(
            {
                "gate": "Profitable at 1.5x costs (stress, out of sample)",
                **_gate(ret, None if ret is None else ret > 0, enough, "> 0%"),
            }
        )
    for g in out:
        g["trades"] = m["trades"]
        g["min_trades"] = MIN_OOS_TRADES
    return out
