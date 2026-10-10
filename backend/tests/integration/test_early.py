"""Early detection end to end: alerts, cooldown, controls, entry quotes and outcomes."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autolp.api.main import app
from autolp.collector.jobs import Collector
from autolp.config import (
    DexScreenerSettings,
    GeckoTerminalSettings,
    HeliusSettings,
    JupiterSettings,
    Settings,
    get_settings,
)
from autolp.db.base import utcnow
from autolp.db.models import AlertObservation, EarlyAlert, EarlyScore, MarketSnapshot, Token
from autolp.selection.engine import _early_detection

Factory = async_sessionmaker[AsyncSession]
AT = datetime(2026, 10, 11, 6, 4, tzinfo=UTC)

HOT = {
    "volume_1h": 30_000.0,
    "volume_24h": 253_000.0,
    "trader_growth_ratio": 1.4,
    "buy_share_1h": 0.6,
    "depth_ratio_6h": 1.15,
    "ret_1h_pct": 3.0,
    "ret_4h_pct": 5.0,
    "ema20_gap": 0.01,
    "organic_label": "high",
    "price_usd": 2.0,
    "market_cap": 10e6,
    "liquidity_usd": 1e6,
}
COLD = {
    **HOT,
    "volume_1h": 5_000.0,
    "trader_growth_ratio": 0.9,
    "buy_share_1h": 0.4,
    "depth_ratio_6h": 0.95,
    "ema20_gap": -0.01,
}


def row(features: dict[str, Any], eligible: bool = True) -> dict[str, Any]:
    return {"features": features, "eligible": eligible}


async def test_alert_controls_and_cooldown(factory: Factory) -> None:
    rows = {
        "HOT": row(HOT),
        "HOT_INELIGIBLE": row(HOT, eligible=False),
        "C_NEAR": row({**COLD, "market_cap": 11e6}),
        "C_FAR": row({**COLD, "market_cap": 900e6, "liquidity_usd": 50e6}),
        "C_MID": row({**COLD, "market_cap": 20e6}),
        "C_X": row({**COLD, "market_cap": 40e6}),
    }
    async with factory() as s, s.begin():
        n = await _early_detection(s, rows, AT)
    assert n == 1  # ineligible tokens are scored but never alerted
    async with factory() as s:
        alert = (await s.scalars(select(EarlyAlert))).one()
        obs = (
            await s.scalars(select(AlertObservation).order_by(AlertObservation.match_distance))
        ).all()
        scores = (await s.scalars(select(EarlyScore))).all()
    assert alert.token_mint == "HOT" and alert.state == "high_priority" and alert.score == 100
    assert alert.pre_return_1h_pct == 3.0 and alert.price_usd == 2.0
    controls = [o.token_mint for o in obs if o.role == "control"]
    assert controls == ["C_NEAR", "C_MID", "C_X"]  # nearest 3 by market cap + liquidity
    assert len(scores) == 6
    async with factory() as s, s.begin():
        assert await _early_detection(s, rows, AT + timedelta(hours=2)) == 0  # cooldown
    async with factory() as s, s.begin():
        assert await _early_detection(s, rows, AT + timedelta(hours=7)) == 1


@pytest.fixture
def collector(settings: Settings, factory: Factory) -> Collector:
    fast: dict[str, Any] = {"requests_per_second": 1000, "max_retries": 1}
    s = settings.model_copy(
        update={
            "jupiter": JupiterSettings(api_key="k", **fast),
            "dexscreener": DexScreenerSettings(**fast),
            "geckoterminal": GeckoTerminalSettings(**fast),
            "helius": HeliusSettings(api_key="k", **fast),
        }
    )
    return Collector.from_settings(s, factory)


@respx.mock
async def test_entry_quote_and_missed_entry(collector: Collector, factory: Factory) -> None:
    now = utcnow()
    async with factory() as s, s.begin():
        s.add(
            EarlyAlert(
                token_mint="NEW",
                at=now,
                model_version="early-v1",
                score=71,
                state="emerging",
                signals={},
            )
        )
        s.add(
            EarlyAlert(
                token_mint="OLD",
                at=now - timedelta(hours=1),
                model_version="early-v1",
                score=71,
                state="emerging",
                signals={},
            )
        )

    def quote(req: httpx.Request) -> httpx.Response:
        amount = int(req.url.params["amount"])
        rate = 0.5 if amount < 100 * 10**6 else 0.5 * 0.99  # 1% fewer tokens per USDC at size
        return httpx.Response(
            200,
            json={
                "inAmount": str(amount),
                "outAmount": str(int(amount * rate)),
                "priceImpactPct": "0",
                "routePlan": [],
            },
        )

    route = respx.get("https://api.jup.ag/swap/v1/quote").mock(side_effect=quote)
    await collector.alert_entry_quotes(1)
    async with factory() as s:
        a = {x.token_mint: x for x in await s.scalars(select(EarlyAlert))}
    assert a["NEW"].entry_cost_pct == pytest.approx(1.0, abs=0.001) and a["NEW"].entry_quoted_at
    assert route.calls[0].request.url.params["inputMint"].startswith("EPjF")  # buying with USDC
    assert a["OLD"].entry_error == "entry_quote_missed" and a["OLD"].entry_cost_pct is None


async def test_outcomes_fill_horizons_and_record_gaps(
    collector: Collector, factory: Factory
) -> None:
    start = utcnow() - timedelta(hours=25)
    async with factory() as s, s.begin():
        s.add(
            AlertObservation(
                alert_id=1, role="alert", token_mint="T", start_at=start, start_price=1.0
            )
        )
        for minutes, price in [
            (60, 1.10),
            (240, 0.90),
            (600, 1.50),
            (24 * 60, 1.20),
        ]:  # no 12h sample
            s.add(
                MarketSnapshot(
                    token_mint="T",
                    source="jupiter",
                    observed_at=start + timedelta(minutes=minutes),
                    price_usd=price,
                )
            )
    await collector.alert_outcomes(1)
    async with factory() as s:
        o = (await s.scalars(select(AlertObservation))).one()
    assert o.ret_1h_pct == pytest.approx(10.0) and o.ret_4h_pct == pytest.approx(-10.0)
    assert o.ret_12h_pct is None and o.missing == ["12h"]  # 10h sample is outside ±10 minutes
    assert o.ret_24h_pct == pytest.approx(20.0)
    assert o.mfe_24h_pct == pytest.approx(50.0) and o.mae_24h_pct == pytest.approx(-10.0)
    assert o.completed_at is not None


async def test_outcomes_wait_until_horizon_has_passed(
    collector: Collector, factory: Factory
) -> None:
    async with factory() as s, s.begin():
        s.add(
            AlertObservation(
                alert_id=1,
                role="alert",
                token_mint="T",
                start_at=utcnow() - timedelta(minutes=30),
                start_price=1.0,
            )
        )
    await collector.alert_outcomes(1)
    async with factory() as s:
        o = (await s.scalars(select(AlertObservation))).one()
    assert o.ret_1h_pct is None and o.missing is None and o.completed_at is None


async def test_early_api(factory: Factory, engine: object) -> None:
    rows = {"HOT": row(HOT), "C1": row(COLD)}
    async with factory() as s, s.begin():
        for m in rows:
            s.add(Token(mint=m, symbol=m, discovery_source="test"))
    async with factory() as s, s.begin():
        await _early_detection(s, rows, AT)
        await s.execute(update(AlertObservation).values(ret_1h_pct=2.0))
    get_settings.cache_clear()
    with TestClient(app) as c:
        board = c.get("/api/early/board?min_score=0").json()
        assert [i["mint"] for i in board["items"]][0] == "HOT" and board["states"][
            "high_priority"
        ] == 1
        alerts = c.get("/api/early/alerts?days=180").json()
        assert alerts[0]["mint"] == "HOT" and alerts[0]["controls"] == 1
        rep = c.get("/api/early/report?days=365").json()
        assert rep["alerts"] == 1 and rep["horizons"]["ret_1h_pct"]["uplift"]["n"] == 1
