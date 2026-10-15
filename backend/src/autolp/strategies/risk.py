"""Shared risk rules: position sizing, portfolio limits and exit checks (strat-v1).

All amounts are paper amounts in USD. Nothing here places orders.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from autolp.strategies.bars import Bar
from autolp.strategies.params import ManageParams, RiskParams
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
    atr: float = 0.0  # ATR at the signal, for a trailing stop
    initial_stop: float = field(default=-1.0)  # defaults to stop
    high: float = 0.0  # highest high since the entry (stop management)

    def __post_init__(self) -> None:
        if self.exec_cost_pct < 0:
            self.exec_cost_pct = self.cost_pct
        if self.initial_stop < 0:
            self.initial_stop = self.stop


@dataclass(frozen=True)
class Exit:
    reason: str  # stop_gap | stop | trail | target_gap | target | time
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
    loss_per_usd = stop_dist + 2 * setup.cost_pct / 100 + p.expected_stop_slippage_pct / 100
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


def admit(
    setup: Setup,
    liquidity_usd: float | None,
    equity: float,
    cash: float,
    day_start_equity: float,
    open_count: int,
    held: set[str],
    open_risk_usd: float,
    p: RiskParams,
) -> Sizing | str:
    """The shared portfolio rules for a new setup, in a fixed order: daily loss stop,
    position count, one position per token, sizing, combined open risk. Returns the sizing,
    or the reason the setup is rejected. `open_count`, `held` and `open_risk_usd` include
    orders that have not filled yet."""
    if equity <= day_start_equity * (1 - p.daily_loss_stop_pct / 100):
        return "daily_loss_stop"
    if open_count >= p.max_positions:
        return "max_positions"
    if p.one_position_per_token and setup.mint in held:
        return "token_already_held"
    sizing = size_position(setup, equity, cash, liquidity_usd, p.quoted_size_usd, p)
    if isinstance(sizing, str):
        return sizing
    if open_risk_usd + sizing.planned_risk_usd > equity * p.max_open_risk_pct / 100:
        return "max_open_risk"
    return sizing


def check_exit(pos: Position, bar: Bar) -> Exit | None:
    """Exit triggered by one completed bar. Within a bar the order of the high and the low
    is unknown, so the stop is assumed to trade first (conservative); a bar opening beyond
    a level fills at the open (gap). A stop raised by stop management exits as `trail`."""
    moved = pos.stop > pos.initial_stop
    if bar.open <= pos.stop:
        return Exit("trail" if moved else "stop_gap", bar.start, bar.open)
    if bar.low <= pos.stop:
        return Exit("trail" if moved else "stop", bar.start, pos.stop)
    if bar.open >= pos.target:
        return Exit("target_gap", bar.start, bar.open)
    if bar.high >= pos.target:
        return Exit("target", bar.start, pos.target)
    if bar.end >= pos.exit_by:
        return Exit("time", bar.end, bar.close)
    return None


def raised_stop(
    stop: float,
    initial_stop: float,
    entry: float,
    high: float,
    atr: float,
    cost_pct: float,
    m: ManageParams,
) -> float:
    """The stop after breakeven and trailing, given the highest price since the entry.
    Never lower than `stop`. R is the initial risk: entry - initial stop."""
    r = entry - initial_stop
    if r <= 0:
        return stop
    gain = high - entry
    if m.breakeven_at_r > 0 and gain >= m.breakeven_at_r * r:
        stop = max(stop, entry * (1 + 2 * cost_pct / 100))
    if m.trail_atr > 0 and atr > 0 and gain >= m.trail_after_r * r:
        stop = max(stop, high - m.trail_atr * atr)
    return stop


def manage_stop(pos: Position, bar: Bar, m: ManageParams) -> None:
    """Raise the stop after a completed bar without an exit (breakeven, trailing). It
    applies from the next bar on: within a bar the order of high and low is unknown."""
    pos.high = max(pos.high, bar.high)
    pos.stop = raised_stop(
        pos.stop, pos.initial_stop, pos.entry_price, pos.high, pos.atr, pos.exec_cost_pct, m
    )


@dataclass
class Breaker:
    """Losing-streak and drawdown pause (RiskParams; off by default): no new entries
    until `paused_until` after max_consecutive_losses losses in a row, or after equity
    fell drawdown_pause_pct below its peak (the peak then restarts from there)."""

    streak: int = 0
    peak: float = 0.0
    paused_until: datetime | None = None
    reason: str | None = None

    def on_exit(self, at: datetime, pnl_usd: float, p: RiskParams) -> None:
        self.streak = self.streak + 1 if pnl_usd <= 0 else 0
        if p.max_consecutive_losses > 0 and self.streak >= p.max_consecutive_losses:
            self._pause(at, "loss_streak", p)
            self.streak = 0

    def on_mark(self, at: datetime, equity: float, p: RiskParams) -> None:
        self.peak = max(self.peak, equity)
        if p.drawdown_pause_pct > 0 and equity <= self.peak * (1 - p.drawdown_pause_pct / 100):
            self._pause(at, "drawdown_pause", p)
            self.peak = equity

    def _pause(self, at: datetime, reason: str, p: RiskParams) -> None:
        self.paused_until, self.reason = at + timedelta(hours=p.pause_hours), reason

    def blocked(self, at: datetime) -> str | None:
        if self.paused_until is not None and at < self.paused_until:
            return self.reason
        return None


def proceeds(pos: Position, price: float, fee_usd: float) -> float:
    return pos.qty * price * (1 - pos.exec_cost_pct / 100) - fee_usd


def hold_deadline(entry_at: datetime, hours: float) -> datetime:
    return entry_at + timedelta(hours=hours)
