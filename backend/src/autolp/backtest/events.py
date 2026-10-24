"""Event studies (events-v1, docs/BACKTESTING.md, section 12): what happened after a
condition held, across every tracked token, before any trading rule is built.

    python -m autolp.backtest.events --grid ../research/events/round1.json [--end ...]

Population: hourly evaluations of the baseline candidates (no critical failure, market cap
and liquidity above the backtest floors, `BT.universe`), eligible or not. A condition is a
set of feature bounds; the forward return is the price of the same token's evaluation h
hours later. A missing later price is counted as missing, not as a return: dead tokens
drop out, which flatters every condition, so the missing share is reported.

Overlapping horizons make hourly observations far from independent. Each condition
therefore reports, besides n, the number of days whose mean was positive and a day-block
bootstrap interval for the mean.

Edge bar: an idea is worth a trading rule only if its mean gross forward return is at
least `edge_multiple` (2) x the round-trip cost (twice the median one-way quoted cost plus
the paper-calibrated fill shortfall, both ways), and it is positive on most days.

Grid format:

    {"name": "round1", "description": "...", "horizons": [1, 4, 24], "edge_multiple": 2,
     "conditions": [{"name": "pumped 1h > 10%", "when": {"ret_1h_pct": [10, null]}},
                    {"name": "eligible, fell 15-30% in 4h",
                     "when": {"eligible": true, "ret_4h_pct": [-30, -15]}}]}

Bounds are [low, high) on numeric features (null = unbounded); booleans match exactly.
"""

import argparse
import asyncio
import hashlib
import json
import random
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from autolp.backtest.params import BT, BacktestParams
from autolp.config import get_settings
from autolp.db.session import make_engine, make_sessionmaker
from autolp.selection.params import MODEL_VERSION

EVENTS_VERSION = "events-v1"
FEATURES = (
    "price_usd",
    "ret_1h_pct",
    "ret_4h_pct",
    "dist_from_4h_high_pct",
    "sell_cost_pct",
    "market_cap",
    "liquidity_usd",
    "buy_share_1h",
    "relvol_ratio",
)
CLIP = (-100.0, 200.0)  # forward returns are clipped: one 50x token must not decide a mean


@dataclass(frozen=True)
class Obs:
    mint: str
    hour: datetime
    eligible: bool
    f: Mapping[str, float | None] = field(default_factory=dict)


def matches(o: Obs, when: Mapping[str, Any]) -> bool:
    for key, cond in when.items():
        if key == "eligible":
            if o.eligible != bool(cond):
                return False
            continue
        v = o.f.get(key)
        if v is None:
            return False  # missing input never satisfies a condition
        lo, hi = cond
        if (lo is not None and v < lo) or (hi is not None and v >= hi):
            return False
    return True


def forward(
    obs: Sequence[Obs],
    prices: Mapping[tuple[str, datetime], float | None],
    hours: int,
    end: datetime,
) -> tuple[list[tuple[datetime, float]], int]:
    """(hour, clipped forward return %) for observations whose horizon ends by `end`, and
    the number with no later price."""
    out, missing = [], 0
    h = timedelta(hours=hours)
    for o in obs:
        if o.hour + h > end:
            continue
        p0, p1 = o.f.get("price_usd"), prices.get((o.mint, o.hour + h))
        if not p0 or p1 is None:
            missing += 1
            continue
        out.append((o.hour, min(max((p1 / p0 - 1) * 100, CLIP[0]), CLIP[1])))
    return out, missing


def day_bootstrap(xs: Sequence[tuple[datetime, float]], seed: str, n: int = 1000) -> Any:
    days: dict[str, list[float]] = defaultdict(list)
    for t, x in xs:
        days[t.date().isoformat()].append(x)
    groups = [days[d] for d in sorted(days)]
    if len(groups) < 2:
        return None
    rng = random.Random(int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big"))
    means = []
    for _ in range(n):
        v = [x for _ in groups for x in groups[rng.randrange(len(groups))]]
        means.append(sum(v) / len(v))
    means.sort()
    return [means[int(0.025 * n)], means[int(0.975 * n)]]


def summarize(
    xs: Sequence[tuple[datetime, float]],
    missing: int,
    round_trip_pct: float,
    multiple: float,
    seed: str,
) -> dict[str, Any]:
    if not xs:
        return {"n": 0, "missing": missing}
    vals = [x for _, x in xs]
    days: dict[str, list[float]] = defaultdict(list)
    for t, x in xs:
        days[t.date().isoformat()].append(x)
    day_means = [statistics.mean(v) for v in days.values()]
    mean = statistics.mean(vals)
    pos_days = sum(m > 0 for m in day_means)
    return {
        "n": len(vals),
        "missing": missing,
        "missing_pct": missing / (missing + len(vals)) * 100,
        "mean_pct": mean,
        "median_pct": statistics.median(vals),
        "positive_pct": sum(x > 0 for x in vals) / len(vals) * 100,
        "days": len(day_means),
        "positive_days": pos_days,
        "ci_pct": day_bootstrap(xs, seed),
        "net_mean_pct": mean - round_trip_pct,
        "clears_bar": mean >= multiple * round_trip_pct and pos_days > len(day_means) / 2,
    }


async def load(
    session: AsyncSession, start: datetime, end: datetime, bp: BacktestParams = BT
) -> tuple[list[Obs], dict[tuple[str, datetime], float | None], str]:
    cols = ", ".join(f"(features->>'{k}')::float" for k in FEATURES)
    rows = await session.execute(
        text(
            f"""
            SELECT token_mint, evaluated_at, eligible, {cols}
            FROM score_evaluations
            WHERE model_version = :v AND hourly AND NOT critical
              AND evaluated_at BETWEEN :lo AND :hi
              AND (features->>'market_cap')::float >= :mc
              AND (features->>'liquidity_usd')::float >= :liq
            ORDER BY token_mint, evaluated_at
            """
        ),
        {
            "v": MODEL_VERSION,
            "lo": start,
            "hi": end,
            "mc": bp.universe.candidate_min_market_cap,
            "liq": bp.universe.candidate_min_liquidity_usd,
        },
    )
    obs: list[Obs] = []
    h = hashlib.sha256()
    for mint, at, eligible, *vals in rows:
        hour = at.replace(minute=0, second=0, microsecond=0)
        obs.append(Obs(mint, hour, eligible, dict(zip(FEATURES, vals, strict=True))))
        h.update(f"{mint}|{at.isoformat()}|{eligible}|{vals}\n".encode())
    # Later prices come from every hourly evaluation (also tokens that fell below the
    # floors afterwards), so a token that shrank still has a forward return.
    rows = await session.execute(
        text(
            """
            SELECT token_mint, evaluated_at, (features->>'price_usd')::float
            FROM score_evaluations
            WHERE model_version = :v AND hourly AND evaluated_at BETWEEN :lo AND :hi
            """
        ),
        {"v": MODEL_VERSION, "lo": start, "hi": end + timedelta(hours=25)},
    )
    prices = {(m, at.replace(minute=0, second=0, microsecond=0)): p for m, at, p in rows}
    return obs, prices, h.hexdigest()


def round_trip_pct(obs: Sequence[Obs], shortfall_pct: float) -> float:
    """Twice (median quoted one-way cost of eligible tokens + fill shortfall)."""
    costs = [c for o in obs if o.eligible and (c := o.f.get("sell_cost_pct")) is not None]
    one_way = statistics.median(costs) if costs else 1.0
    return 2 * (one_way + shortfall_pct)


def study(
    grid: Mapping[str, Any],
    obs: Sequence[Obs],
    prices: Mapping[tuple[str, datetime], float | None],
    end: datetime,
    bp: BacktestParams = BT,
) -> dict[str, Any]:
    rt = round_trip_pct(obs, bp.scenarios["paper"].price_shortfall_pct)
    mult = float(grid.get("edge_multiple", 2))
    out: list[dict[str, Any]] = []
    for c in [{"name": "all candidates", "when": {}}, *grid["conditions"]]:
        sel = [o for o in obs if matches(o, c["when"])]
        row: dict[str, Any] = {"name": c["name"], "when": c["when"], "horizons": {}}
        for hrs in grid.get("horizons", [1, 4, 24]):
            xs, missing = forward(sel, prices, hrs, end)
            row["horizons"][str(hrs)] = summarize(xs, missing, rt, mult, f"{c['name']}|{hrs}")
        out.append(row)
    return {"round_trip_pct": rt, "edge_multiple": mult, "conditions": out}


def _f(v: Any, spec: str = "{:+.2f}") -> str:
    return "-" if v is None else spec.format(v)


def markdown(report: Mapping[str, Any]) -> str:
    g = report["grid"]
    lines = [
        f"# Event study {g['name']} ({EVENTS_VERSION}, model {MODEL_VERSION})",
        "",
        g.get("description", ""),
        "",
        f"Window {report['start'][:16]} -> {report['end'][:16]} UTC; {report['observations']} "
        f"hourly observations; input digest {report['input_digest'][:16]}...",
        f"Round trip {report['round_trip_pct']:.2f}%; edge bar: mean >= "
        f"{report['edge_multiple']:g} x round trip and positive on most days.",
        "",
        "| Condition | Horizon | n | Missing % | Mean % | 95% CI (days) | Median % | "
        "Positive % | Positive days | Net mean % | Bar |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in report["conditions"]:
        for hrs, s in c["horizons"].items():
            if not s["n"]:
                lines.append(f"| {c['name']} | {hrs}h | 0 | | | | | | | | |")
                continue
            ci = s["ci_pct"]
            lines.append(
                f"| {c['name']} | {hrs}h | {s['n']} | {s['missing_pct']:.1f} | {_f(s['mean_pct'])} | "
                f"{'-' if ci is None else f'{ci[0]:+.2f} .. {ci[1]:+.2f}'} | "
                f"{_f(s['median_pct'])} | {s['positive_pct']:.0f} | "
                f"{s['positive_days']}/{s['days']} | {_f(s['net_mean_pct'])} | "
                f"{'clears' if s['clears_bar'] else '-'} |"
            )
    lines += [
        "",
        "Missing later prices are mostly dead tokens and flatter every row. Hourly observations "
        "overlap, so trust the day counts and intervals more than n.",
    ]
    return "\n".join(lines)


async def _load(start: datetime | None, end: datetime | None) -> tuple[Any, ...]:
    db = make_engine(get_settings())
    try:
        async with make_sessionmaker(db)() as session:
            first = await session.scalar(
                text("SELECT min(evaluated_at) FROM score_evaluations WHERE model_version = :v"),
                {"v": MODEL_VERSION},
            )
            if first is None:
                raise SystemExit("no model evaluations stored yet")
            end = end or datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
            start = max(start or first, first)
            return (start, end, *(await load(session, start, end)))
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
    ap.add_argument("--out", type=Path, default=Path("../reports/events"))
    args = ap.parse_args()
    grid = json.loads(args.grid.read_text())
    start, end, obs, prices, digest = asyncio.run(_load(args.start, args.end))
    res = study(grid, obs, prices, end)
    report = {
        "version": EVENTS_VERSION,
        "model_version": MODEL_VERSION,
        "grid": grid,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "observations": len(obs),
        "input_digest": digest,
        **res,
    }
    md = markdown(report)
    print(md)
    out = args.out / f"{grid['name']}-{end.strftime('%Y%m%dT%H%M')}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True, default=str))
    (out / "report.md").write_text(md + "\n")
    print(f"\nWritten to {out}/")


if __name__ == "__main__":
    main()
