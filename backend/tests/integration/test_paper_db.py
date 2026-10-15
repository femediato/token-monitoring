"""Paper trading engine against TimescaleDB with a fake quote provider."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autolp.db.models import PaperAccount, PaperEvent, PaperFill, PaperPosition, Token
from autolp.paper import report as rep
from autolp.paper.engine import PaperTrader
from tests.integration.test_strategies_db import BREAKOUT, T0, seed
from tests.paper_fakes import FakeQuoter

Factory = async_sessionmaker[AsyncSession]
SIGNAL = T0 + timedelta(hours=2, minutes=5, seconds=30)  # breakout candle (02:00) closed


async def setup(factory: Factory, *mints: str) -> tuple[PaperTrader, FakeQuoter]:
    for m in mints:
        await seed(factory, m, BREAKOUT)
    async with factory() as s, s.begin():
        await s.execute(update(Token).values(decimals=6))
    q = FakeQuoter({m: 6 for m in mints}, {m: 1.06 for m in mints})
    return PaperTrader(factory, q), q


async def tick(trader: PaperTrader, q: FakeQuoter, at: object) -> None:
    q.now = at  # type: ignore[assignment]
    await trader.tick(1, at)  # type: ignore[arg-type]


async def positions(factory: Factory) -> list[PaperPosition]:
    async with factory() as s:
        return list(await s.scalars(select(PaperPosition).order_by(PaperPosition.id)))


async def test_signal_to_exit_is_recorded_and_reconciles(factory: Factory) -> None:
    trader, q = await setup(factory, "A")
    await tick(trader, q, SIGNAL)
    [pos] = await positions(factory)
    assert pos.status == "open" and pos.strategy == "momentum" and pos.entry_ref == 1.05
    assert pos.entry_cost_pct is not None and 0 < pos.entry_cost_pct < 2
    # Restart: a new engine on the same minute and the next one decides nothing again.
    trader2 = PaperTrader(factory, q)
    await tick(trader2, q, SIGNAL)
    await tick(trader2, q, SIGNAL + timedelta(minutes=1))
    assert len(await positions(factory)) == 1
    q.prices["A"] = 1.30  # above the +15% target
    await tick(trader2, q, SIGNAL + timedelta(minutes=2))
    [pos] = await positions(factory)
    assert pos.status == "closed" and pos.exit_reason == "target"
    assert pos.pnl_usd is not None and pos.pnl_usd > 0
    async with factory() as s:
        kinds = [e.kind for e in await s.scalars(select(PaperEvent).order_by(PaperEvent.id))]
        acct = await rep.account(s)
        assert acct is not None
        r = await rep.summary(s, acct, SIGNAL + timedelta(minutes=3))
        n_equity = await s.scalar(select(func.count()).select_from(PaperAccount))
    assert kinds == ["signal", "order", "fill", "exit"] and n_equity == 1
    assert r["reconciliation"]["ok"], r["reconciliation"]
    assert r["metrics"]["trades"] == 1 and r["execution"]["buy"]["attempts"] == 1
    assert abs(r["account"]["cash_usd"] - (10_000 + pos.pnl_usd)) < 1e-6
    # The backtest-model price of the entry: open of the 02:05 candle plus the quoted cost.
    async with factory() as s:
        buy = await s.scalar(select(PaperFill).where(PaperFill.side == "buy"))
    assert buy is not None and buy.model_note == "next_candle_open"
    assert abs((buy.model_price or 0) - 1.05 * 1.005) < 1e-9


async def test_failed_swaps_cancel_entries_and_retry_exits(factory: Factory) -> None:
    trader, q = await setup(factory, "A", "B")
    q.fail.add("B")
    await tick(trader, q, SIGNAL)
    a, b = await positions(factory)
    assert (a.status, b.status, b.cancel_reason) == ("open", "cancelled", "swap_failed")
    q.prices["A"] = 0.9  # below the stop, but the exit swap fails
    q.fail_full = {"A"}
    await tick(trader, q, SIGNAL + timedelta(minutes=1))
    a = (await positions(factory))[0]
    assert a.status == "open" and a.pending_exit == "stop" and a.exit_attempts == 1
    q.fail_full = set()
    q.prices["A"] = 0.95  # retried at whatever the price is now
    await tick(trader, q, SIGNAL + timedelta(minutes=2))
    a = (await positions(factory))[0]
    assert a.status == "closed" and a.exit_reason == "stop_retry" and a.pnl_usd < 0  # type: ignore[operator]
    async with factory() as s:
        acct = await rep.account(s)
        assert acct is not None
        r = await rep.summary(s, acct, SIGNAL + timedelta(minutes=3))
    assert r["reconciliation"]["ok"], r["reconciliation"]
    ex = r["execution"]
    assert (ex["buy"]["failed"], ex["sell"]["failed"], ex["stops"]) == (1, 1, 1)
    assert ex["stop_slippage_pct"] > 0


async def test_interrupted_orders_are_cancelled_and_api_serves_the_audit_trail(
    factory: Factory,
) -> None:
    trader, q = await setup(factory, "A")
    acct_id = await trader.account_id()
    async with factory() as s, s.begin():
        s.add(
            PaperPosition(
                account_id=acct_id, status="pending", strategy="range", token_mint="A",
                strategy_version="strat-v1", paper_version="paper-v1", signal_at=T0,
                signal_bar=T0, entry_ref=1, stop=0.9, target=1.2, max_hold_hours=12,
                quoted_cost_pct=0.5, size_usd=100, planned_risk_usd=10, sizing_binding="risk",
                exit_attempts=0,
            )
        )  # fmt: skip
    await tick(trader, q, SIGNAL)
    old, new = await positions(factory)
    assert (old.status, old.cancel_reason) == ("cancelled", "interrupted")
    assert new.status == "open"

    from autolp.api.main import app
    from autolp.config import get_settings

    get_settings.cache_clear()
    with TestClient(app) as c:
        body = c.get("/api/paper").json()
        assert body["account"]["name"] == "main" and len(body["open"]) == 1
        assert body["symbols"]["A"] == "A" and body["equity"]
        detail = c.get(f"/api/paper/positions/{new.id}").json()
        assert [e["kind"] for e in detail["events"]] == ["signal", "order", "fill"]
        assert detail["issues"] == [] and detail["fills"][0]["raw_ids"]
        # The interrupted order has no signal/order events: reconciliation reports it.
        assert c.get(f"/api/paper/positions/{old.id}").json()["issues"]
        assert c.get("/api/paper/positions/999999").status_code == 404
        assert c.get("/api/paper/events?kind=fill").json()["items"][0]["kind"] == "fill"


async def test_a_research_account_trades_independently_with_a_trailing_stop(
    factory: Factory,
) -> None:
    from autolp.paper.accounts import AccountSpec

    trader, q = await setup(factory, "A")
    trail = AccountSpec(
        "trail-test",
        "test",
        {
            "momentum.exit.take_profit_pct": 100,  # target out of reach
            "momentum.manage.breakeven_at_r": 1,
        },
    )
    research = PaperTrader(factory, q, trail.params(), trail.paper())
    for t in (trader, research):
        await tick(t, q, SIGNAL)
    main_pos, res_pos = await positions(factory)
    assert main_pos.account_id != res_pos.account_id
    assert res_pos.initial_stop == res_pos.stop and res_pos.high_price is not None
    q.prices["A"] = 1.06 * 1.10  # 1.4 R up: the research stop moves to breakeven
    for t in (trader, research):
        await tick(t, q, SIGNAL + timedelta(minutes=1))
    main_pos, res_pos = await positions(factory)
    assert main_pos.stop == main_pos.initial_stop  # main never moves its stop
    assert res_pos.stop > res_pos.entry_price  # type: ignore[operator]
    q.prices["A"] = 1.04  # back below the entry: research exits on the raised stop
    for t in (trader, research):
        await tick(t, q, SIGNAL + timedelta(minutes=2))
    main_pos, res_pos = await positions(factory)
    assert main_pos.status == "open"
    assert res_pos.status == "closed" and res_pos.exit_reason == "trail"
    async with factory() as s:
        moved = await s.scalar(
            select(func.count()).select_from(PaperEvent).where(PaperEvent.kind == "stop_moved")
        )
        assert moved == 1
        assert await rep.account(s, "trail-test") is not None


async def test_entry_drift_guard_cancels_without_moving_cash(factory: Factory) -> None:
    from autolp.paper.accounts import AccountSpec

    _, q = await setup(factory, "A")
    q.prices["A"] = 1.05 * 1.05  # 0.7 R above the signal close at the quote
    spec = AccountSpec("drift-test", "test", {"risk.max_entry_drift_r": 0.25})
    await tick(PaperTrader(factory, q, spec.params(), spec.paper()), q, SIGNAL)
    [pos] = await positions(factory)
    assert pos.status == "cancelled" and pos.cancel_reason == "entry_drift"
    async with factory() as s:
        acct = await rep.account(s, "drift-test")
        assert acct is not None and acct.cash_usd == acct.starting_equity_usd
