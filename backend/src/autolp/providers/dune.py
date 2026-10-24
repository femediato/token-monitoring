"""Dune Analytics API v1: run a saved, parameterised query and fetch its result rows.

Research data only (Phase 4a, docs/RESEARCH.md): nothing in the collector, the strategies
or paper trading reads Dune. The key is read-only (it can run queries and read results,
never sign or send anything).
"""

import asyncio
from typing import Any

from autolp.config import DuneSettings
from autolp.providers.http import ProviderClient, ProviderError, ProviderResponse

SOURCE = "dune"
DONE = "QUERY_STATE_COMPLETED"
FAILED = {"QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED", "QUERY_STATE_EXPIRED"}


class DuneQueryFailed(ProviderError):
    pass


class DuneClient:
    def __init__(self, settings: DuneSettings, **kw: Any) -> None:
        if settings.api_key is None:
            raise ValueError("DUNE__API_KEY is not set")
        headers = {"X-Dune-API-Key": settings.api_key.get_secret_value()}
        self.http = ProviderClient(SOURCE, settings, headers=headers, **kw)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def execute(self, query_id: int, params: dict[str, Any]) -> str:
        resp = await self.http.post_json(
            f"/query/{query_id}/execute", {"query_parameters": params, "performance": "medium"}
        )
        return str(resp.payload["execution_id"])

    async def status(self, execution_id: str) -> ProviderResponse:
        return await self.http.get_json(f"/execution/{execution_id}/status")

    async def results(
        self, execution_id: str, *, limit: int = 32_000
    ) -> tuple[list[dict[str, Any]], list[ProviderResponse]]:
        """All result rows, paged; the raw responses are returned for auditing."""
        rows: list[dict[str, Any]] = []
        pages: list[ProviderResponse] = []
        offset = 0
        while True:
            resp = await self.http.get_json(
                f"/execution/{execution_id}/results", {"limit": limit, "offset": offset}
            )
            pages.append(resp)
            batch = (resp.payload.get("result") or {}).get("rows") or []
            rows.extend(batch)
            nxt = resp.payload.get("next_offset")
            if nxt is None or not batch:
                return rows, pages
            offset = int(nxt)

    async def run(
        self,
        query_id: int,
        params: dict[str, Any],
        *,
        poll_seconds: float = 5,
        max_wait_seconds: float = 900,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], list[ProviderResponse]]:
        """Execute, wait for completion, return (rows, final status payload, raw pages)."""
        ex = await self.execute(query_id, params)
        waited = 0.0
        while True:
            st = (await self.status(ex)).payload
            state = st.get("state")
            if state == DONE:
                rows, pages = await self.results(ex)
                return rows, st, pages
            if state in FAILED:
                raise DuneQueryFailed(f"dune execution {ex}: {state} {st.get('error')}")
            if waited >= max_wait_seconds:
                raise DuneQueryFailed(
                    f"dune execution {ex}: still {state} after {max_wait_seconds:.0f}s"
                )
            await asyncio.sleep(poll_seconds)
            waited += poll_seconds
