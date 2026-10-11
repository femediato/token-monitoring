"""Shared risk rules: position sizing, portfolio limits and exit checks (strat-v1).

All amounts are paper amounts in USD. Nothing here places orders.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from autolp.strategies.bars import Bar
from autolp.strategies.params import RiskParams
from autolp.strategies.rules import Setup


@dataclass
class Position:
    id: int
    strategy: str
    mint: str
    signal_at: datetime
    entry_at: datetime  # start of the bar whose open filled the entry
    entry_price: float  # fill price per token, before costs
    qty: float  # tokens bought
    size_usd: float  # cash committed, incl. entry cost and fee
    stop: float
    target: float
    exit_by: datetime
    cost_pct: float  # one-way execution cost estimate (the quote) used for sizing
    planned_risk_usd: float
    checked_until: datetime  # bars ending at or before this have been checked for exits
    exec_cost_pct: float = field(default=-1.0)  # realised one-way cost; defaults to cost_pct
    pending_exit: str | None = None  # an exit whose swap failed, retried on the next candle
    exit_attempts: int = 0

    def __post_init__(self) -> None:
        if self.exec_cost_pct < 0:
            self.exec_cost_pct = self.cost_pct


@dataclass(frozen=True)
class Exit:
    reason: str  # stop_gap | stop | target_gap | target | time
    at: datetime
    price: float  # trigger / fill price per token, before costs


@dataclass(frozen=True)
class Sizing:
    size_usd: float
    planned_risk_usd: float
    binding: str  # which limit set the size: risk | cash | quoted_size | liquidity


def size_position(
    setup: Setup,
    equity: float,
    cash: float,
    liquidity_usd: float | None,
    quoted_size_usd: float,
    p: RiskParams,
) -> Sizing | str:
    """Size so the loss at the stop, including round-trip costs and fees, is at most
    risk_per_trade_pct of equity. Returns a rejection reason instead when not tradeable."""
    if liquidity_usd is None or liquidity_usd <= 0:
        return "liquidity_unknown"
    stop_dist = (setup.entry_ref - setup.stop) / setup.entry_ref
    if stop_dist <= 0:
        return "invalid_stop"
    loss_per_usd = stop_dist + 2 * setup.cost_pct / 100
    fees = 2 * p.fee_usd_per_swap
    budget = equity * p.risk_per_trade_pct / 100 - fees
    if budget <= 0:
        return "risk_budget_exhausted"
    caps = {
        "risk": budget / loss_per_usd,
        "cash": cash,
        # The cost estimate is only valid up to the size it was quoted for.
        "quoted_size": quoted_size_usd,
        "liquidity": liquidity_usd * p.max_liquidity_share_pct / 100,
    }
    binding = min(caps, key=lambda k: caps[k])
    size = caps[binding]
    if size < p.min_order_usd:
        return f"below_min_order:{binding}"
    return Sizing(size, size * loss_per_usd + fees, binding)


def check_exit(pos: Position, bar: Bar) -> Exit | None:
    """Exit triggered by one completed bar. Within a bar the order of the high and the low
    is unknown, so the stop is assumed to trade first (conservative); a bar opening beyond
    a level fills at the open (gap)."""
    if bar.open <= pos.stop:
        return Exit("stop_gap", bar.start, bar.open)
    if bar.low <= pos.stop:
        return Exit("stop", bar.start, pos.stop)
    if bar.open >= pos.target:
        return Exit("target_gap", bar.start, bar.open)
    if bar.high >= pos.target:
        return Exit("target", bar.start, pos.target)
    if bar.end >= pos.exit_by:
        return Exit("time", bar.end, bar.close)
    return None


def proceeds(pos: Position, price: float, fee_usd: float) -> float:
    return pos.qty * price * (1 - pos.exec_cost_pct / 100) - fee_usd


def hold_deadline(entry_at: datetime, hours: float) -> datetime:
    return entry_at + timedelta(hours=hours)
