"""Paper trading report and reconciliation (paper-v1).

`reconcile` rebuilds every position and the account's cash from the stored fills and checks
them against what the engine recorded: Phase 7's acceptance ("every simulated position can
be reconciled from signal creation through final exit, including fees and execution
assumptions"). `summary` computes the same metrics as the backtest, the execution quality
of the paper fills and how they compare with the backtest fill model.

    python -m autolp.paper.report
"""

import asyncio
import json
import statistics
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autolp.backtest.metrics import Point, summarize
from autolp.backtest.params import BT
from autolp.db.base import utcnow
from autolp.db.models import PaperAccount, PaperEquity, PaperEvent, PaperFill, PaperPosition
from autolp.paper.engine import mark_value
from autolp.paper.params import PAPER, PAPER_VERSION
from autolp.strategies.params import RiskParams

TOLERANCE_USD = 1e-6
GATE_DAYS = 30  # master plan: at least 30 days of forward paper trading
GATE_TRADES = 100  # Phase 9: at least 100 completed paper trades


def _ok_fills(fills: Sequence[PaperFill], side: str) -> list[PaperFill]:
    return [f for f in fills if f.side == side and f.error is None]


def check_position(
    pos: PaperPosition, events: Sequence[PaperEvent], fills: Sequence[PaperFill]
) -> list[str]:
    """Every inconsistency between a position, its events and its fills."""
    issues: list[str] = []
    kinds = [e.kind for e in events]
    for k in ("signal", "order"):
        if k not in kinds:
            issues.append(f"no {k} event")
    buys, sells = _ok_fills(fills, "buy"), _ok_fills(fills, "sell")
    if pos.status in ("pending", "cancelled"):
        if buys or sells:
            issues.append(f"{pos.status} position has a fill")
        if pos.status == "cancelled" and "cancel" not in kinds:
            issues.append("no cancel event")
        return issues
    if len(buys) != 1:
        return [*issues, f"{len(buys)} entry fills"]
    buy = buys[0]
    if "fill" not in kinds:
        issues.append("no fill event")
    if pos.qty is None or abs(pos.qty - (buy.qty or 0)) > 1e-12 * max(1.0, pos.qty or 0):
        issues.append("quantity differs from the entry fill")
    if buy.usd is None or buy.fee_usd is None:
        return [*issues, "entry fill without amounts"]
    if buy.usd + buy.fee_usd > pos.size_usd + TOLERANCE_USD:
        issues.append("entry spent more than the order size")
    if pos.status == "open":
        if sells:
            issues.append("open position has an exit fill")
        return issues
    if len(sells) != 1:
        return [*issues, f"{len(sells)} exit fills"]
    sell = sells[0]
    if "exit" not in kinds:
        issues.append("no exit event")
    if int(sell.in_amount_raw or 0) != int(buy.out_amount_raw or -1):
        issues.append("exit sold a different amount than the entry bought")
    if sell.usd is None or sell.fee_usd is None:
        return [*issues, "exit fill without amounts"]
    proceeds = sell.usd - sell.fee_usd
    if pos.proceeds_usd is None or abs(pos.proceeds_usd - proceeds) > TOLERANCE_USD:
        issues.append("proceeds differ from the exit fill")
    pnl = proceeds - (buy.usd + buy.fee_usd)
    if pos.pnl_usd is None or abs(pos.pnl_usd - pnl) > TOLERANCE_USD:
        issues.append("P/L differs from the fills")
    failed_exits = sum(1 for f in fills if f.side == "sell" and f.error is not None)
    if failed_exits != pos.exit_attempts:
        issues.append("exit attempts differ from the failed exit fills")
    return issues


async def _load(
    session: AsyncSession, acct_id: int
) -> tuple[list[PaperPosition], dict[int, list[PaperEvent]], dict[int, list[PaperFill]]]:
    positions = list(
        await session.scalars(
            select(PaperPosition)
            .where(PaperPosition.account_id == acct_id)
            .order_by(PaperPosition.id)
        )
    )
    events: dict[int, list[PaperEvent]] = defaultdict(list)
    for e in await session.scalars(
        select(PaperEvent)
        .where(PaperEvent.account_id == acct_id, PaperEvent.position_id.is_not(None))
        .order_by(PaperEvent.id)
    ):
        assert e.position_id is not None
        events[e.position_id].append(e)
    fills: dict[int, list[PaperFill]] = defaultdict(list)
    ids = [p.id for p in positions]
    if ids:
        for f in await session.scalars(
            select(PaperFill).where(PaperFill.position_id.in_(ids)).order_by(PaperFill.id)
        ):
            fills[f.position_id].append(f)
    return positions, events, fills


async def reconcile(session: AsyncSession, acct: PaperAccount) -> dict[str, Any]:
    positions, events, fills = await _load(session, acct.id)
    issues = [
        {"position": p.id, "issue": i}
        for p in positions
        for i in check_position(p, events[p.id], fills[p.id])
    ]
    cash = acct.starting_equity_usd
    for fs in fills.values():
        for f in fs:
            if f.error is None and f.usd is not None and f.fee_usd is not None:
                cash += -(f.usd + f.fee_usd) if f.side == "buy" else f.usd - f.fee_usd
    if abs(cash - acct.cash_usd) > TOLERANCE_USD * max(1, len(positions)):
        issues.append({"position": None, "issue": f"cash {acct.cash_usd} != fills {cash}"})
    return {
        "ok": not issues,
        "positions": len(positions),
        "cash_from_fills_usd": cash,
        "cash_usd": acct.cash_usd,
        "issues": issues,
    }


def _mean(xs: Sequence[float | None]) -> float | None:
    vals = [x for x in xs if x is not None]
    return statistics.fmean(vals) if vals else None


def shortfall_pct(f: PaperFill) -> float | None:
    """How much worse the paper fill was than the backtest model price, in percent
    (positive: paid more on a buy, received less on a sell)."""
    if f.model_price is None or f.fill_price is None or f.model_price <= 0:
        return None
    if f.side == "buy":
        return (f.fill_price / f.model_price - 1) * 100
    return (1 - f.fill_price / f.model_price) * 100


def execution_quality(
    positions: Sequence[PaperPosition], fills: dict[int, list[PaperFill]]
) -> dict[str, Any]:
    by_id = {p.id: p for p in positions}
    out: dict[str, Any] = {}
    for side in ("buy", "sell"):
        fs = [f for p in positions for f in fills[p.id] if f.side == side]
        ok = [f for f in fs if f.error is None]
        quoted = [by_id[f.position_id].quoted_cost_pct for f in ok]
        realised = [f.cost_pct for f in ok]
        out[side] = {
            "attempts": len(fs),
            "failed": len(fs) - len(ok),
            "failure_pct": (len(fs) - len(ok)) / len(fs) * 100 if fs else None,
            "cost_pct": _mean(realised),
            "quoted_cost_pct": _mean(quoted),
            "price_impact_pct": _mean([f.price_impact_pct for f in ok]),
            "model_n": sum(1 for f in ok if f.model_price is not None),
            "shortfall_vs_model_pct": _mean([shortfall_pct(f) for f in ok]),
        }
    stops = [
        f
        for p in positions
        if (p.exit_reason or "").startswith("stop")
        for f in _ok_fills(fills[p.id], "sell")
        if f.trigger_price
    ]
    slips = [(1 - (f.fill_price or 0) / (f.trigger_price or 1)) * 100 for f in stops]
    out["stop_slippage_pct"] = _mean(slips)
    out["stops"] = len(slips)
    attempts = out["buy"]["attempts"] + out["sell"]["attempts"]
    failed = out["buy"]["failed"] + out["sell"]["failed"]
    costs = [(f.cost_pct, by_id[f.position_id].quoted_cost_pct) for p in positions
             for f in fills[p.id] if f.error is None and f.cost_pct is not None]  # fmt: skip
    base = BT.scenarios["base"]
    out["vs_backtest"] = {
        "assumed": {
            "cost_multiplier": base.cost_multiplier,
            "stop_slippage_pct": base.stop_slippage_pct,
            "swap_failure_pct": base.swap_failure_pct,
        },
        "observed": {
            # realised cost / quoted estimate, over fills with a positive estimate
            "cost_multiplier": _mean([c / q for c, q in costs if q and q > 0]),
            "stop_slippage_pct": out["stop_slippage_pct"],
            "swap_failure_pct": failed / attempts * 100 if attempts else None,
        },
    }
    return out


def _trade(p: PaperPosition, fills: Sequence[PaperFill]) -> dict[str, Any]:
    buy, sell = _ok_fills(fills, "buy")[0], _ok_fills(fills, "sell")[0]
    basis = (buy.usd or 0) + (buy.fee_usd or 0)
    assert p.entry_at and p.exit_at and p.pnl_usd is not None
    return {
        "position": p.id,
        "strategy": p.strategy,
        "mint": p.token_mint,
        "entry_at": p.entry_at.isoformat(),
        "exit_at": p.exit_at.isoformat(),
        "entry_price": p.entry_price,
        "exit_price": p.exit_price,
        "exit_fill_price": sell.fill_price,
        "exit_trigger_price": p.exit_trigger_price,
        "size_usd": basis,
        "qty": p.qty,
        "proceeds_usd": p.proceeds_usd,
        "pnl_usd": p.pnl_usd,
        "return_pct": p.pnl_usd / basis * 100 if basis else None,
        "reason": p.exit_reason,
        "entry_cost_pct": p.entry_cost_pct,
        "exit_cost_pct": p.exit_cost_pct,
        "fees_usd": (buy.fee_usd or 0) + (sell.fee_usd or 0),
    }


def exposure(book: Sequence[PaperPosition], equity: float, risk: RiskParams) -> dict[str, Any]:
    """What the open book holds against the portfolio limits. Value is marked at the last
    small sell quote; risk to stop is what the positions would lose (before costs) if every
    one exited exactly at its stop now."""
    open_ = [p for p in book if p.status == "open"]
    rows: list[dict[str, Any]] = []
    for p in open_:
        value = mark_value(p)
        rows.append(
            {
                "position": p.id,
                "strategy": p.strategy,
                "mint": p.token_mint,
                "value_usd": value,
                "pct_of_equity": value / equity * 100 if equity else None,
                "planned_risk_usd": p.planned_risk_usd,
                "risk_to_stop_usd": max(0.0, value - (p.qty or 0.0) * p.stop),
            }
        )
    by_strategy: dict[str, float] = defaultdict(float)
    for r in rows:
        by_strategy[r["strategy"]] += r["value_usd"]
    invested = sum(r["value_usd"] for r in rows)
    planned = sum(p.planned_risk_usd for p in book)  # pending orders count, as in admit()
    return {
        "invested_usd": invested,
        "invested_pct": invested / equity * 100 if equity else None,
        "positions": len(book),
        "max_positions": risk.max_positions,
        "planned_risk_usd": planned,
        "planned_risk_pct": planned / equity * 100 if equity else None,
        "max_open_risk_pct": risk.max_open_risk_pct,
        "risk_to_stop_usd": sum(r["risk_to_stop_usd"] for r in rows),
        "by_strategy": {k: by_strategy[k] for k in sorted(by_strategy)},
        "items": rows,
    }


async def account(session: AsyncSession, name: str = PAPER.account) -> PaperAccount | None:
    return await session.scalar(select(PaperAccount).where(PaperAccount.name == name))


async def summary(
    session: AsyncSession, acct: PaperAccount, at: datetime | None = None
) -> dict[str, Any]:
    at = at or utcnow()
    positions, events, fills = await _load(session, acct.id)
    curve = [
        Point(r.at, r.equity_usd, r.positions_usd)
        for r in await session.scalars(
            select(PaperEquity).where(PaperEquity.account_id == acct.id).order_by(PaperEquity.at)
        )
    ]
    closed = [p for p in positions if p.status == "closed"]
    trades = [_trade(p, fills[p.id]) for p in closed]
    start = curve[0].at if curve else acct.created_at
    metrics = summarize(trades, curve, start, at)
    counts: dict[str, int] = defaultdict(int)
    for p in positions:
        counts[p.status] += 1
    days = (at - start).total_seconds() / 86400
    equity = curve[-1].equity if curve else acct.cash_usd
    return {
        "version": PAPER_VERSION,
        "account": {
            "name": acct.name,
            "created_at": acct.created_at.isoformat(),
            "starting_equity_usd": acct.starting_equity_usd,
            "cash_usd": acct.cash_usd,
            "equity_usd": equity,
            "positions_usd": curve[-1].exposure_usd if curve else 0.0,
            "params": acct.params,
        },
        "running_since": start.isoformat(),
        "days": days,
        "positions": dict(counts),
        "metrics": metrics,
        "execution": execution_quality(positions, fills),
        "progress": {
            "days": days,
            "days_required": GATE_DAYS,
            "trades": len(trades),
            "trades_required": GATE_TRADES,
        },
        "trades": trades,
        "reconciliation": await reconcile(session, acct),
    }


def text_report(r: dict[str, Any]) -> str:
    m, e, a = r["metrics"], r["execution"], r["account"]

    def f(v: Any, nd: int = 2) -> str:
        return "-" if v is None else f"{v:,.{nd}f}"

    rec = r["reconciliation"]
    lines = [
        f"Paper trading {r['version']} - account '{a['name']}' since {r['running_since'][:16]}",
        f"  days: {f(r['days'], 1)} of {r['progress']['days_required']}, "
        f"completed trades: {m['trades']} of {r['progress']['trades_required']}",
        f"  equity: {f(a['equity_usd'])} USD (cash {f(a['cash_usd'])}), "
        f"net return {f(m['net_return_pct'])}%, max drawdown {f(m['max_drawdown_pct'])}%",
        f"  win rate {f(m['win_rate_pct'], 1)}%, profit factor {f(m['profit_factor'])}, "
        f"expectancy {f(m['expectancy_usd'])} USD",
        f"  positions: {json.dumps(r['positions'], sort_keys=True)}",
        "  execution (paper fills vs. backtest base assumptions):",
    ]
    for side in ("buy", "sell"):
        s = e[side]
        lines.append(
            f"    {side}: {s['attempts']} attempts, {s['failed']} failed; cost {f(s['cost_pct'])}%"
            f" (quoted {f(s['quoted_cost_pct'])}%); vs model {f(s['shortfall_vs_model_pct'])}%"
            f" over {s['model_n']}"
        )
    obs, ass = e["vs_backtest"]["observed"], e["vs_backtest"]["assumed"]
    for k in ("cost_multiplier", "stop_slippage_pct", "swap_failure_pct"):
        lines.append(f"    {k}: observed {f(obs[k])}, assumed {f(ass[k])}")
    lines.append(
        f"  reconciliation: {'OK' if rec['ok'] else 'FAILED'} over {rec['positions']} positions"
        + "".join(f"\n    position {i['position']}: {i['issue']}" for i in rec["issues"])
    )
    return "\n".join(lines)


async def _main() -> None:
    from autolp.config import get_settings
    from autolp.db.session import make_engine, make_sessionmaker

    db = make_engine(get_settings())
    try:
        async with make_sessionmaker(db)() as s:
            acct = await account(s)
            if acct is None:
                print("No paper account yet: the paper_trading job has not run.")
                return
            print(text_report(await summary(s, acct)))
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
