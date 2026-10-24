"""Fast prices: Price V3 parsing and the polling loop's change detection, skips and
rate-limit pause (the database side is in tests/integration/test_jobs.py)."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from autolp.collector.fast_prices import FastPrices
from autolp.collector.schedule import job_specs
from autolp.config import CollectionSettings, Settings
from autolp.providers import jupiter
from autolp.providers.http import ProviderHTTPError, ProviderResponse
from tests.conftest import load_fixture

BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
SOL = "So11111111111111111111111111111111111111112"


def test_parse_prices_keeps_usable_prices_only() -> None:
    prices = {p.mint: p for p in jupiter.parse_prices(load_fixture("jupiter_price.json"))}
    assert prices[BONK].usd_price == pytest.approx(3.0516e-06, rel=1e-3)
    assert prices[BONK].block_id == 460977008 and prices[SOL].liquidity_usd
    junk = {"a": None, "b": {"usdPrice": 0}, "c": {"usdPrice": "x"}, "d": {"usdPrice": 2}}
    assert [p.mint for p in jupiter.parse_prices(junk)] == ["d"]
    assert jupiter.parse_prices([]) == []


class FakeJupiter:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.payloads: list[Any] = []
        self.http = SimpleNamespace(settings=SimpleNamespace(rate_limited_backoff_seconds=15.0))

    async def prices(self, mints: list[str]) -> ProviderResponse:
        self.calls.append(mints)
        p = self.payloads.pop(0)
        if isinstance(p, Exception):
            raise p
        return ProviderResponse("jupiter", "/price/v3", {}, 200, p, datetime.now(UTC))


def make(clock: list[float], mints: list[str], **cfg: Any) -> tuple[FastPrices, FakeJupiter]:
    jup = FakeJupiter()

    async def select() -> list[str]:
        return list(mints)

    fp = FastPrices(
        cast(Any, None),
        cast(Any, jup),
        CollectionSettings(**cfg),
        select_shortlist=select,
        clock=lambda: clock[0],
    )
    return fp, jup


def price(mint: str, p: float, slot: int) -> dict[str, Any]:
    return {mint: {"usdPrice": p, "blockId": slot}}


async def test_poll_buffers_only_changed_prices() -> None:
    clock = [0.0]
    fp, jup = make(clock, [BONK])
    jup.payloads = [price(BONK, 1.0, 1), price(BONK, 1.0, 1), price(BONK, 1.0, 2)]
    for _ in range(3):
        await fp.poll()
        clock[0] += 2
    assert [r["block_id"] for r in fp._buffer] == [1, 2]  # the repeat is not stored
    assert fp.stats.polls == 3 and jup.calls == [[BONK]] * 3


async def test_shortlist_refreshes_when_due_and_empty_shortlist_skips_requests() -> None:
    clock = [0.0]
    fp, jup = make(clock, [], fast_prices_shortlist_minutes=1)
    await fp.poll()
    assert jup.calls == [] and fp.stats.polls == 0
    fp._select = lambda: asyncio.sleep(0, result=[SOL])  # type: ignore[method-assign]
    clock[0] = 30
    await fp.poll()
    assert jup.calls == []  # shortlist not due yet
    clock[0] = 61
    jup.payloads = [price(SOL, 100.0, 5)]
    await fp.poll()
    assert jup.calls == [[SOL]]


async def test_rate_limit_pauses_polling_for_the_backoff() -> None:
    clock = [0.0]
    fp, jup = make(clock, [BONK])
    jup.payloads = [ProviderHTTPError("jupiter", "/price/v3", 429, "slow down", None)]
    await fp.poll()
    assert fp.stats.failures == 1 and fp.stats.rate_limited == 1
    clock[0] = 10
    await fp.poll()
    assert len(jup.calls) == 1  # still paused
    clock[0] = 16
    jup.payloads = [price(BONK, 1.0, 1)]
    await fp.poll()
    assert len(jup.calls) == 2 and fp.stats.polls == 1


async def test_overlapping_poll_is_skipped() -> None:
    fp, _ = make([0.0], [BONK])
    fp._busy = True
    await fp.poll()
    assert fp.stats.skipped == 1


def test_budget_fits_the_free_jupiter_tier() -> None:
    s = Settings(_env_file=None)
    c = s.collection
    assert c.fast_prices_max_tokens <= jupiter.PRICE_MAX_IDS
    # One request per poll: at most half the shared 1 req/s budget.
    assert 1 / c.fast_prices_seconds <= s.jupiter.requests_per_second / 2
    assert "fast_prices" in {j.name for j in job_specs(s)}


async def test_prices_rejects_too_many_mints() -> None:
    client = jupiter.JupiterClient(Settings(_env_file=None).jupiter)
    try:
        with pytest.raises(ValueError):
            await client.prices([str(i) for i in range(51)])
    finally:
        await client.aclose()
