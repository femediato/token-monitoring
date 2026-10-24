"""Dune client: execute, poll until done, page through results (mocked HTTP)."""

import httpx
import pytest
import respx

from autolp.config import DuneSettings
from autolp.providers.dune import DuneClient, DuneQueryFailed

API = "https://api.dune.com/api/v1"


def client() -> DuneClient:
    return DuneClient(DuneSettings(api_key="k", requests_per_second=1000, max_retries=0))


@respx.mock
async def test_run_polls_and_pages() -> None:
    ex = respx.post(f"{API}/query/7/execute").mock(
        return_value=httpx.Response(
            200, json={"execution_id": "E1", "state": "QUERY_STATE_PENDING"}
        )
    )
    respx.get(f"{API}/execution/E1/status").mock(
        side_effect=[
            httpx.Response(200, json={"state": "QUERY_STATE_EXECUTING"}),
            httpx.Response(
                200, json={"state": "QUERY_STATE_COMPLETED", "execution_cost_credits": 3}
            ),
        ]
    )
    respx.get(f"{API}/execution/E1/results").mock(
        side_effect=[
            httpx.Response(200, json={"result": {"rows": [{"a": 1}]}, "next_offset": 1}),
            httpx.Response(200, json={"result": {"rows": [{"a": 2}]}}),
        ]
    )
    d = client()
    try:
        rows, status, pages = await d.run(7, {"mints": "X"}, poll_seconds=0)
    finally:
        await d.aclose()
    assert (
        rows == [{"a": 1}, {"a": 2}] and status["execution_cost_credits"] == 3 and len(pages) == 2
    )
    assert ex.calls[0].request.headers["X-Dune-API-Key"] == "k"


@respx.mock
async def test_failed_query_raises() -> None:
    respx.post(f"{API}/query/7/execute").mock(
        return_value=httpx.Response(200, json={"execution_id": "E2"})
    )
    respx.get(f"{API}/execution/E2/status").mock(
        return_value=httpx.Response(200, json={"state": "QUERY_STATE_FAILED", "error": "bad sql"})
    )
    d = client()
    try:
        with pytest.raises(DuneQueryFailed, match="bad sql"):
            await d.run(7, {}, poll_seconds=0)
    finally:
        await d.aclose()


def test_key_required() -> None:
    with pytest.raises(ValueError, match="DUNE__API_KEY"):
        DuneClient(DuneSettings())
