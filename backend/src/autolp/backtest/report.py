"""Render and store backtest reports: Markdown summary, CSV/JSON files and the database."""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from autolp.db.models import BacktestRun

METRICS = (
    ("trades", "Trades", "{:.0f}"),
    ("net_return_pct", "Net return %", "{:+.2f}"),
    ("win_rate_pct", "Win rate %", "{:.0f}"),
    ("profit_factor", "Profit factor", "{:.2f}"),
    ("expectancy_usd", "Expectancy $", "{:+.2f}"),
    ("max_drawdown_pct", "Max DD %", "{:.2f}"),
    ("sharpe", "Sharpe", "{:.2f}"),
    ("sortino", "Sortino", "{:.2f}"),
    ("avg_cost_pct", "Avg cost %", "{:.2f}"),
    ("exposure_pct", "Exposure %", "{:.1f}"),
)


def fmt(v: Any, f: str) -> str:
    return "-" if v is None else f.format(v)


def markdown(r: dict[str, Any]) -> str:
    w, d = r["window"], r["data"]
    lines = [
        f"# Backtest {r['version']} ({r['strategy_version']}, model {r['model_version']})",
        "",
        f"Window {w['start'][:16]} -> {w['end'][:16]} UTC ({w['days']} days); "
        f"simulated until {w['simulated_until'][:16]}"
        + (" - holdout sealed" if w["sealed"] else " - holdout UNSEALED"),
        f"Inputs: {d['tokens']} tokens, {d['evaluations']} evaluations, {d['candles']} candles; "
        f"input digest {d['input_digest'][:16]}..., result digest {r['result_digest'][:16]}...",
    ]
    if not d["sufficient"]:
        lines += [
            "",
            f"**Insufficient data:** {d['days_available']} of the {d['days_target']:.0f} days the "
            "plan requires. Results below are a pipeline check, not evidence of an edge.",
        ]
    if r["params"]["overrides"]:
        lines += ["", f"Overrides: {json.dumps(r['params']['overrides'])}"]
    splits = (
        [s["name"] for s in r["splits"] if s["name"] in r["runs"][0]["metrics"]]
        if r["runs"]
        else []
    )
    for split in [*splits, "all"]:
        lines += ["", f"## {split}", ""]
        lines.append(
            "| Universe | Scenario | " + " | ".join(h for _, h, _ in METRICS) + " | Candles % |"
        )
        lines.append("|---" * (len(METRICS) + 3) + "|")
        for run in r["runs"]:
            m = run["metrics"][split]
            cells = [fmt(m[k], f) for k, _, f in METRICS]
            cov = fmt(run["candle_coverage_pct"], "{:.0f}")
            lines.append(
                f"| {run['universe']} | {run['scenario']} | " + " | ".join(cells) + f" | {cov} |"
            )
    for hrs, per in r["forward"].items():
        lines += ["", f"## Selection test: {hrs}h forward return of members (all splits)", ""]
        lines.append(
            "| Universe | n | missing | mean % | median % | hit % | net mean % | uplift % |"
        )
        lines.append("|---|---|---|---|---|---|---|---|")
        for u, rows in per.items():
            x = rows["all"]
            lines.append(
                f"| {u} | {x['n']} | {x['missing']} | {fmt(x['mean_pct'], '{:+.2f}')} | "
                f"{fmt(x['median_pct'], '{:+.2f}')} | {fmt(x['hit_rate_pct'], '{:.0f}')} | "
                f"{fmt(x['net_mean_pct'], '{:+.2f}')} | {fmt(x['uplift_pct'], '{:+.2f}')} |"
            )
    lines += ["", "## Gates (Phase 9, indicative)", ""]
    for g in r["gates"]:
        lines.append(
            f"- {g['gate']}: {fmt(g['value'], '{:.2f}')} (target {g['threshold']}) -> "
            f"{g['status']} ({g['trades']} out-of-sample trades, {g['min_trades']} needed)"
        )
    lines += ["", "Assumptions and limitations: docs/BACKTESTING.md"]
    return "\n".join(lines)


def write_files(r: dict[str, Any], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(r, indent=1, sort_keys=True))
    (out / "report.md").write_text(markdown(r) + "\n")
    trade_cols = [
        "universe", "scenario", "position", "strategy", "mint", "entry_at", "exit_at",
        "entry_price", "exit_price", "exit_fill_price", "size_usd", "qty", "cost_pct",
        "proceeds_usd", "pnl_usd", "return_pct", "reason",
    ]  # fmt: skip
    with (out / "trades.csv").open("w", newline="") as fh:
        wr = csv.DictWriter(fh, trade_cols, extrasaction="ignore")
        wr.writeheader()
        for run in r["runs"]:
            for t in run["trades"]:
                wr.writerow({**t, "universe": run["universe"], "scenario": run["scenario"]})
    with (out / "equity.csv").open("w", newline="") as fh:
        wr2 = csv.writer(fh)
        wr2.writerow(["universe", "scenario", "at", "equity_usd", "exposure_usd"])
        for run in r["runs"]:
            for at, eq, exp in run["equity"]:
                wr2.writerow([run["universe"], run["scenario"], at, eq, exp])


async def store(session: AsyncSession, r: dict[str, Any]) -> int:
    row = BacktestRun(
        version=r["version"],
        strategy_version=r["strategy_version"],
        model_version=r["model_version"],
        window_start=datetime.fromisoformat(r["window"]["start"]),
        window_end=datetime.fromisoformat(r["window"]["end"]),
        sealed=r["window"]["sealed"],
        overrides=r["params"]["overrides"],
        input_digest=r["data"]["input_digest"],
        result_digest=r["result_digest"],
        report=r,
    )
    session.add(row)
    await session.flush()
    return row.id
