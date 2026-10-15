"""Paper trading (paper-v2): exit triggers, quote fills and reconciliation checks."""

from datetime import UTC, datetime, timedelta

import pytest

from autolp.db.models import PaperEvent, PaperFill, PaperPosition
from autolp.paper import quotes
from autolp.paper.engine import exit_trigger
from autolp.paper.report import check_position, shortfall_pct
from tests.paper_fakes import FakeQuoter

T = datetime(2026, 10, 12, tzinfo=UTC)


def position(**kw: object) -> PaperPosition:
    base: dict[str, object] = {
        "id": 1, "account_id": 1, "status": "open", "strategy": "momentum", "token_mint": "A",
        "signal_at": T, "signal_bar": T, "entry_ref": 1.0, "stop": 0.93, "target": 1.15,
        "max_hold_hours": 4, "quoted_cost_pct": 0.5, "size_usd": 100.0,
        "planned_risk_usd": 8.0, "sizing_binding": "risk", "exit_by": T + timedelta(hours=4),
        "exit_attempts": 0, "pending_exit": None, "qty": 98.0,
    }  # fmt: skip
    base.update(kw)
    return PaperPosition(**base)


def test_exit_trigger_needs_a_price_except_for_time() -> None:
    p = position()
    assert exit_trigger(p, 1.0, T) is None
    assert exit_trigger(p, 0.93, T) == "stop"
    assert exit_trigger(p, 1.2, T) == "target"
    assert exit_trigger(p, None, T + timedelta(hours=3)) is None  # no price: no stop/target
    assert exit_trigger(p, None, T + timedelta(hours=4)) == "time"
    assert exit_trigger(position(pending_exit="stop"), 1.1, T) == "stop"  # retried


async def test_buy_and_sell_legs_measure_cost_against_the_reference() -> None:
    q = FakeQuoter({"A": 6}, {"A": 2.0})
    leg = await quotes.buy(q, "A", 6, 1000, 10)
    assert leg.error is None and leg.usd == 1000
    assert leg.ref_price == pytest.approx(2.0 / (1 - 0.0025 - 0.0001), rel=1e-6)
    assert leg.cost_pct == pytest.approx((0.9974 / 0.9875 - 1) * 100, rel=1e-3)
    held = leg.out_raw
    assert held is not None
    mark = await quotes.reference_sell(q, "A", 6, held, 10, 2.0)
    assert mark.ref_price == pytest.approx(2.0 * (1 - 0.0025 - 0.0001), rel=1e-4)
    out = await quotes.sell(q, "A", 6, held, mark.ref_price)
    assert out.in_raw == held and out.cost_pct == pytest.approx(0.98, abs=0.01)  # $987.50 sold
    q.fail.add("A")
    failed = await quotes.buy(q, "A", 6, 1000, 10)
    assert failed.error and "no route" in failed.error and failed.usd is None


def fill(side: str, **kw: object) -> PaperFill:
    base: dict[str, object] = {"position_id": 1, "side": side, "decided_at": T, "error": None}
    base.update(kw)
    return PaperFill(**base)


def test_check_position_reconciles_a_closed_trade() -> None:
    evs = [PaperEvent(kind=k) for k in ("signal", "order", "fill", "exit")]
    buy = fill("buy", usd=99.95, fee_usd=0.05, qty=98.0, out_amount_raw=98_000_000)
    sell = fill("sell", usd=110.0, fee_usd=0.05, qty=98.0, in_amount_raw=98_000_000)
    ok = position(status="closed", proceeds_usd=109.95, pnl_usd=9.95)
    assert check_position(ok, evs, [buy, sell]) == []
    bad = position(status="closed", proceeds_usd=110.0, pnl_usd=10.0, exit_attempts=1)
    assert set(check_position(bad, evs[:3], [buy, sell])) == {
        "no exit event",
        "proceeds differ from the exit fill",
        "P/L differs from the fills",
        "exit attempts differ from the failed exit fills",
    }
    assert check_position(position(status="cancelled"), evs[:2], []) == ["no cancel event"]


def test_shortfall_sign_is_worse_for_us() -> None:
    assert shortfall_pct(fill("buy", fill_price=1.02, model_price=1.0)) == pytest.approx(2)
    assert shortfall_pct(fill("sell", fill_price=0.98, model_price=1.0)) == pytest.approx(2)
    assert shortfall_pct(fill("sell", fill_price=0.98)) is None


def test_observed_cost_multiplier_is_a_ratio_of_sums() -> None:
    from autolp.paper.report import _ratio_of_sums

    # One fill with a near-zero estimate must not dominate: (0.4 + 0.05) / (0.4 + 0.01).
    assert _ratio_of_sums([(0.4, 0.4), (0.05, 0.01)]) == pytest.approx(0.45 / 0.41)
    assert _ratio_of_sums([]) is None
