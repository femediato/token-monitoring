"""Fast prices for the shortlist (docs/DATA_SOURCES.md, "Fast prices").

A dedicated loop polls Jupiter Price V3 every `fast_prices_seconds` for the shortlist:
open paper positions first, then tokens eligible in the last `fast_prices_eligible_hours`,
at most `fast_prices_max_tokens` (one request). It is market-data collection only: no
strategy or paper decision reads these prices yet.

Budget: the loop shares the Jupiter rate limiter with every other Jupiter job, so the key's
total request rate stays within `jupiter.requests_per_second`. It never retries: a late
price is worth less than the next poll. After a 429 it pauses for the provider's backoff.

Storage: a `price_ticks` row only when a token's price or slot changed since its previous
poll; ticks are buffered and written every `fast_prices_flush_seconds`. Raw responses are
not all kept (about 43,000 a day); the `fast_prices` job keeps one sample a minute in
raw_observations and records the minute's polls, failures and rate limits in
collector_runs, so a gap in polling can be told apart from an unchanged price.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autolp.collector.runs import JobResult
from autolp.config import CollectionSettings
from autolp.db.base import utcnow
from autolp.db.models import PaperPosition, PriceTick, ScoreEvaluation
from autolp.db.repo import raw_observation_from
from autolp.log import get_logger
from autolp.providers.http import ProviderError, ProviderHTTPError, ProviderResponse
from autolp.providers.jupiter import SOURCE, JupiterClient, parse_prices

log = get_logger(__name__)

Factory = async_sessionmaker[AsyncSession]


class NoFastPrices(Exception):
    """Every poll of the last minute failed although the shortlist was not empty."""


@dataclass
class Stats:
    polls: int = 0
    failures: int = 0
    rate_limited: int = 0
    skipped: int = 0  # the previous poll was still in flight
    rows: int = 0
    lag_ms: list[float] = field(default_factory=list)  # request round trip


async def shortlist(factory: Factory, cfg: CollectionSettings) -> list[str]:
    """Open or pending paper positions first, then recently eligible tokens; capped."""
    since = utcnow() - timedelta(hours=cfg.fast_prices_eligible_hours)
    async with factory() as s:
        held = list(
            await s.scalars(
                select(PaperPosition.token_mint)
                .where(PaperPosition.status.in_(("pending", "open")))
                .distinct()
                .order_by(PaperPosition.token_mint)
            )
        )
        eligible = list(
            await s.scalars(
                select(ScoreEvaluation.token_mint)
                .where(ScoreEvaluation.evaluated_at >= since, ScoreEvaluation.eligible.is_(True))
                .distinct()
                .order_by(ScoreEvaluation.token_mint)
            )
        )
    out = list(dict.fromkeys([*held, *eligible]))
    return out[: cfg.fast_prices_max_tokens]


class FastPrices:
    def __init__(
        self,
        factory: Factory,
        jup: JupiterClient,
        cfg: CollectionSettings,
        *,
        select_shortlist: Callable[[], Awaitable[list[str]]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.factory, self.jup, self.cfg, self.clock = factory, jup, cfg, clock
        self._select = select_shortlist or (lambda: shortlist(factory, cfg))
        self.mints: list[str] = []
        self._mints_at: float | None = None
        self._last: dict[str, tuple[float, int | None]] = {}
        self._buffer: list[dict[str, object]] = []
        self._sample: ProviderResponse | None = None
        self._busy = False
        self._task: asyncio.Task[None] | None = None
        self._paused_until = 0.0
        self.stats = Stats()

    async def poll(self) -> None:
        """One poll: refresh the shortlist when due, request prices, buffer changes."""
        if self._busy:
            self.stats.skipped += 1
            return
        now = self.clock()
        if now < self._paused_until:
            return
        self._busy = True
        try:
            due = self.cfg.fast_prices_shortlist_minutes * 60
            if self._mints_at is None or now - self._mints_at >= due:
                self.mints = await self._select()
                self._mints_at = now
            if not self.mints:
                return
            t0 = self.clock()
            try:
                resp = await self.jup.prices(self.mints)
            except ProviderError as exc:
                self.stats.failures += 1
                if isinstance(exc, ProviderHTTPError) and exc.status_code == 429:
                    self.stats.rate_limited += 1
                    wait = exc.retry_after or self.jup.http.settings.rate_limited_backoff_seconds
                    self._paused_until = self.clock() + wait
                log.debug("fast_prices_failed", error=str(exc))
                return
            self.stats.polls += 1
            self.stats.lag_ms.append((self.clock() - t0) * 1000)
            self._sample = resp
            for p in parse_prices(resp.payload):
                key = (p.usd_price, p.block_id)
                if self._last.get(p.mint) == key:
                    continue
                self._last[p.mint] = key
                self._buffer.append(
                    {
                        "token_mint": p.mint,
                        "observed_at": resp.fetched_at,
                        "source": SOURCE,
                        "price_usd": p.usd_price,
                        "block_id": p.block_id,
                        "liquidity_usd": p.liquidity_usd,
                    }
                )
        finally:
            self._busy = False

    async def flush(self) -> int:
        rows, self._buffer = self._buffer, []
        if not rows:
            return 0
        async with self.factory() as s, s.begin():
            await s.execute(insert(PriceTick).values(rows).on_conflict_do_nothing())
        self.stats.rows += len(rows)
        return len(rows)

    async def run(self, stop: asyncio.Event) -> None:
        """Poll on a fixed grid of `fast_prices_seconds` until `stop` is set. A poll that
        overruns its slot makes the next slots skip, never pile up."""
        period = self.cfg.fast_prices_seconds
        next_at = self.clock()
        last_flush = next_at
        log.info("fast_prices_started", seconds=period, max_tokens=self.cfg.fast_prices_max_tokens)
        while not stop.is_set():
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._safe_poll())
            else:
                self.stats.skipped += 1
            if self.clock() - last_flush >= self.cfg.fast_prices_flush_seconds:
                last_flush = self.clock()
                await self._safe_flush()
            next_at += period
            delay = next_at - self.clock()
            if delay < 0:  # fell behind: realign on the grid
                next_at = self.clock()
                delay = 0
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)
        await self._safe_flush()
        log.info("fast_prices_stopped")

    async def _safe_poll(self) -> None:
        try:
            await self.poll()
        except Exception:
            self.stats.failures += 1
            log.exception("fast_prices_poll_error")

    async def _safe_flush(self) -> None:
        try:
            await self.flush()
        except Exception:
            log.exception("fast_prices_flush_error")

    async def summary(self, run_id: int) -> JobResult:
        """The `fast_prices` job (every minute): flush, keep one raw sample, and record the
        minute's polls. Fails when every poll failed for a non-empty shortlist."""
        await self.flush()
        stats, self.stats = self.stats, Stats()
        sample, self._sample = self._sample, None
        if sample is not None:
            async with self.factory() as s, s.begin():
                s.add(raw_observation_from(sample, run_id))
        lag = sorted(stats.lag_ms)
        log.info(
            "fast_prices_minute",
            tokens=len(self.mints),
            polls=stats.polls,
            failures=stats.failures,
            rate_limited=stats.rate_limited,
            skipped=stats.skipped,
            rows=stats.rows,
            lag_ms_median=round(lag[len(lag) // 2]) if lag else None,
        )
        if self.mints and stats.polls == 0 and stats.failures > 0:
            raise NoFastPrices(
                f"{stats.failures} polls failed ({stats.rate_limited} rate limited) "
                f"for {len(self.mints)} tokens"
            )
        return JobResult(rows_written=stats.rows, api_credits_used=stats.polls + stats.failures)
