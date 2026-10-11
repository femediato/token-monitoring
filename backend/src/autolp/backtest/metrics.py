"""Performance metrics for a backtest run (pure functions).

Trades belong to the split in which they were entered; equity-based metrics (return,
drawdown, Sharpe/Sortino, exposure) use the marked equity curve inside the split.
"""

import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

DAYS_PER_YEAR = 365  # crypto trades every day
MIN_DAYS_FOR_RATIOS = 14  # Sharpe / Sortino are not reported on fewer daily returns


@dataclass(frozen=True)
class Point:
    at: datetime
    equity: float
    exposure_usd: float  # marked value of open positions


def trade_costs_usd(t: dict[str, Any]) -> float:
    """Everything the trade paid beyond the price move: entry cost and fee, exit cost and
    fee, and stop slippage (exit fill below the trigger)."""
    entry = t["size_usd"] - t["qty"] * t["entry_price"]
    exit_ = t["qty"] * t["exit_price"] - t["proceeds_usd"]
    return float(entry + exit_)


def max_drawdown_pct(values: Sequence[float]) -> float:
    peak, worst = -math.inf, 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak * 100)
    return worst


def _ratio(returns: Sequence[float], downside: bool) -> float | None:
    """Annualised Sharpe (or Sortino) of daily returns, risk-free rate 0."""
    if len(returns) < MIN_DAYS_FOR_RATIOS:
        return None
    mean = statistics.fmean(returns)
    if downside:
        dev = math.sqrt(statistics.fmean([min(r, 0.0) ** 2 for r in returns]))
    else:
        dev = statistics.pstdev(returns)
    if dev == 0:
        return None
    return mean / dev * math.sqrt(DAYS_PER_YEAR)


def daily_returns(curve: Sequence[Point]) -> list[float]:
    """Returns between equity at successive UTC midnights."""
    daily = [p.equity for p in curve if p.at.hour == 0 and p.at.minute == 0]
    return [b / a - 1 for a, b in zip(daily, daily[1:], strict=False) if a > 0]


def _in(t: datetime, start: datetime, end: datetime) -> bool:
    return start <= t < end


def summarize(
    trades: Sequence[dict[str, Any]],
    curve: Sequence[Point],
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    pts = [p for p in curve if start <= p.at <= end]
    ts = [t for t in trades if _in(datetime.fromisoformat(t["entry_at"]), start, end)]
    pnl = [t["pnl_usd"] for t in ts]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x <= 0]
    gross_profit, gross_loss = sum(wins), -sum(losses)
    costs = [trade_costs_usd(t) for t in ts]
    rets = daily_returns(pts)
    by_strategy: dict[str, dict[str, float]] = defaultdict(lambda: {"trades": 0, "pnl_usd": 0.0})
    for t in ts:
        by_strategy[t["strategy"]]["trades"] += 1
        by_strategy[t["strategy"]]["pnl_usd"] += t["pnl_usd"]
    eq0 = pts[0].equity if pts else None
    eq1 = pts[-1].equity if pts else None
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "hours": round((end - start).total_seconds() / 3600, 2),
        "trades": len(ts),
        "net_pnl_usd": sum(pnl),
        "net_return_pct": (eq1 / eq0 - 1) * 100 if eq0 and eq1 is not None else None,
        "win_rate_pct": len(wins) / len(ts) * 100 if ts else None,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "gross_profit_usd": gross_profit,
        "gross_loss_usd": gross_loss,
        "expectancy_usd": statistics.fmean(pnl) if pnl else None,
        "expectancy_pct": statistics.fmean(t["return_pct"] for t in ts) if ts else None,
        "max_drawdown_pct": max_drawdown_pct([p.equity for p in pts]),
        "sharpe": _ratio(rets, downside=False),
        "sortino": _ratio(rets, downside=True),
        "return_days": len(rets),
        "avg_cost_pct": (
            statistics.fmean(c / t["size_usd"] * 100 for c, t in zip(costs, ts, strict=True))
            if ts
            else None
        ),
        "costs_usd": sum(costs),
        "exposure_pct": (
            statistics.fmean(p.exposure_usd / p.equity * 100 for p in pts if p.equity > 0)
            if pts
            else None
        ),
        "exits": dict(sorted(Counter(t["reason"] for t in ts).items())),
        "by_strategy": {k: by_strategy[k] for k in sorted(by_strategy)},
    }


def forward_summary(rets: Sequence[float], nets: Sequence[float], missing: int) -> dict[str, Any]:
    """Equal-weight forward returns of universe members (gross, and net of the quoted
    round-trip cost where one was known)."""
    return {
        "n": len(rets),
        "missing": missing,
        "mean_pct": statistics.fmean(rets) if rets else None,
        "median_pct": statistics.median(rets) if rets else None,
        "hit_rate_pct": sum(1 for r in rets if r > 0) / len(rets) * 100 if rets else None,
        "net_mean_pct": statistics.fmean(nets) if nets else None,
        "net_n": len(nets),
    }
