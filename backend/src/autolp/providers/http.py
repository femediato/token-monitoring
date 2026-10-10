"""Shared provider HTTP client: rate limiting, retries and raw-response capture."""

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

from autolp.config import ProviderSettings
from autolp.log import get_logger

log = get_logger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class RateLimiter:
    """Async token bucket: at most `rate` acquisitions per second, burst of `burst`."""

    def __init__(self, rate: float, burst: int = 1) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate
        self.capacity = float(burst)
        self._tokens = float(burst)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await asyncio.sleep((1 - self._tokens) / self.rate)


@dataclass(frozen=True)
class ProviderResponse:
    source: str
    endpoint: str
    params: dict[str, Any] | None
    status_code: int
    payload: Any
    fetched_at: datetime

    @property
    def payload_sha256(self) -> str:
        canonical = json.dumps(self.payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


class ProviderHTTPError(Exception):
    def __init__(self, source: str, endpoint: str, status_code: int, body: str) -> None:
        super().__init__(f"{source} {endpoint} -> HTTP {status_code}: {body[:200]}")
        self.status_code = status_code


ResponseSink = Callable[[ProviderResponse], Awaitable[None]]


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, ProviderHTTPError):
        return exc.status_code in RETRYABLE_STATUS
    return isinstance(exc, httpx.TransportError)


class ProviderClient:
    """HTTP client for one data provider.

    Every successful response is passed to `sink` (if given) so raw payloads are
    persisted before normalization. Non-retryable HTTP errors raise immediately.
    """

    def __init__(
        self,
        source: str,
        settings: ProviderSettings,
        *,
        headers: dict[str, str] | None = None,
        sink: ResponseSink | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_wait_seconds: float = 30.0,
    ) -> None:
        self.source = source
        self.settings = settings
        self.sink = sink
        self.max_wait_seconds = max_wait_seconds
        self.limiter = RateLimiter(settings.requests_per_second)
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            headers=headers,
            transport=transport,
        )

    async def __aenter__(self) -> "ProviderClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _log_retry(self, state: RetryCallState) -> None:
        exc = state.outcome.exception() if state.outcome else None
        log.warning(
            "provider_retry", source=self.source, attempt=state.attempt_number, error=str(exc)
        )

    async def request_json(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> ProviderResponse:
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(self.settings.max_retries + 1),
            wait=wait_random_exponential(multiplier=0.5, max=self.max_wait_seconds),
            before_sleep=self._log_retry,
            reraise=True,
        ):
            with attempt:
                await self.limiter.acquire()
                resp = await self._client.request(method, endpoint, params=params, json=json_body)
                if resp.status_code >= 400:
                    raise ProviderHTTPError(self.source, endpoint, resp.status_code, resp.text)
                result = ProviderResponse(
                    source=self.source,
                    endpoint=endpoint,
                    params=params if json_body is None else {"params": params, "body": json_body},
                    status_code=resp.status_code,
                    payload=resp.json(),
                    fetched_at=datetime.now(UTC),
                )
        if self.sink is not None:
            await self.sink(result)
        return result

    async def get_json(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> ProviderResponse:
        return await self.request_json("GET", endpoint, params=params)
