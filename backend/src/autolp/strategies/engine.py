"""Deterministic paper decision engine (strat-v1).

`Engine.step(at, views)` is called on a 5-minute grid with point-in-time market views. In
order, it fills pending entries, checks exits on newly completed candles, and evaluates new
setups against the shared risk rules. Every action is appended to `decisions`; the same
inputs always give the same decisions (see `digest`).

Fill model (v0, refined in Phase 6): an accepted entry fills at the open of the first
candle starting at or after the signal, plus the quoted one-way cost and a fixed fee; exits
fill at the trigger price (or the open on a gap) minus the same cost and fee.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from autolp.strategies import rules
from autolp.strategies.params import PARAMS, StrategyParams
from autolp.strategies.risk import (
    Position,
    Sizing,
    check_exit,
    hold_deadline,
    proceeds,
    size_position,
)
from autolp.strategies.rules import MarketView, Setup, Signal

FILL_WINDOW = timedelta(minutes=10)  # an entry not filled within this is cancelled


@dataclass(frozen=True)
class Decision:
    at: datetime
    action: str  # signal | reject | order | fill | cancel | exit | stop_day
    strategy: str | None
    mint: str | None
    reason: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": self.at.isoformat(),
            "action": self.action,
            "strategy": self.strategy,
            "mint": self.mint,
            "reason": self.reason,
            "data": {k: _num(v) for k, v in sorted(self.data.items())},
        }


def _num(v: Any) -> Any:
    return round(v, 10) if isinstance(v, float) else v


@dataclass
class Pending:
    setup: Setup
    sizing: Sizing


@dataclass
class StepResult:
    signals: list[Signal]
    decisions: list[Decision]


class Engine:
    def __init__(self, p: StrategyParams = PARAMS) -> None:
        self.p = p
        self.cash = p.risk.starting_equity_usd
        self.positions: list[Position] = []
        self.pending: list[Pending] = []
        self.closed: list[dict[str, Any]] = []
        self.marks: dict[str, float] = {}
        self.decisions: list[Decision] = []
        self.day: date | None = None
        self.day_start_equity = self.cash
        self._next_id = 1
        # A signal candle is considered once per strategy and token, even if no newer
        # candle has arrived by the next step.
        self._seen: set[tuple[str, str, datetime]] = set()

    # ------------------------------------------------------------------ state

    def equity(self) -> float:
        return self.cash + sum(
            pos.qty * self.marks.get(pos.mint, pos.entry_price) for pos in self.positions
        )

    def open_risk(self) -> float:
        return sum(x.planned_risk_usd for x in self.positions) + sum(
            x.sizing.planned_risk_usd for x in self.pending
        )

    def digest(self) -> str:
        h = hashlib.sha256()
        for d in self.decisions:
            h.update(json.dumps(d.as_dict(), sort_keys=True).encode())
            h.update(b"\n")
        return h.hexdigest()

    def _log(self, out: list[Decision], d: Decision) -> None:
        out.append(d)
        self.decisions.append(d)

    # ------------------------------------------------------------------ step

    def step(self, at: datetime, views: dict[str, MarketView]) -> StepResult:
        out: list[Decision] = []
        for mint in sorted(views):
            if views[mint].bars:
                self.marks[mint] = views[mint].bars[-1].close
        self._fill(at, views, out)
        self._exits(at, views, out)
        if self.day != at.date():
            self.day, self.day_start_equity = at.date(), self.equity()
        signals = [
            s
            for mint in sorted(views)
            if views[mint].evaluation is not None
            for s in rules.evaluate(views[mint], self.p)
        ]
        order = {name: i for i, name in enumerate(self.p.enabled)}
        setups = sorted((s for s in signals if s.setup), key=lambda s: (order[s.strategy], s.mint))
        for s in setups:
            assert s.setup is not None
            key = (s.strategy, s.mint, s.setup.signal_bar)
            if key in self._seen:
                continue
            self._seen.add(key)
            self._consider(at, s.setup, views[s.mint], out)
        return StepResult(signals, out)

    def _fill(self, at: datetime, views: dict[str, MarketView], out: list[Decision]) -> None:
        r = self.p.risk
        still: list[Pending] = []
        for pend in self.pending:
            s = pend.setup
            view = views.get(s.mint)
            bar = next((b for b in view.bars if b.start >= s.at), None) if view else None
            if bar is None:
                if at - s.at > FILL_WINDOW:
                    self._log(out, Decision(at, "cancel", s.strategy, s.mint, "no_fill_bar"))
                else:
                    still.append(pend)
                continue
            fill = bar.open * (1 + s.cost_pct / 100)
            size = pend.sizing.size_usd
            pos = Position(
                id=self._next_id,
                strategy=s.strategy,
                mint=s.mint,
                signal_at=s.at,
                entry_at=bar.start,
                entry_price=bar.open,
                qty=(size - r.fee_usd_per_swap) / fill,
                size_usd=size,
                stop=s.stop,
                target=s.target,
                exit_by=hold_deadline(bar.start, s.max_hold_hours),
                cost_pct=s.cost_pct,
                planned_risk_usd=pend.sizing.planned_risk_usd,
                checked_until=bar.start,
            )
            self._next_id += 1
            self.cash -= size
            self.positions.append(pos)
            self._log(
                out,
                Decision(
                    at,
                    "fill",
                    s.strategy,
                    s.mint,
                    None,
                    {
                        "position": pos.id,
                        "bar": bar.start.isoformat(),
                        "price": bar.open,
                        "fill_price": fill,
                        "qty": pos.qty,
                        "size_usd": size,
                    },
                ),
            )
        self.pending = still

    def _exits(self, at: datetime, views: dict[str, MarketView], out: list[Decision]) -> None:
        r = self.p.risk
        remaining: list[Position] = []
        for pos in self.positions:
            view = views.get(pos.mint)
            new_bars = [b for b in view.bars if b.start >= pos.checked_until] if view else []
            ex = None
            for bar in new_bars:
                pos.checked_until = bar.end
                ex = check_exit(pos, bar)
                if ex:
                    break
            if ex is not None:
                reason, price, ex_at = ex.reason, ex.price, ex.at
            elif at >= pos.exit_by and not new_bars:
                # Holding period over and no new candle: exit at the last known price.
                reason, price, ex_at = "time_no_data", self.marks.get(pos.mint, pos.entry_price), at
            else:
                remaining.append(pos)
                continue
            value = proceeds(pos, price, r.fee_usd_per_swap)
            self.cash += value
            pnl = value - pos.size_usd
            trade = {
                "position": pos.id,
                "strategy": pos.strategy,
                "mint": pos.mint,
                "entry_at": pos.entry_at.isoformat(),
                "exit_at": ex_at.isoformat(),
                "entry_price": pos.entry_price,
                "exit_price": price,
                "size_usd": pos.size_usd,
                "proceeds_usd": value,
                "pnl_usd": pnl,
                "return_pct": pnl / pos.size_usd * 100,
                "reason": reason,
            }
            self.closed.append(trade)
            self._log(
                out,
                Decision(
                    at,
                    "exit",
                    pos.strategy,
                    pos.mint,
                    reason,
                    {k: trade[k] for k in ("position", "exit_price", "pnl_usd", "return_pct")},
                ),
            )
        self.positions = remaining

    def _consider(self, at: datetime, s: Setup, view: MarketView, out: list[Decision]) -> None:
        r = self.p.risk
        base = {
            "signal_bar": s.signal_bar.isoformat(),
            "entry_ref": s.entry_ref,
            "stop": s.stop,
            "target": s.target,
        }
        self._log(out, Decision(at, "signal", s.strategy, s.mint, None, base))
        equity = self.equity()
        reason: str | None = None
        held = {x.mint for x in self.positions} | {x.setup.mint for x in self.pending}
        if equity <= self.day_start_equity * (1 - r.daily_loss_stop_pct / 100):
            reason = "daily_loss_stop"
        elif len(self.positions) + len(self.pending) >= r.max_positions:
            reason = "max_positions"
        elif r.one_position_per_token and s.mint in held:
            reason = "token_already_held"
        if reason is None:
            assert view.evaluation is not None
            sizing = size_position(
                s,
                equity,
                self.cash,
                view.evaluation.features.get("liquidity_usd"),
                r.quoted_size_usd,
                r,
            )
            if isinstance(sizing, str):
                reason = sizing
            elif self.open_risk() + sizing.planned_risk_usd > equity * r.max_open_risk_pct / 100:
                reason = "max_open_risk"
        if reason is not None:
            self._log(out, Decision(at, "reject", s.strategy, s.mint, reason, base))
            return
        assert not isinstance(sizing, str)
        self.pending.append(Pending(s, sizing))
        self._log(
            out,
            Decision(
                at,
                "order",
                s.strategy,
                s.mint,
                sizing.binding,
                {
                    **base,
                    "size_usd": sizing.size_usd,
                    "planned_risk_usd": sizing.planned_risk_usd,
                    "equity_usd": equity,
                    "cost_pct": s.cost_pct,
                },
            ),
        )
