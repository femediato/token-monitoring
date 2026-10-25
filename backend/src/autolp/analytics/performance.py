"""Performance analytics over completed paper trades (pure functions, analytics-v1).

Trades are the dicts of `paper.report._trade`. Daily results group trades by the UTC day
they closed (realised P/L); the day's equity change comes from the marked equity curve,
so it also includes open positions.
"""

import statistics
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from autolp.backtest.metrics import trade_costs_usd

STOP_PREFIX = ("stop", "trail")  # exits that fill at or past the stop


def _hours(t: dict[str, Any]) -> float:
    a, b = datetime.fromisoformat(t["entry_at"]), datetime.fromisoformat(t["exit_at"])
    return (b - a).total_seconds() / 3600


def cost_breakdown(t: dict[str, Any]) -> dict[str, float]:
    """Where a trade's costs went, in USD. Entry and exit costs are against the small
    reference quote and include the swap fees; stop overshoot is how far below its stop
    a stop exit's reference price was (a price move caught late, not a trading cost)."""
    entry = t["size_usd"] - t["qty"] * t["entry_price"]
    exit_ = t["qty"] * t["exit_price"] - t["proceeds_usd"]
    trigger = t.get("exit_trigger_price")
    overshoot = 0.0
    if (t["reason"] or "").startswith(STOP_PREFIX) and trigger:
        overshoot = max(0.0, t["qty"] * (trigger - t["exit_price"]))
    return {
        "entry_usd": entry,
        "exit_usd": exit_,
        "fees_usd": t.get("fees_usd", 0.0),
        "total_usd": trade_costs_usd(t),
        "stop_overshoot_usd": overshoot,
    }


def group_stats(trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    pnl = [t["pnl_usd"] for t in trades]
    wins = [x for x in pnl if x > 0]
    gross_loss = -sum(x for x in pnl if x <= 0)
    costs = [cost_breakdown(t) for t in trades]
    volume = sum(t["size_usd"] + t["proceeds_usd"] for t in trades)
    total_costs = sum(c["total_usd"] for c in costs)
    return {
        "trades": len(trades),
        "wins": len(wins),
        "win_rate_pct": len(wins) / len(trades) * 100 if trades else None,
        "net_pnl_usd": sum(pnl),
        "profit_factor": sum(wins) / gross_loss if gross_loss > 0 else None,
        "expectancy_usd": statistics.fmean(pnl) if pnl else None,
        "expectancy_pct": statistics.fmean(t["return_pct"] for t in trades) if trades else None,
        "best_pct": max((t["return_pct"] for t in trades), default=None),
        "worst_pct": min((t["return_pct"] for t in trades), default=None),
        "avg_hold_hours": statistics.fmean(_hours(t) for t in trades) if trades else None,
        "exits": dict(sorted(Counter(t["reason"] for t in trades).items())),
        # P/L before costs: what the price moves alone made.
        "pnl_before_costs_usd": sum(pnl) + total_costs,
        "costs": {
            "total_usd": total_costs,
            "entry_usd": sum(c["entry_usd"] for c in costs),
            "exit_usd": sum(c["exit_usd"] for c in costs),
            "fees_usd": sum(c["fees_usd"] for c in costs),
            "stop_overshoot_usd": sum(c["stop_overshoot_usd"] for c in costs),
            "volume_usd": volume,
            "pct_of_volume": total_costs / volume * 100 if volume else None,
            "per_trade_usd": total_costs / len(trades) if trades else None,
        },
    }


def by_strategy(trades: Sequence[dict[str, Any]], strategies: Sequence[str]) -> dict[str, Any]:
    """Every enabled strategy (also those without trades), then strategies that are no
    longer enabled but have trades, then all trades together."""
    names = list(dict.fromkeys([*strategies, *sorted({t["strategy"] for t in trades})]))
    out = {s: group_stats([t for t in trades if t["strategy"] == s]) for s in names}
    out["all"] = group_stats(trades)
    return out


def daily(
    trades: Sequence[dict[str, Any]],
    day_close_equity: Sequence[tuple[date, float]],
    starting_equity: float,
) -> list[dict[str, Any]]:
    """One row per UTC day since the start: realised results of the trades closed that
    day and the change in marked equity (last mark of the day vs. the previous day's)."""
    closed: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        closed[datetime.fromisoformat(t["exit_at"]).date()].append(t)
    equity = dict(day_close_equity)
    days = sorted(set(equity) | set(closed))
    rows, prev = [], starting_equity
    for d in days:
        ts = closed.get(d, [])
        eq = equity.get(d)
        rows.append(
            {
                "day": d.isoformat(),
                "trades": len(ts),
                "wins": sum(1 for t in ts if t["pnl_usd"] > 0),
                "pnl_usd": sum(t["pnl_usd"] for t in ts),
                "costs_usd": sum(trade_costs_usd(t) for t in ts),
                "equity_usd": eq,
                "return_pct": (eq / prev - 1) * 100 if eq is not None and prev else None,
            }
        )
        if eq is not None:
            prev = eq
    return rows
