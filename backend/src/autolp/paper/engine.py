"""Paper trading engine (paper-v1): strat-v1 decisions, executable-quote fills.

Every minute (`PaperTrader.tick`):

1. Orders left pending by an interruption are cancelled.
2. Each open position is marked with a small Jupiter sell quote. A stop, target or holding
   limit triggers an exit, filled with a full-size sell quote. A failed quote is logged
   and retried on the next tick at whatever the price is then.
3. The strategies are evaluated on the same point-in-time views as the live signal job
   and the replay. Each setup is considered once (per strategy, token and signal candle)
   against the shared risk rules. An accepted order is filled at once with a full-size
   buy quote, or cancelled if the quote fails.
4. The account is marked to the latest quotes (`paper_equity`).
5. Fills old enough to have candles get the price the backtest fill model would have used,
   for the comparison in the report.

All state is in the database and every step is committed as it happens, so the engine
continues where it stopped after a restart. Nothing is signed or sent: quotes only.
"""

from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autolp.collector.jobs import store_raw
from autolp.collector.runs import JobResult
from autolp.db.base import utcnow
from autolp.db.models import PaperAccount, PaperEquity, PaperEvent, PaperFill, PaperPosition, Token
from autolp.log import get_logger
from autolp.paper import quotes
from autolp.paper.params import PAPER, PAPER_VERSION, PaperParams
from autolp.paper.quotes import Leg, Quoter
from autolp.strategies import rules
from autolp.strategies.data import load_history
from autolp.strategies.params import PARAMS, STRATEGY_VERSION, StrategyParams
from autolp.strategies.risk import admit
from autolp.strategies.rules import Setup

log = get_logger(__name__)
Factory = async_sessionmaker[AsyncSession]
LOCK_KEY = 0x6175746F6C70  # pg advisory lock: one paper engine at a time


def mark_value(p: PaperPosition) -> float:
    price = p.last_price if p.last_price is not None else p.entry_price
    return (p.qty or 0.0) * (price or 0.0)


def exit_trigger(p: PaperPosition, price: float | None, at: datetime) -> str | None:
    """Which exit, if any, the latest mark triggers. A missing mark triggers only the
    holding limit; a stop or target needs a fresh price."""
    if p.pending_exit:
        return p.pending_exit
    if price is not None and price <= p.stop:
        return "stop"
    if price is not None and price >= p.target:
        return "target"
    if p.exit_by is not None and at >= p.exit_by:
        return "time"
    return None


class PaperTrader:
    def __init__(
        self,
        factory: Factory,
        quoter: Quoter,
        p: StrategyParams = PARAMS,
        pp: PaperParams = PAPER,
    ) -> None:
        self.factory, self.quoter, self.p, self.pp = factory, quoter, p, pp

    # ------------------------------------------------------------------ tick

    async def tick(self, run_id: int, at: datetime | None = None) -> JobResult:
        at = (at or utcnow()).replace(microsecond=0)
        async with self.factory() as lock:
            if not await lock.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}):
                log.warning("paper_tick_skipped", reason="another engine holds the lock")
                return JobResult()
            try:
                return await self._tick(run_id, at)
            finally:
                await lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})

    async def _tick(self, run_id: int, at: datetime) -> JobResult:
        res = JobResult()
        acct_id = await self.account_id()
        await self._expire_pending(acct_id, at)
        await self._manage(acct_id, at, run_id, res)
        await self._enter(acct_id, at, run_id, res)
        await self._mark_equity(acct_id, at)
        await self._model_prices(at)
        return res

    async def account_id(self) -> int:
        async with self.factory() as s, s.begin():
            await s.execute(
                insert(PaperAccount)
                .values(
                    name=self.pp.account,
                    created_at=utcnow(),
                    starting_equity_usd=self.p.risk.starting_equity_usd,
                    cash_usd=self.p.risk.starting_equity_usd,
                    params={
                        "paper_version": PAPER_VERSION,
                        "strategy_version": STRATEGY_VERSION,
                        "paper": asdict(self.pp),
                        "strategy": asdict(self.p),
                    },
                )
                .on_conflict_do_nothing(index_elements=["name"])
            )
            acct_id = await s.scalar(
                select(PaperAccount.id).where(PaperAccount.name == self.pp.account)
            )
        assert acct_id is not None
        return acct_id

    async def _positions(self, s: AsyncSession, acct_id: int, *status: str) -> list[PaperPosition]:
        rows = await s.scalars(
            select(PaperPosition)
            .where(PaperPosition.account_id == acct_id, PaperPosition.status.in_(status))
            .order_by(PaperPosition.id)
        )
        return list(rows)

    @staticmethod
    def _event(acct_id: int, at: datetime, kind: str, **kw: Any) -> PaperEvent:
        return PaperEvent(account_id=acct_id, at=at, kind=kind, data=kw.pop("data", {}), **kw)

    async def _decimals(self, s: AsyncSession, mint: str) -> int | None:
        return await s.scalar(select(Token.decimals).where(Token.mint == mint))

    async def _raw(self, s: AsyncSession, leg: Leg, run_id: int, res: JobResult) -> list[int]:
        res.api_credits_used += len(leg.responses)
        return [await store_raw(s, r, run_id) for r in leg.responses]

    # ------------------------------------------------------------------ 1. interrupted

    async def _expire_pending(self, acct_id: int, at: datetime) -> None:
        limit = at - timedelta(minutes=self.pp.order_timeout_minutes)
        async with self.factory() as s, s.begin():
            for pos in await self._positions(s, acct_id, "pending"):
                if pos.signal_at <= limit:
                    pos.status, pos.cancel_reason = "cancelled", "interrupted"
                    s.add(
                        self._event(
                            acct_id, at, "cancel", position_id=pos.id, strategy=pos.strategy,
                            token_mint=pos.token_mint, reason="interrupted",
                        )
                    )  # fmt: skip

    # ------------------------------------------------------------------ 2. exits

    async def _manage(self, acct_id: int, at: datetime, run_id: int, res: JobResult) -> None:
        async with self.factory() as s:
            open_ids = [x.id for x in await self._positions(s, acct_id, "open")]
        for pid in open_ids:
            await self._manage_one(acct_id, pid, at, run_id, res)

    async def _manage_one(
        self, acct_id: int, pid: int, at: datetime, run_id: int, res: JobResult
    ) -> None:
        async with self.factory() as s:
            pos = await s.get_one(PaperPosition, pid)
            decimals = await self._decimals(s, pos.token_mint)
            held_raw = await s.scalar(
                select(PaperFill.out_amount_raw).where(
                    PaperFill.position_id == pid, PaperFill.side == "buy", PaperFill.error.is_(None)
                )
            )
        if decimals is None or held_raw is None:  # cannot happen for a filled position
            log.error("paper_position_unquotable", position=pid)
            return
        held = int(held_raw)
        hint = pos.last_price or pos.entry_price or pos.entry_ref
        mark = await quotes.reference_sell(
            self.quoter, pos.token_mint, decimals, held, self.pp.reference_usd, hint
        )
        price = mark.ref_price
        trigger = exit_trigger(pos, price, at)
        leg = None
        if trigger is not None:
            leg = await quotes.sell(self.quoter, pos.token_mint, decimals, held, price)
        async with self.factory() as s, s.begin():
            pos = await s.get_one(PaperPosition, pid)
            mark_ids = await self._raw(s, mark, run_id, res)
            if price is not None:
                pos.last_price, pos.last_price_at = price, mark.quoted_at
            if trigger is None or leg is None:
                return
            fill_ids = await self._raw(s, leg, run_id, res)
            level = {"stop": pos.stop, "target": pos.target}.get(trigger.removesuffix("_retry"))
            fill = self._fill_row(pos.id, "sell", at, leg, [*mark_ids, *fill_ids], level)
            s.add(fill)
            ev = {"position_id": pos.id, "strategy": pos.strategy, "token_mint": pos.token_mint}
            if leg.error is not None:
                pos.exit_attempts += 1
                pos.pending_exit = trigger
                s.add(
                    self._event(
                        acct_id, at, "exit_failed", reason=trigger,
                        data={"error": leg.error, "mark": price, "attempt": pos.exit_attempts},
                        **ev,
                    )
                )  # fmt: skip
                return
            assert leg.usd is not None
            fee = self.p.risk.fee_usd_per_swap
            proceeds = leg.usd - fee
            cost_basis = await self._cost_basis(s, pos.id)
            reason = trigger + ("_retry" if pos.exit_attempts else "")
            pos.status, pos.exit_at, pos.exit_reason = "closed", leg.quoted_at, reason
            pos.exit_trigger_price = level
            pos.exit_price = leg.ref_price if leg.ref_price is not None else leg.fill_price
            pos.exit_cost_pct, pos.proceeds_usd = leg.cost_pct, proceeds
            pos.pnl_usd, pos.pending_exit = proceeds - cost_basis, None
            await s.execute(
                update(PaperAccount)
                .where(PaperAccount.id == acct_id)
                .values(cash_usd=PaperAccount.cash_usd + proceeds)
            )
            s.add(
                self._event(
                    acct_id, at, "exit", reason=reason,
                    data={
                        "mark": price, "trigger_price": level, "fill_price": leg.fill_price,
                        "cost_pct": leg.cost_pct, "proceeds_usd": proceeds,
                        "pnl_usd": pos.pnl_usd,
                    },
                    **ev,
                )
            )  # fmt: skip
            res.rows_written += 1

    async def _cost_basis(self, s: AsyncSession, pid: int) -> float:
        """USDC paid for the entry plus its fee."""
        row = (
            await s.execute(
                select(PaperFill.usd, PaperFill.fee_usd).where(
                    PaperFill.position_id == pid, PaperFill.side == "buy", PaperFill.error.is_(None)
                )
            )
        ).one()
        return float(row.usd + row.fee_usd)

    def _fill_row(
        self, pid: int, side: str, at: datetime, leg: Leg, raw_ids: list[int], trigger: float | None
    ) -> PaperFill:
        return PaperFill(
            position_id=pid,
            side=side,
            decided_at=at,
            quoted_at=leg.quoted_at,
            in_amount_raw=leg.in_raw,
            out_amount_raw=leg.out_raw,
            usd=leg.usd,
            qty=leg.qty,
            ref_price=leg.ref_price,
            fill_price=leg.fill_price,
            cost_pct=leg.cost_pct,
            fee_usd=None if leg.error else self.p.risk.fee_usd_per_swap,
            price_impact_pct=leg.impact_pct,
            route_labels=leg.route,
            trigger_price=trigger,
            raw_ids=raw_ids,
            error=leg.error,
        )

    # ------------------------------------------------------------------ 3. entries

    async def _enter(self, acct_id: int, at: datetime, run_id: int, res: JobResult) -> None:
        async with self.factory() as s:
            book = await self._positions(s, acct_id, "open", "pending")
            history = await load_history(s, at, at, self.p, mints=[x.token_mint for x in book])
        views = history.views(at, [x.token_mint for x in book])
        order = {name: i for i, name in enumerate(self.p.enabled)}
        setups = sorted(
            (
                sig.setup
                for v in views.values()
                if v.evaluation is not None
                for sig in rules.evaluate(v, self.p)
                if sig.setup is not None
            ),
            key=lambda x: (order[x.strategy], x.mint),
        )
        for setup in setups:
            pid = await self._decide(acct_id, at, setup, views[setup.mint].evaluation)
            if pid is not None:
                await self._open(acct_id, pid, at, run_id, res)

    async def _day_start_equity(self, s: AsyncSession, acct_id: int, at: datetime) -> float | None:
        day = at.replace(hour=0, minute=0, second=0)
        return await s.scalar(
            select(PaperEquity.equity_usd)
            .where(PaperEquity.account_id == acct_id, PaperEquity.at >= day)
            .order_by(PaperEquity.at)
            .limit(1)
        )

    async def _decide(
        self, acct_id: int, at: datetime, setup: Setup, ev: rules.Evaluation | None
    ) -> int | None:
        """Record the signal once and apply the risk rules; returns the new order's id."""
        assert ev is not None
        base = {
            "entry_ref": setup.entry_ref,
            "stop": setup.stop,
            "target": setup.target,
            "max_hold_hours": setup.max_hold_hours,
            "cost_pct": setup.cost_pct,
            "evaluation_at": ev.at.isoformat(),
        }
        key = {"strategy": setup.strategy, "token_mint": setup.mint, "signal_bar": setup.signal_bar}
        async with self.factory() as s, s.begin():
            new = await s.scalar(
                insert(PaperEvent)
                .values(account_id=acct_id, at=at, kind="signal", data=base, **key)
                .on_conflict_do_nothing(
                    index_elements=["account_id", "strategy", "token_mint", "signal_bar"],
                    index_where=text("kind = 'signal'"),
                )
                .returning(PaperEvent.id)
            )
            if new is None:  # already decided on an earlier tick
                return None
            acct = await s.get_one(PaperAccount, acct_id)
            book = await self._positions(s, acct_id, "open", "pending")
            equity = acct.cash_usd + sum(mark_value(x) for x in book if x.status == "open")
            day_start = await self._day_start_equity(s, acct_id, at)
            sizing = admit(
                setup,
                ev.features.get("liquidity_usd"),
                equity,
                acct.cash_usd,
                equity if day_start is None else day_start,
                len(book),
                {x.token_mint for x in book},
                sum(x.planned_risk_usd for x in book),
                self.p.risk,
            )
            if isinstance(sizing, str):
                s.add(self._event(acct_id, at, "reject", reason=sizing, data=base, **key))
                return None
            pos = PaperPosition(
                account_id=acct_id,
                status="pending",
                strategy=setup.strategy,
                token_mint=setup.mint,
                strategy_version=STRATEGY_VERSION,
                paper_version=PAPER_VERSION,
                signal_at=at,
                signal_bar=setup.signal_bar,
                entry_ref=setup.entry_ref,
                stop=setup.stop,
                target=setup.target,
                max_hold_hours=setup.max_hold_hours,
                quoted_cost_pct=setup.cost_pct,
                size_usd=sizing.size_usd,
                planned_risk_usd=sizing.planned_risk_usd,
                sizing_binding=sizing.binding,
                exit_attempts=0,
            )
            s.add(pos)
            await s.flush()
            await s.execute(
                update(PaperEvent).where(PaperEvent.id == new).values(position_id=pos.id)
            )
            s.add(
                self._event(
                    acct_id, at, "order", position_id=pos.id, reason=sizing.binding,
                    data={
                        **base, "size_usd": sizing.size_usd, "equity_usd": equity,
                        "planned_risk_usd": sizing.planned_risk_usd,
                    },
                    **key,
                )
            )  # fmt: skip
            return pos.id

    async def _open(
        self, acct_id: int, pid: int, at: datetime, run_id: int, res: JobResult
    ) -> None:
        async with self.factory() as s:
            pos = await s.get_one(PaperPosition, pid)
            decimals = await self._decimals(s, pos.token_mint)
        fee = self.p.risk.fee_usd_per_swap
        if decimals is None:
            leg = Leg(error="decimals_unknown")
        else:
            leg = await quotes.buy(
                self.quoter, pos.token_mint, decimals, pos.size_usd - fee, self.pp.reference_usd
            )
        async with self.factory() as s, s.begin():
            pos = await s.get_one(PaperPosition, pid)
            raw_ids = await self._raw(s, leg, run_id, res)
            s.add(self._fill_row(pid, "buy", at, leg, raw_ids, pos.entry_ref))
            ev = {"position_id": pid, "strategy": pos.strategy, "token_mint": pos.token_mint}
            if leg.error is not None:
                pos.status, pos.cancel_reason = "cancelled", "swap_failed"
                s.add(
                    self._event(acct_id, at, "cancel", reason="swap_failed",
                                data={"error": leg.error}, **ev)
                )  # fmt: skip
                return
            assert leg.usd is not None and leg.quoted_at is not None
            pos.status, pos.entry_at = "open", leg.quoted_at
            pos.entry_price, pos.entry_cost_pct, pos.qty = leg.ref_price, leg.cost_pct, leg.qty
            pos.exit_by = leg.quoted_at + timedelta(hours=pos.max_hold_hours)
            pos.last_price, pos.last_price_at = leg.ref_price, leg.quoted_at
            await s.execute(
                update(PaperAccount)
                .where(PaperAccount.id == acct_id)
                .values(cash_usd=PaperAccount.cash_usd - (leg.usd + fee))
            )
            s.add(
                self._event(
                    acct_id, at, "fill", reason=None,
                    data={
                        "ref_price": leg.ref_price, "fill_price": leg.fill_price,
                        "cost_pct": leg.cost_pct, "qty": leg.qty, "usd": leg.usd,
                        "fee_usd": fee, "slip_vs_entry_ref_pct":
                            (leg.fill_price / pos.entry_ref - 1) * 100 if leg.fill_price else None,
                    },
                    **ev,
                )
            )  # fmt: skip
            res.rows_written += 1

    # ------------------------------------------------------------------ 4. equity

    async def _mark_equity(self, acct_id: int, at: datetime) -> None:
        async with self.factory() as s, s.begin():
            acct = await s.get_one(PaperAccount, acct_id)
            book = await self._positions(s, acct_id, "open")
            value = sum(mark_value(x) for x in book)
            await s.execute(
                insert(PaperEquity)
                .values(
                    account_id=acct_id,
                    at=at.replace(second=0),
                    cash_usd=acct.cash_usd,
                    positions_usd=value,
                    equity_usd=acct.cash_usd + value,
                    open_positions=len(book),
                )
                .on_conflict_do_nothing()
            )

    # ------------------------------------------------------------------ 5. model prices

    async def _model_prices(self, at: datetime) -> None:
        """The price the backtest fill model (v0: quoted cost, no slippage) would have
        used for each fill: the open of the first candle starting at or after the decision
        (entries and time exits) or the trigger level (stops and targets)."""
        give_up = at - timedelta(hours=self.pp.model_wait_hours)
        async with self.factory() as s, s.begin():
            rows = (
                await s.execute(
                    text(
                        """
                        SELECT f.id, f.side, f.decided_at, f.trigger_price, p.quoted_cost_pct,
                               p.exit_reason,
                               (SELECT c.open FROM candles_5m c JOIN tokens t
                                  ON t.mint = c.token_mint AND t.primary_pool = c.pool_address
                                WHERE c.token_mint = p.token_mint AND c.is_complete
                                  AND c.bucket >= date_trunc('minute', f.decided_at)
                                  AND c.bucket < f.decided_at + interval '30 minutes'
                                ORDER BY c.bucket LIMIT 1) AS next_open
                        FROM paper_fills f JOIN paper_positions p ON p.id = f.position_id
                        WHERE f.error IS NULL AND f.model_note IS NULL
                        """
                    )
                )
            ).mappings()
            for r in rows:
                sign = 1 if r["side"] == "buy" else -1
                cost = 1 + sign * r["quoted_cost_pct"] / 100
                reason = (r["exit_reason"] or "").removesuffix("_retry")
                if r["side"] == "sell" and reason in ("stop", "target"):
                    price, note = r["trigger_price"] * cost, "trigger"
                elif r["next_open"] is not None:
                    price, note = r["next_open"] * cost, "next_candle_open"
                elif r["decided_at"] < give_up:
                    price, note = None, "no_candle"
                else:
                    continue
                await s.execute(
                    update(PaperFill)
                    .where(PaperFill.id == r["id"])
                    .values(model_price=price, model_note=note)
                )


async def run_paper(factory: Factory, quoter: Quoter, run_id: int) -> JobResult:
    return await PaperTrader(factory, quoter).tick(run_id)
