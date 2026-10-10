import time

import httpx
import pytest
import respx

from autolp.config import ProviderSettings
from autolp.providers.http import (
    ProviderClient,
    ProviderHTTPError,
    ProviderResponse,
    ProviderTransportError,
    RateLimiter,
)

BASE = "https://example.test"


def make_client(**kw: object) -> ProviderClient:
    settings = ProviderSettings(
        base_url=BASE, requests_per_second=1000, max_retries=3, rate_limited_backoff_seconds=0.01
    )
    return ProviderClient("test", settings, max_wait_seconds=0.01, **kw)  # type: ignore[arg-type]


@respx.mock
async def test_retries_on_429_then_succeeds() -> None:
    route = respx.get(f"{BASE}/x").mock(
        side_effect=[httpx.Response(429), httpx.Response(503), httpx.Response(200, json={"a": 1})]
    )
    async with make_client() as client:
        resp = await client.get_json("/x")
    assert resp.payload == {"a": 1}
    assert route.call_count == 3


@respx.mock
async def test_retries_on_transport_error() -> None:
    route = respx.get(f"{BASE}/x").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json=[])]
    )
    async with make_client() as client:
        await client.get_json("/x")
    assert route.call_count == 2


@respx.mock
async def test_non_retryable_error_raises_immediately() -> None:
    route = respx.get(f"{BASE}/x").mock(return_value=httpx.Response(401, text="bad key"))
    async with make_client() as client:
        with pytest.raises(ProviderHTTPError) as err:
            await client.get_json("/x")
    assert err.value.status_code == 401
    assert route.call_count == 1


@respx.mock
async def test_gives_up_after_max_retries() -> None:
    route = respx.get(f"{BASE}/x").mock(return_value=httpx.Response(500))
    async with make_client() as client:
        with pytest.raises(ProviderHTTPError):
            await client.get_json("/x")
    assert route.call_count == 4


@respx.mock
async def test_sink_receives_raw_response_only_on_success() -> None:
    respx.get(f"{BASE}/ok").mock(return_value=httpx.Response(200, json={"b": [1, 2]}))
    respx.get(f"{BASE}/bad").mock(return_value=httpx.Response(404))
    captured: list[ProviderResponse] = []

    async def sink(r: ProviderResponse) -> None:
        captured.append(r)

    async with make_client(sink=sink) as client:
        await client.get_json("/ok", params={"ids": "m1"})
        with pytest.raises(ProviderHTTPError):
            await client.get_json("/bad")
    assert len(captured) == 1
    r = captured[0]
    assert (r.source, r.endpoint, r.params, r.payload) == (
        "test",
        "/ok",
        {"ids": "m1"},
        {"b": [1, 2]},
    )
    assert r.fetched_at.utcoffset() is not None and r.fetched_at.utcoffset().total_seconds() == 0
    assert len(r.payload_sha256) == 64


def test_payload_hash_is_key_order_independent() -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    a = ProviderResponse("s", "/e", None, 200, {"x": 1, "y": 2}, now)
    b = ProviderResponse("s", "/e", None, 200, {"y": 2, "x": 1}, now)
    assert a.payload_sha256 == b.payload_sha256


async def test_rate_limiter_spaces_requests() -> None:
    limiter = RateLimiter(rate=20)  # 50 ms per token, burst 1
    start = time.monotonic()
    for _ in range(5):
        await limiter.acquire()
    elapsed = time.monotonic() - start
    assert 0.18 <= elapsed < 0.6


def test_rate_limiter_rejects_nonpositive_rate() -> None:
    with pytest.raises(ValueError):
        RateLimiter(rate=0)


@respx.mock
async def test_429_waits_for_retry_after() -> None:
    respx.get(f"{BASE}/x").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "0.3"}),
            httpx.Response(200, json=1),
        ]
    )
    async with make_client() as client:
        start = time.monotonic()
        await client.get_json("/x")
    assert time.monotonic() - start >= 0.3


@respx.mock
async def test_transport_error_message_hides_url_credentials() -> None:
    respx.get(f"{BASE}/x").mock(
        side_effect=httpx.ConnectError("failed for https://h?api-key=SECRET")
    )
    async with make_client() as client:
        with pytest.raises(ProviderTransportError) as err:
            await client.get_json("/x")
    assert "SECRET" not in str(err.value)


@respx.mock
async def test_default_params_sent_but_not_recorded() -> None:
    route = respx.get(f"{BASE}/x").mock(return_value=httpx.Response(200, json=[]))
    settings = ProviderSettings(base_url=BASE, requests_per_second=1000)
    async with ProviderClient("t", settings, default_params={"api-key": "SECRET"}) as client:
        resp = await client.get_json("/x", {"q": "1"})
    assert route.calls.last.request.url.params["api-key"] == "SECRET"
    assert resp.params == {"q": "1"}
