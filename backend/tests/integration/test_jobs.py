"""Collector jobs against a real TimescaleDB with mocked provider HTTP (respx)."""

import copy
import re
from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autolp.collector.jobs import Collector
from autolp.collector.runs import run_recorded
from autolp.config import (
    DexScreenerSettings,
    GeckoTerminalSettings,
    HeliusSettings,
    JupiterSettings,
    Settings,
)
from autolp.db.base import utcnow
from autolp.db.models import (
    Candle5m,
    CollectorRun,
    MarketSnapshot,
    MarketWindowStats,
    Pool,
    PoolSnapshot,
    RawObservation,
    RunStatus,
    SwapQuote,
    Token,
    TokenDiscovery,
    TokenSecurity,
    TrackingEvent,
    TrackingStatus,
)
from autolp.monitoring.health import job_health
from autolp.providers.http import ProviderResponse
from tests.conftest import load_fixture

Factory = async_sessionmaker[AsyncSession]
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
JUP_LISTS = re.compile(r"https://api\.jup\.ag/tokens/v2/(toptrending|toptraded|toporganicscore)/")


@pytest.fixture
def collector(settings: Settings, factory: Factory) -> Collector:
    fast: dict[str, Any] = {"requests_per_second": 1000, "max_retries": 1}
    s = settings.model_copy(
        update={
            "jupiter": JupiterSettings(api_key="test-key", **fast),
            "dexscreener": DexScreenerSettings(**fast),
            "geckoterminal": GeckoTerminalSettings(**fast),
            "helius": HeliusSettings(api_key="test-key", **fast),
        }
    )
    return Collector.from_settings(s, factory)


async def add_tokens(factory: Factory, *mints: str, **fields: Any) -> None:
    async with factory() as s, s.begin():
        for m in mints:
            s.add(Token(mint=m, discovery_source="test", decimals=5, **fields))


async def count(factory: Factory, model: Any) -> int:
    async with factory() as s:
        return await s.scalar(select(func.count()).select_from(model)) or 0


@respx.mock
async def test_discovery_classifies_and_records_reasons(
    collector: Collector, factory: Factory
) -> None:
    payload = load_fixture("jupiter_search.json")
    stock = {**copy.deepcopy(payload[0]), "id": "STOCKmint", "tags": ["verified", "stocks"]}
    route = respx.get(JUP_LISTS).mock(return_value=httpx.Response(200, json=[*payload, stock]))

    run = await run_recorded(factory, "discovery", collector.discover)

    assert run.status is RunStatus.SUCCESS and route.call_count == 12
    async with factory() as s:
        toks = {t.mint: t for t in await s.scalars(select(Token))}
    assert toks[BONK].classification == "meme" and toks[BONK].tracking_status == "active"
    assert toks["STOCKmint"].tracking_status == "excluded"
    assert toks["STOCKmint"].tracking_reason == "tag:stocks:tokenized_asset"
    assert toks[BONK].discovery_source.startswith("jupiter:toptrending/")
    async with factory() as s:
        events = {e.token_mint: e for e in await s.scalars(select(TrackingEvent))}
    assert len(events) == 3  # one per new token, despite 12 appearances each
    assert (events[BONK].from_status, events[BONK].to_status) == (None, "active")
    assert events[BONK].reason.startswith("discovered:")
    assert events["STOCKmint"].to_status == "excluded"
    assert await count(factory, TokenDiscovery) == 12 * 3
    assert await count(factory, RawObservation) == 12
    assert route.calls.last.request.headers["x-api-key"] == "test-key"


@respx.mock
async def test_rediscovery_reactivates_dormant_token(
    collector: Collector, factory: Factory
) -> None:
    await add_tokens(factory, BONK, tracking_status=TrackingStatus.DORMANT)
    respx.get(JUP_LISTS).mock(
        return_value=httpx.Response(200, json=load_fixture("jupiter_search.json"))
    )
    await run_recorded(factory, "discovery", collector.discover)
    async with factory() as s:
        tok = await s.get_one(Token, BONK)
    assert tok.tracking_status == "active"
    assert (tok.tracking_reason or "").startswith("rediscovered:")
    async with factory() as s:
        events = (
            await s.scalars(select(TrackingEvent).where(TrackingEvent.token_mint == BONK))
        ).all()
    assert [(e.from_status, e.to_status) for e in events] == [("dormant", "active")]


@respx.mock
async def test_discovery_fails_when_every_list_fails(
    collector: Collector, factory: Factory
) -> None:
    respx.get(JUP_LISTS).mock(return_value=httpx.Response(401, text="bad key"))
    run = await run_recorded(factory, "discovery", collector.discover)
    assert run.status is RunStatus.FAILED and "all 12 discovery lists failed" in (run.error or "")


@respx.mock
async def test_snapshots_link_to_raw_observation(collector: Collector, factory: Factory) -> None:
    await add_tokens(factory, BONK, WIF)
    respx.get("https://api.jup.ag/tokens/v2/search").mock(
        return_value=httpx.Response(200, json=load_fixture("jupiter_search.json"))
    )
    run = await run_recorded(
        factory, "snapshots_active", lambda rid: collector.snapshots(rid, TrackingStatus.ACTIVE)
    )
    assert run.status is RunStatus.SUCCESS and run.rows_written == 2 + 8
    async with factory() as s:
        snap = (
            await s.scalars(select(MarketSnapshot).where(MarketSnapshot.token_mint == BONK))
        ).one()
        raw = (await s.scalars(select(RawObservation))).one()
        windows = await s.scalar(select(func.count()).select_from(MarketWindowStats))
    assert snap.raw_observation_id == raw.id and raw.collector_run_id == run.id
    assert snap.observed_at == raw.fetched_at
    assert snap.source_updated_at is not None and snap.price_usd is not None
    assert windows == 8


@respx.mock
async def test_snapshots_ignore_tokens_not_requested(
    collector: Collector, factory: Factory
) -> None:
    await add_tokens(factory, BONK)
    respx.get("https://api.jup.ag/tokens/v2/search").mock(
        return_value=httpx.Response(200, json=load_fixture("jupiter_search.json"))
    )
    await collector.snapshots(1, TrackingStatus.ACTIVE)
    assert await count(factory, MarketSnapshot) == 1


@respx.mock
async def test_pools_store_reserves_and_pick_deepest_pool(
    collector: Collector, factory: Factory
) -> None:
    await add_tokens(factory, BONK, WIF)
    pairs = load_fixture("dexscreener_tokens.json")
    shallow = {**copy.deepcopy(pairs[0]), "pairAddress": "ShallowPool", "liquidity": {"usd": 5}}
    respx.get(re.compile(r"https://api\.dexscreener\.com/tokens/v1/solana/.*")).mock(
        return_value=httpx.Response(200, json=[shallow, *pairs])
    )
    run = await run_recorded(factory, "pools", collector.pools)
    assert run.status is RunStatus.SUCCESS
    async with factory() as s:
        bonk = await s.get_one(Token, BONK)
        snap = (
            await s.scalars(
                select(PoolSnapshot).where(PoolSnapshot.pool_address == bonk.primary_pool)
            )
        ).one()
    assert bonk.primary_pool == "5zpyutJu9ee6jFymDGoK7F6S5Kczqtc9FomP3ueKuyA9"
    assert snap.reserve_quote and snap.reserve_base and snap.buys_24h == 3500
    assert await count(factory, Pool) == 3


async def test_completed_candles_are_never_overwritten(
    collector: Collector, factory: Factory
) -> None:
    payload = load_fixture("geckoterminal_ohlcv.json")
    rows = payload["data"]["attributes"]["ohlcv_list"]
    newest_ts = rows[0][0]
    fetched = utcnow()
    first = ProviderResponse("geckoterminal", "/x", None, 200, payload, fetched)
    # Pretend the newest candle was still forming at the first fetch.
    from datetime import UTC, datetime

    forming_at = datetime.fromtimestamp(newest_ts, UTC) + timedelta(minutes=2)
    await collector._store_candles(
        1, BONK, "P", ProviderResponse("geckoterminal", "/x", None, 200, payload, forming_at)
    )
    changed = copy.deepcopy(payload)
    for r in changed["data"]["attributes"]["ohlcv_list"]:
        r[4] = 123.0
    await collector._store_candles(
        1, BONK, "P", ProviderResponse("geckoterminal", "/x", None, 200, changed, fetched)
    )
    async with factory() as s:
        candles = {c.bucket.timestamp(): c for c in await s.scalars(select(Candle5m))}
    assert len(candles) == 3
    assert candles[newest_ts].close == 123.0 and candles[newest_ts].is_complete  # was forming
    assert all(c.close != 123.0 for ts, c in candles.items() if ts != newest_ts)  # final
    del first


@respx.mock
async def test_security_unknown_when_account_missing(
    collector: Collector, factory: Factory
) -> None:
    await add_tokens(factory, BONK, "MissingMint")
    bonk_acc = load_fixture("helius_mint_account.json")["result"]["value"]
    route = respx.post(host="mainnet.helius-rpc.com").mock(
        return_value=httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": {"value": [bonk_acc, None]}}
        )
    )
    run = await run_recorded(factory, "security", collector.security)
    assert run.status is RunStatus.SUCCESS
    async with factory() as s:
        rows = {r.token_mint: r for r in await s.scalars(select(TokenSecurity))}
        raw = (await s.scalars(select(RawObservation))).one()
    assert rows[BONK].status == "ok" and rows["MissingMint"].status == "unknown"
    assert route.calls.last.request.url.params["api-key"] == "test-key"
    assert "test-key" not in str(raw.params) and "test-key" not in raw.endpoint


async def _fresh_snapshot(
    factory: Factory, mint: str, liq: float, vol: float, price: float
) -> None:
    now = utcnow()
    async with factory() as s, s.begin():
        s.add(
            MarketSnapshot(
                token_mint=mint,
                source="jupiter",
                observed_at=now,
                price_usd=price,
                liquidity_usd=liq,
            )
        )
        s.add(
            MarketWindowStats(
                token_mint=mint, source="jupiter", observed_at=now, window="24h", volume_usd=vol
            )
        )


@respx.mock
async def test_quotes_measure_slippage_and_record_failures(
    collector: Collector, factory: Factory
) -> None:
    await add_tokens(factory, BONK, WIF)
    await _fresh_snapshot(factory, BONK, 1e6, 1e6, 3.4e-6)
    await _fresh_snapshot(factory, WIF, 1e6, 1e6, 0.5)
    quote = load_fixture("jupiter_quote.json")
    quote_route = respx.get("https://api.jup.ag/swap/v1/quote")
    quote_route.side_effect = lambda req: (
        httpx.Response(200, json=quote)
        if req.url.params["inputMint"] == BONK
        else httpx.Response(400, json={"error": "No routes found"})
    )
    collector.cfg = collector.cfg.model_copy(update={"quote_sell_usd": 0.34})
    run = await run_recorded(factory, "quotes", collector.quotes)
    assert run.status is RunStatus.SUCCESS and run.rows_written == 2
    async with factory() as s:
        q = {r.token_mint: r for r in await s.scalars(select(SwapQuote))}
    assert q[BONK].error is None and q[BONK].out_usd == pytest.approx(0.340462)
    assert q[BONK].slippage_pct == pytest.approx((0.34 - 0.340462) / 0.34 * 100)
    assert q[WIF].error is not None and "No routes" in q[WIF].error and q[WIF].out_usd is None


async def test_tracking_moves_inactive_tokens_to_dormant(
    collector: Collector, factory: Factory
) -> None:
    old = utcnow() - timedelta(days=2)
    await add_tokens(factory, "Quiet", "Busy", first_seen_at=old)
    await _fresh_snapshot(factory, "Quiet", liq=1_000, vol=500, price=1)
    await _fresh_snapshot(factory, "Busy", liq=1e6, vol=1e6, price=1)
    await add_tokens(factory, "Back", tracking_status=TrackingStatus.DORMANT, first_seen_at=old)
    await _fresh_snapshot(factory, "Back", liq=1e6, vol=1e6, price=1)

    await run_recorded(factory, "tracking", collector.update_tracking)
    async with factory() as s:
        status = {
            t.mint: (t.tracking_status, t.tracking_reason) for t in await s.scalars(select(Token))
        }
    assert status["Quiet"] == ("dormant", "below_tracking_floor")
    assert status["Busy"][0] == "active"
    assert status["Back"] == ("active", "above_tracking_floor")
    async with factory() as s:
        events = {e.token_mint: e for e in await s.scalars(select(TrackingEvent))}
    assert set(events) == {"Quiet", "Back"}
    assert (events["Quiet"].from_status, events["Quiet"].to_status) == ("active", "dormant")


async def test_job_health_detects_gaps_and_staleness(factory: Factory) -> None:
    now = utcnow()
    since = now - timedelta(hours=1)
    starts = [since + timedelta(minutes=m) for m in (0, 5, 10, 15, 50, 55)]  # 35-minute gap
    async with factory() as s, s.begin():
        for t in starts:
            s.add(CollectorRun(job="snap", started_at=t, finished_at=t, status=RunStatus.SUCCESS))
        s.add(
            CollectorRun(job="old", started_at=since, finished_at=since, status=RunStatus.SUCCESS)
        )
        s.add(
            CollectorRun(
                job="old", started_at=since + timedelta(minutes=1), status=RunStatus.FAILED
            )
        )
    async with factory() as s:
        health = {
            h.job: h for h in await job_health(s, {"snap": 5, "old": 5, "never": 5}, since, now)
        }
    assert health["snap"].status == "degraded" and health["snap"].gaps == 1
    assert health["snap"].max_gap_minutes == pytest.approx(35.0)
    assert health["old"].status == "stale" and health["old"].failures == 1
    assert health["never"].status == "never_ran"


async def test_dormant_tokens_not_in_active_snapshot_batches(
    collector: Collector, factory: Factory
) -> None:
    await add_tokens(factory, "A")
    await add_tokens(factory, "D", tracking_status=TrackingStatus.DORMANT)
    await add_tokens(factory, "X", tracking_status=TrackingStatus.EXCLUDED)
    assert await collector._mints(TrackingStatus.ACTIVE) == ["A"]
    assert await collector._mints(TrackingStatus.DORMANT) == ["D"]
    async with factory() as s, s.begin():
        await s.execute(update(Token).values(tracking_status=TrackingStatus.ACTIVE))


@respx.mock
async def test_pool_discovery_switches_to_deepest_pool_and_resets_backfill(
    collector: Collector, factory: Factory
) -> None:
    await add_tokens(factory, BONK, primary_pool="ShallowPool", candles_backfilled_at=utcnow())
    pairs = load_fixture("dexscreener_tokens.json")
    shallow = {**copy.deepcopy(pairs[0]), "pairAddress": "ShallowPool", "liquidity": {"usd": 5}}
    respx.get(f"https://api.dexscreener.com/token-pairs/v1/solana/{BONK}").mock(
        return_value=httpx.Response(200, json=[shallow, pairs[0]])
    )
    run = await run_recorded(factory, "pool_discovery", collector.pool_discovery)
    assert run.status is RunStatus.SUCCESS
    async with factory() as s:
        tok = await s.get_one(Token, BONK)
    assert tok.primary_pool == "5zpyutJu9ee6jFymDGoK7F6S5Kczqtc9FomP3ueKuyA9"
    assert tok.candles_backfilled_at is None  # history is re-fetched for the new pool


@respx.mock
async def test_pool_discovery_ignores_pools_listed_with_base_and_quote_swapped(
    collector: Collector, factory: Factory
) -> None:
    """DEX Screener can list a token/token pool with base and quote swapped and a nonsense
    liquidity; it must not become primary (its candles would be the other token's)."""
    await add_tokens(factory, BONK)
    async with factory() as s, s.begin():  # the pool as first seen: WIF is its base
        s.add(
            Pool(address="TokenTokenPool", token_mint=WIF, quote_mint=BONK, first_seen_at=utcnow())
        )
    pairs = load_fixture("dexscreener_tokens.json")
    swapped = {
        **copy.deepcopy(pairs[0]),
        "pairAddress": "TokenTokenPool",
        "quoteToken": {"address": WIF, "name": "dogwifhat", "symbol": "$WIF"},
        "liquidity": {"usd": 157_599_669, "base": 1, "quote": 1},
    }
    respx.get(f"https://api.dexscreener.com/token-pairs/v1/solana/{BONK}").mock(
        return_value=httpx.Response(200, json=[swapped, pairs[0]])
    )
    await run_recorded(factory, "pool_discovery", collector.pool_discovery)
    async with factory() as s:
        tok = await s.get_one(Token, BONK)
    assert tok.primary_pool == "5zpyutJu9ee6jFymDGoK7F6S5Kczqtc9FomP3ueKuyA9"


@respx.mock
async def test_candles_are_requested_for_the_tracked_token(
    collector: Collector, factory: Factory
) -> None:
    route = respx.get(
        "https://api.geckoterminal.com/api/v2/networks/solana/pools/P/ohlcv/minute"
    ).mock(return_value=httpx.Response(200, json=load_fixture("geckoterminal_ohlcv.json")))
    await add_tokens(factory, BONK)
    await collector._fetch_candles(1, BONK, "P", 10, None)
    assert route.calls.last.request.url.params["token"] == BONK


@respx.mock
async def test_pools_snapshot_primary_pool_by_address(
    collector: Collector, factory: Factory
) -> None:
    deep = "5zpyutJu9ee6jFymDGoK7F6S5Kczqtc9FomP3ueKuyA9"
    await add_tokens(factory, BONK, primary_pool=deep)
    await add_tokens(factory, WIF)  # no pool yet -> bootstrap via token endpoint
    pairs = load_fixture("dexscreener_tokens.json")
    by_pair = respx.get(f"https://api.dexscreener.com/latest/dex/pairs/solana/{deep}").mock(
        return_value=httpx.Response(200, json={"schemaVersion": "1", "pairs": [pairs[0]]})
    )
    by_token = respx.get(f"https://api.dexscreener.com/tokens/v1/solana/{WIF}").mock(
        return_value=httpx.Response(200, json=[pairs[1]])
    )
    run = await run_recorded(factory, "pools", collector.pools)
    assert run.status is RunStatus.SUCCESS and by_pair.called and by_token.called
    async with factory() as s:
        snaps = {p.token_mint: p for p in await s.scalars(select(PoolSnapshot))}
        wif = await s.get_one(Token, WIF)
    assert snaps[BONK].pool_address == deep and snaps[BONK].reserve_quote
    assert wif.primary_pool == pairs[1]["pairAddress"]
