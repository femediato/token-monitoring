"""Backtest bt-v1 against TimescaleDB: point-in-time universes, storage and API."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autolp.backtest import report as rep
from autolp.backtest.data import BtEvaluation, load
from autolp.backtest.runner import backtest
from autolp.db.models import Candle5m, EarlyAlert, ScoreEvaluation, Token, WatchlistEvent
from autolp.selection.params import MODEL_VERSION

Factory = async_sessionmaker[AsyncSession]
T0 = datetime(2026, 10, 12, 0, 0, tzinfo=UTC)
BREAKOUT = [1.0] * 24 + [1.05, 1.06, 1.1, 1.25, 1.25, 1.2] + [1.2] * 30


def features(mc: float, liq: float, vol: float, price: float, **kw: Any) -> dict[str, Any]:
    return {
        "market_cap": mc,
        "liquidity_usd": liq,
        "volume_24h": vol,
        "price_usd": price,
        "relvol_ratio": 2.0,
        "risk_flags": [],
        "organic_label": "high",
        "sell_cost_pct": 0.5,
        "quote_age_minutes": 5.0,
        "buy_share_1h": 0.62,
        "net_buy_liq_1h": 0.05,
        **kw,
    }


async def seed(
    factory: Factory, mint: str, closes: list[float], *, eligible: bool, critical: bool, mc: float
) -> None:
    async with factory() as s, s.begin():
        s.add(Token(mint=mint, symbol=mint, discovery_source="test", primary_pool=f"P{mint}"))
        prev = closes[0]
        for i, c in enumerate(closes):
            s.add(
                Candle5m(
                    pool_address=f"P{mint}",
                    source="geckoterminal",
                    bucket=T0 + timedelta(minutes=5 * i),
                    token_mint=mint,
                    open=prev,
                    high=max(prev, c) + 0.001,
                    low=min(prev, c) - 0.001,
                    close=c,
                    volume_usd=1000,
                    is_complete=True,
                    fetched_at=T0 + timedelta(minutes=5 * i + 5),
                )
            )
            prev = c
        for k in range(len(closes) // 3 + 1):
            at = T0 + timedelta(minutes=15 * k + 4)
            price = closes[min(len(closes) - 1, 3 * k)]
            s.add(
                ScoreEvaluation(
                    token_mint=mint,
                    evaluated_at=at,
                    model_version=MODEL_VERSION,
                    hourly=k % 4 == 0,
                    eligible=eligible,
                    critical=critical,
                    reasons=[],
                    features=features(mc, 500_000, 2e6, price),
                    buying=70.0 if eligible else None,
                    total=80.0 if eligible else None,
                )
            )


async def seeded(factory: Factory) -> None:
    await seed(factory, "SEL", BREAKOUT, eligible=True, critical=False, mc=5e6)
    await seed(factory, "BIG", [1.0] * 60, eligible=False, critical=False, mc=9e8)
    await seed(factory, "BAD", [1.0] * 60, eligible=False, critical=True, mc=9e9)
    async with factory() as s, s.begin():
        s.add(
            WatchlistEvent(
                at=T0, token_mint="SEL", action="add", group="emerging", reason="test",
                score=80, model_version=MODEL_VERSION,
            )
        )  # fmt: skip
        s.add(
            EarlyAlert(
                token_mint="SEL", at=T0 + timedelta(hours=2, minutes=19), model_version="early-v1",
                score=71.4, state="emerging", signals={},
            )
        )  # fmt: skip


async def test_universes_are_point_in_time_and_report_reproducible(factory: Factory) -> None:
    await seeded(factory)
    start, end = T0 + timedelta(hours=1), T0 + timedelta(hours=5)
    async with factory() as s:
        data = await load(s, start, end)
    tl = data.timelines
    at = T0 + timedelta(hours=2)
    assert tl["selection"].at(at) == {"SEL"} and tl["fixed"].at(at) == {"SEL"}
    assert tl["mcap"].at(at) == {"BIG", "SEL"}  # BAD is critical: never a candidate
    assert tl["random"].at(at) == {"BIG"}  # the only non-selected candidate
    assert tl["watchlist"].at(at) == {"SEL"}
    assert data.alerts == {"SEL": [T0 + timedelta(hours=2, minutes=19)]}
    big = data.history.evals["BIG"][0]
    assert isinstance(big, BtEvaluation) and big.buying is not None  # from its features

    async with factory() as s:
        again = await load(s, start, end)
    assert again.input_digest == data.input_digest

    r1, r2 = backtest(data, sealed=False), backtest(again, sealed=False)
    assert r1["result_digest"] == r2["result_digest"]
    sel = next(r for r in r1["runs"] if (r["universe"], r["scenario"]) == ("selection", "base"))
    assert [t["mint"] for t in sel["trades"]] == ["SEL"]
    assert sel["candle_coverage_pct"] == 100
    early = next(r for r in r1["runs"] if (r["universe"], r["scenario"]) == ("early", "base"))
    assert early["decision_digest"] != sel["decision_digest"]  # the alert adds a setup
    fwd = r1["forward"]["1"]
    assert fwd["selection"]["all"]["n"] > 0 and fwd["mcap"]["all"]["uplift_pct"] is not None

    async with factory() as s, s.begin():
        run_id = await rep.store(s, r1)

    from autolp.api.main import app
    from autolp.config import get_settings

    get_settings.cache_clear()
    with TestClient(app) as c:
        lst = c.get("/api/backtests").json()
        assert [i["id"] for i in lst["items"]] == [run_id]
        assert lst["items"][0]["selection"]["trades"] == 1
        detail = c.get(f"/api/backtests/{run_id}").json()
        assert detail["result_digest"] == r1["result_digest"]
        assert detail["symbols"] == {"SEL": "SEL"}
        assert c.get("/api/backtests/999999").status_code == 404
