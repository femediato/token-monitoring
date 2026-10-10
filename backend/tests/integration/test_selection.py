"""Scoring engine end to end against TimescaleDB, plus paired quotes and holder priority."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autolp.collector.jobs import Collector
from autolp.config import (
    DexScreenerSettings,
    GeckoTerminalSettings,
    HeliusSettings,
    JupiterSettings,
    Settings,
)
from autolp.db.models import (
    HolderSnapshot,
    MarketSnapshot,
    MarketWindowStats,
    Pool,
    ScoreEvaluation,
    SwapQuote,
    Token,
    TokenSecurity,
    WatchlistEvent,
    WatchlistMember,
)
from autolp.selection.engine import run_scoring

Factory = async_sessionmaker[AsyncSession]


async def seed_token(
    factory: Factory,
    mint: str,
    at: datetime,
    *,
    flags: list[str] | None = None,
    top10: float = 20.0,
    rising: bool = True,
    hours: int = 5,
    strong_at: tuple[datetime, ...] = (),
) -> None:
    """A token with `hours` of 5-minute history ending at `at`, safety data and a quote.
    The 1h activity spikes at `at` and at every time in `strong_at`."""
    n = hours * 12 + 1
    spikes = {at, *strong_at}
    async with factory() as s, s.begin():
        s.add(
            Token(
                mint=mint,
                symbol=mint,
                discovery_source="test",
                decimals=6,
                pair_created_at=at - timedelta(days=60),
                primary_pool=f"P{mint}",
            )
        )
        s.add(Pool(address=f"P{mint}", token_mint=mint, pair_created_at=at - timedelta(days=30)))
        for i in range(n):
            t = at - timedelta(minutes=5 * (n - 1 - i))
            price = 1.0 + (0.004 * i if rising else 0)
            spike = t in spikes
            s.add(
                MarketSnapshot(
                    token_mint=mint,
                    source="jupiter",
                    observed_at=t,
                    price_usd=price,
                    market_cap=80e6,
                    liquidity_usd=2e6,
                    organic_score_label="high",
                )
            )
            for w, vol, traders, bs in [
                ("5m", 3000.0, 40, (2000.0, 1000.0)),
                (
                    "1h",
                    60000.0 if spike else 30000.0,
                    400 if spike else 200,
                    (39000.0, 21000.0),
                ),
                ("24h", 5e6, 3000, (2.6e6, 2.4e6)),
            ]:
                s.add(
                    MarketWindowStats(
                        token_mint=mint,
                        source="jupiter",
                        observed_at=t,
                        window=w,
                        volume_usd=vol,
                        buy_volume_usd=bs[0],
                        sell_volume_usd=bs[1],
                        num_buys=traders * 2,
                        num_sells=traders,
                        num_traders=traders,
                        price_change_pct=9.0 if w == "1h" else 1.0,
                    )
                )
        s.add(
            TokenSecurity(
                token_mint=mint,
                checked_at=at - timedelta(hours=1),
                status="risky" if flags else "ok",
                risk_flags=flags or [],
            )
        )
        s.add(
            HolderSnapshot(
                token_mint=mint,
                observed_at=at - timedelta(hours=1),
                method_version="v2",
                top10_pct_adjusted=top10,
                top1_pct_adjusted=3.0,
                accounts=[],
            )
        )
        for t in sorted(spikes):  # a quote shortly before each evaluation time
            s.add(
                SwapQuote(
                    token_mint=mint,
                    quoted_at=t - timedelta(minutes=10),
                    side="sell",
                    notional_usd=1000,
                    cost_pct=0.25,
                )
            )


async def test_scoring_run_evaluates_ranks_and_records_reasons(factory: Factory) -> None:
    at = datetime(2026, 10, 11, 6, 4, tzinfo=UTC)
    await seed_token(factory, "GOOD", at)
    await seed_token(factory, "RISKY", at, flags=["mint_authority_active"])
    await seed_token(factory, "CONC", at, top10=55)
    result = await run_scoring(factory, 1, at)
    assert result.rows_written >= 3
    async with factory() as s:
        rows = {r.token_mint: r for r in await s.scalars(select(ScoreEvaluation))}
    good = rows["GOOD"]
    assert good.eligible and good.hourly and good.rank == 1 and good.coverage is not None
    assert good.total is not None and good.group == "established"
    assert good.features["volume_24h"] == 5e6 and "activity" in good.components
    assert rows["RISKY"].critical and rows["RISKY"].reasons == ["security:mint_authority_active"]
    assert rows["RISKY"].total is None  # ineligible tokens are not scored
    assert rows["CONC"].reasons == ["top10_concentration"] and not rows["CONC"].critical


async def test_watchlist_forms_after_two_qualifying_hourly_evaluations(factory: Factory) -> None:
    t1 = datetime(2026, 10, 11, 5, 4, tzinfo=UTC)
    await seed_token(factory, "GOOD", t1 + timedelta(hours=1), hours=6, strong_at=(t1,))
    await run_scoring(factory, 1, t1)  # first hourly evaluation: no previous -> no entry
    async with factory() as s:
        assert (await s.scalars(select(WatchlistMember))).all() == []
    await run_scoring(factory, 2, t1 + timedelta(hours=1))
    async with factory() as s:
        evs = (
            await s.scalars(select(ScoreEvaluation).where(ScoreEvaluation.token_mint == "GOOD"))
        ).all()
        members = (await s.scalars(select(WatchlistMember))).all()
        events = (await s.scalars(select(WatchlistEvent))).all()
    # Seeded token scores ~85 with ~97% coverage in both hourly evaluations.
    assert all(e.total is not None and e.total >= 70 and (e.coverage or 0) >= 0.8 for e in evs), [
        (e.evaluated_at, e.total, e.coverage, e.reasons, e.activity, e.momentum) for e in evs
    ]
    assert [m.token_mint for m in members] == ["GOOD"]
    assert [(e.action, e.reason) for e in events] == [("add", "qualified:established")]


async def test_critical_failure_removes_member_at_quarter_hour(factory: Factory) -> None:
    at = datetime(2026, 10, 11, 7, 19, tzinfo=UTC)  # not hourly, not a window
    await seed_token(factory, "BAD", at, flags=["freeze_authority_active"])
    async with factory() as s, s.begin():
        s.add(
            WatchlistMember(
                token_mint="BAD",
                group="established",
                joined_at=at - timedelta(days=1),
                entry_score=80,
                model_version="v1.1",
            )
        )
    await run_scoring(factory, 1, at)
    async with factory() as s:
        assert (await s.scalars(select(WatchlistMember))).all() == []
        ev = (await s.scalars(select(WatchlistEvent))).one()
    assert ev.action == "remove" and ev.reason == "critical:security:freeze_authority_active"


@pytest.fixture
def collector(settings: Settings, factory: Factory) -> Collector:
    fast = {"requests_per_second": 1000, "max_retries": 1}
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
async def test_paired_quote_cost_is_independent_of_stale_reference_price(
    collector: Collector, factory: Factory
) -> None:
    now = datetime.now(UTC)
    async with factory() as s, s.begin():
        s.add(Token(mint="Q", discovery_source="test", decimals=6))
        # Snapshot price is stale: 2.0, while the market now pays ~1.9 per token.
        s.add(
            MarketSnapshot(
                token_mint="Q", source="jupiter", observed_at=now, price_usd=2.0, liquidity_usd=1e6
            )
        )
        s.add(
            MarketWindowStats(
                token_mint="Q", source="jupiter", observed_at=now, window="24h", volume_usd=1e6
            )
        )

    def quote(req: httpx.Request) -> httpx.Response:
        amount = int(req.url.params["amount"])
        per_token = 1.9 if amount < 100 * 10**6 else 1.9 * 0.99  # 1% worse at size
        return httpx.Response(
            200,
            json={
                "inAmount": str(amount),
                "outAmount": str(int(amount * per_token)),
                "priceImpactPct": "0.01",
                "routePlan": [],
                "contextSlot": 1,
            },
        )

    respx.get("https://api.jup.ag/swap/v1/quote").mock(side_effect=quote)
    await collector.quotes(1)
    async with factory() as s:
        q = (await s.scalars(select(SwapQuote))).one()
    assert q.cost_pct == pytest.approx(1.0, abs=0.001)  # true size cost
    assert q.slippage_pct == pytest.approx(5.95, abs=0.01)  # inflated by the stale price
    assert q.ref_notional_usd == 10 and q.ref_out_usd == pytest.approx(9.5, abs=0.01)


async def test_selection_api(factory: Factory, engine: object) -> None:
    from fastapi.testclient import TestClient

    from autolp.api.main import app
    from autolp.config import get_settings

    at = datetime(2026, 10, 11, 6, 4, tzinfo=UTC)

    async def prepare() -> None:
        await seed_token(factory, "GOOD", at)
        await seed_token(factory, "RISKY", at, flags=["mint_authority_active"])
        await run_scoring(factory, 1, at)
        async with factory() as s, s.begin():
            s.add(
                WatchlistMember(
                    token_mint="GOOD",
                    group="established",
                    joined_at=at,
                    entry_score=80,
                    model_version="v1.1",
                )
            )

    await prepare()
    get_settings.cache_clear()
    with TestClient(app) as c:
        model = c.get("/api/model").json()
        assert model["version"] == "v1.1" and model["category_weights"]["activity"] == 0.25
        wl = c.get("/api/watchlist").json()
        assert [m["mint"] for m in wl["members"]] == ["GOOD"] and wl["members"][0]["rank"] == 1
        ev = c.get("/api/evaluations?view=all").json()
        assert ev["counts"]["evaluated"] == 2 and ev["counts"]["eligible"] == 1
        assert {"reason": "security:mint_authority_active", "n": 1} in ev["reasons"]
        risky = c.get(
            "/api/evaluations?view=ineligible&reason=security:mint_authority_active"
        ).json()
        assert [i["mint"] for i in risky["items"]] == ["RISKY"]
        scores = c.get("/api/tokens/GOOD/scores?hours=720").json()
        assert scores["latest"]["features"]["volume_24h"] == 5e6
        assert len(scores["history"]) == 1
        assert c.get("/api/holders/infrastructure-candidates").status_code == 200
