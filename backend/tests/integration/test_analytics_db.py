"""Dashboard analytics API on a paper account driven by the paper engine."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.integration.test_paper_db import SIGNAL, setup, tick

Factory = async_sessionmaker[AsyncSession]


async def test_signals_chart_and_performance(factory: Factory) -> None:
    trader, q = await setup(factory, "A", "B")
    # One position at a time: B's signal (same candle, considered after A's) is rejected.
    trader.p = replace(trader.p, risk=replace(trader.p.risk, max_positions=1))
    await tick(trader, q, SIGNAL)
    q.prices["A"] = 1.30  # above A's target
    await tick(trader, q, SIGNAL + timedelta(minutes=1))

    from autolp.api.main import app
    from autolp.config import get_settings

    get_settings.cache_clear()
    now = SIGNAL + timedelta(minutes=5)
    with patch("autolp.api.analytics.utcnow", return_value=now), TestClient(app) as c:
        sig = c.get("/api/analytics/signals?days=30").json()
        by_mint = {s["mint"]: s for s in sig["items"]}
        assert by_mint["A"]["decision"] == "taken" and by_mint["A"]["exit_reason"] == "target"
        assert by_mint["A"]["paper_return_pct"] > 0
        assert by_mint["B"]["decision"] == "rejected"
        assert by_mint["B"]["reject_reason"] == "max_positions"
        # The seeded candles end with the signal candle: nothing after the decision yet.
        assert by_mint["A"]["outcome"]["status"] == "waiting"
        assert sig["accuracy"]["all"]["all"]["signals"] == 2
        assert sig["accuracy"]["momentum"]["rejected"]["signals"] == 1

        r = c.get("/api/analytics/chart/A", params={"end": now.isoformat(), "hours": 6})
        assert r.status_code == 200, r.text
        chart = r.json()
        assert chart["symbol"] == "A" and chart["candles"]
        assert [s["mint"] for s in chart["signals"]] == ["A"]
        assert c.get("/api/analytics/chart/nope").status_code == 404
        assert c.get("/api/analytics/chart/A?hours=1000").status_code == 422

        perf = c.get("/api/analytics/performance").json()
        assert perf["by_strategy"]["momentum"]["trades"] == 1
        assert perf["by_strategy"]["all"]["costs"]["total_usd"] > 0
        assert perf["daily"][0]["trades"] == 1 and perf["equity"]

        paper = c.get("/api/paper").json()
        assert paper["exposure"]["positions"] == 0 and paper["exposure"]["max_positions"] == 3
