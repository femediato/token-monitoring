"""Explorer API against seeded rows in the test database."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autolp.api.main import app
from autolp.config import get_settings
from autolp.db.base import utcnow
from autolp.db.models import (
    Candle5m,
    HolderSnapshot,
    MarketSnapshot,
    MarketWindowStats,
    SwapQuote,
    Token,
    TokenSecurity,
    TrackingEvent,
)

Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def seeded(factory: Factory) -> None:
    now = utcnow()
    async with factory() as s, s.begin():
        for mint, sym, status, liq in [
            ("MintA", "AAA", "active", 500_000.0),
            ("MintB", "BBB", "active", 200_000.0),
            ("MintX", "XXX", "excluded", 900_000.0),
        ]:
            s.add(
                Token(
                    mint=mint,
                    symbol=sym,
                    name=f"{sym} coin",
                    discovery_source="test",
                    tracking_status=status,
                    primary_pool=f"Pool{sym}",
                    pair_created_at=now - timedelta(days=10),
                )
            )
            s.add(TrackingEvent(token_mint=mint, at=now, to_status=status, reason="test"))
            for age in (0, 5):
                t = now - timedelta(minutes=age)
                s.add(
                    MarketSnapshot(
                        token_mint=mint,
                        source="jupiter",
                        observed_at=t,
                        price_usd=1.0 + age,
                        liquidity_usd=liq,
                        market_cap=liq * 10,
                    )
                )
                s.add(
                    MarketWindowStats(
                        token_mint=mint,
                        source="jupiter",
                        observed_at=t,
                        window="24h",
                        volume_usd=1000.0,
                        buy_volume_usd=600.0,
                        sell_volume_usd=400.0,
                        num_buys=30,
                        num_sells=20,
                        num_traders=25,
                    )
                )
            s.add(
                Candle5m(
                    pool_address=f"Pool{sym}",
                    source="geckoterminal",
                    bucket=now - timedelta(minutes=10),
                    token_mint=mint,
                    open=1,
                    high=2,
                    low=0.5,
                    close=1.5,
                    volume_usd=10,
                    is_complete=True,
                    fetched_at=now,
                )
            )
        s.add(
            TokenSecurity(
                token_mint="MintA",
                checked_at=now,
                status="risky",
                risk_flags=["mint_authority_active"],
            )
        )
        s.add(
            HolderSnapshot(
                token_mint="MintA",
                observed_at=now,
                method_version="v1",
                top10_pct_adjusted=33.3,
                accounts=[{"account": "acc", "amount_raw": 10**30, "excluded": None}],
            )
        )
        s.add(
            SwapQuote(
                token_mint="MintA", quoted_at=now, side="sell", notional_usd=1000, cost_pct=0.8
            )
        )


@pytest.fixture
def client(engine: object, seeded: None) -> Iterator[TestClient]:
    get_settings.cache_clear()
    with TestClient(app) as c:
        yield c


def test_list_defaults_to_active_sorted_by_liquidity(client: TestClient) -> None:
    body = client.get("/api/tokens").json()
    assert body["total"] == 2
    a, b = body["items"]
    assert (a["symbol"], b["symbol"]) == ("AAA", "BBB")
    assert a["price_usd"] == 1.0  # latest snapshot, not the older one
    assert a["buy_share_24h"] == pytest.approx(60.0)
    assert a["trades_24h"] == 50 and a["traders_24h"] == 25
    assert a["security_status"] == "risky" and a["top10_pct_adjusted"] == 33.3
    assert a["sell_cost_pct"] == 0.8
    assert b["security_status"] is None  # never checked -> null, not "ok"
    assert 9.9 < a["age_days"] < 10.1


def test_list_filters_and_sorting(client: TestClient) -> None:
    assert client.get("/api/tokens?status=all").json()["total"] == 3
    assert client.get("/api/tokens?status=excluded").json()["items"][0]["symbol"] == "XXX"
    assert client.get("/api/tokens?q=bbb").json()["items"][0]["mint"] == "MintB"
    assert client.get("/api/tokens?q=MintA").json()["total"] == 1
    assert client.get("/api/tokens?security=unchecked").json()["total"] == 1
    assert client.get("/api/tokens?min_liquidity=300000").json()["total"] == 1
    asc = client.get("/api/tokens?sort=liquidity_usd&order=asc").json()["items"]
    assert [t["symbol"] for t in asc] == ["BBB", "AAA"]
    assert client.get("/api/tokens?sort=drop_table").status_code == 400


def test_detail(client: TestClient) -> None:
    d = client.get("/api/tokens/MintA").json()
    assert d["token"]["symbol"] == "AAA"
    assert d["security"]["risk_flags"] == ["mint_authority_active"]
    assert d["holders"]["accounts"][0]["amount_raw"] == str(10**30)  # no JS precision loss
    assert d["tracking_events"][0]["reason"] == "test"
    assert [w["window"] for w in d["windows"]] == ["24h"]
    assert client.get("/api/tokens/Nope").status_code == 404


def test_series_candles_events_overview(client: TestClient) -> None:
    series = client.get("/api/tokens/MintA/series?hours=1").json()
    assert series["bucket_seconds"] == 300 and len(series["points"]) == 2
    assert series["points"][-1]["price_usd"] == 1.0
    candles = client.get("/api/tokens/MintA/candles").json()
    assert candles["pool"] == "PoolAAA" and candles["candles"][0]["close"] == 1.5
    events = client.get("/api/events?to_status=excluded").json()
    assert [e["token_mint"] for e in events] == ["MintX"]
    ov = client.get("/api/overview").json()
    assert {(u["tracking_status"], u["n"]) for u in ov["universe"]} >= {("active", 2)}
    assert {j["job"] for j in ov["jobs"]} >= {"discovery", "snapshots_active"}
