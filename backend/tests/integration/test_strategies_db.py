"""Strategy signals and decision replay against TimescaleDB."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autolp.collector.jobs import Collector
from autolp.config import Settings
from autolp.db.models import Candle5m, ScoreEvaluation, StrategySignal, Token
from autolp.selection.params import MODEL_VERSION
from autolp.strategies.live import run_strategy_signals
from autolp.strategies.replay import replay

Factory = async_sessionmaker[AsyncSession]
T0 = datetime(2026, 10, 12, 0, 0, tzinfo=UTC)


def features(**kw: Any) -> dict[str, Any]:
    return {
        "relvol_ratio": 2.0,
        "risk_flags": [],
        "organic_label": "high",
        "sell_cost_pct": 0.5,
        "quote_age_minutes": 5.0,
        "liquidity_usd": 500_000.0,
        "buy_share_1h": 0.6,
        **kw,
    }


async def seed(factory: Factory, mint: str, closes: list[float], *, eligible: bool = True) -> None:
    """Candles from T0 (one per 5 minutes) and an evaluation every 15 minutes."""
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
            s.add(
                ScoreEvaluation(
                    token_mint=mint,
                    evaluated_at=T0 + timedelta(minutes=15 * k + 4),
                    model_version=MODEL_VERSION,
                    hourly=k % 4 == 0,
                    eligible=eligible,
                    critical=False,
                    reasons=[],
                    features=features(),
                    buying=70.0,
                )
            )


BREAKOUT = [1.0] * 24 + [1.05, 1.06, 1.1, 1.25, 1.25, 1.2]  # breakout candle at 02:00


async def test_live_job_stores_every_check(factory: Factory) -> None:
    await seed(factory, "A", BREAKOUT)
    await seed(factory, "X", BREAKOUT, eligible=False)  # outside the universe
    at = T0 + timedelta(hours=2, minutes=5, seconds=30)  # breakout candle just closed
    result = await run_strategy_signals(factory, 1, at)
    assert result.rows_written == 2  # momentum and pullback; range is off (strat-v1.1)
    async with factory() as s:
        rows = {r.strategy: r for r in await s.scalars(select(StrategySignal))}
    m = rows["momentum"]
    assert m.token_mint == "A" and m.setup and m.failed == []
    assert m.entry_ref == 1.05 and m.target is not None and m.stop is not None
    assert m.last_candle_at == T0 + timedelta(hours=2)
    # Too little history for pullback: inputs missing, recorded as None.
    assert not rows["pullback"].setup and rows["pullback"].checks["uptrend"] is None
    assert "range" not in rows


async def test_replay_is_deterministic_and_point_in_time(factory: Factory) -> None:
    await seed(factory, "A", BREAKOUT)
    await seed(factory, "B", [1.0] * 30)
    start, end = T0 + timedelta(hours=1), T0 + timedelta(hours=2, minutes=30)
    a = await replay(factory, start, end)
    b = await replay(factory, start, end)
    assert a.digest() == b.digest() and a.decisions
    acts = [(d.action, d.mint, d.reason) for d in a.decisions]
    assert acts[:3] == [("signal", "A", None), ("order", "A", "risk"), ("fill", "A", None)]
    assert ("exit", "A", "target") in acts
    # Stopping before the breakout candle closed: no decision can see it.
    early = await replay(factory, start, T0 + timedelta(hours=2, minutes=4))
    assert early.decisions == []


async def test_candles_prioritise_eligible_tokens(settings: Settings, factory: Factory) -> None:
    recent = datetime.now(UTC) - timedelta(minutes=10)
    async with factory() as s, s.begin():
        for mint, eligible in (("ELIG", True), ("DEEP", False)):
            s.add(
                ScoreEvaluation(
                    token_mint=mint,
                    evaluated_at=recent,
                    model_version=MODEL_VERSION,
                    hourly=False,
                    eligible=eligible,
                    critical=False,
                    reasons=[],
                    features={},
                )
            )
    col = Collector.from_settings(settings, factory)
    ordered = await col._eligible_first([("DEEP", "P1"), ("ELIG", "P2"), ("OTHER", "P3")])
    assert [m for m, _ in ordered] == ["ELIG", "DEEP", "OTHER"]


async def test_strategies_api(factory: Factory, engine: object) -> None:
    from autolp.api.main import app
    from autolp.config import get_settings

    await seed(factory, "A", BREAKOUT)
    await run_strategy_signals(factory, 1, T0 + timedelta(hours=2, minutes=5, seconds=30))
    get_settings.cache_clear()
    with TestClient(app) as c:
        p = c.get("/api/strategies").json()
        assert p["version"] == "strat-v1.1" and p["params"]["risk"]["max_positions"] == 3
        sig = c.get("/api/strategies/signals").json()
        assert {(i["strategy"], i["setup"]) for i in sig["items"]} == {
            ("momentum", True),
            ("pullback", False),
        }
        st = c.get("/api/strategies/setups?days=90").json()
        assert [i["strategy"] for i in st["items"]] == ["momentum"]
        assert {t["strategy"]: t["setups"] for t in st["totals"]}["momentum"] == 1
