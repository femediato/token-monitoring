"""Collection jobs. Each job stores the verbatim provider response in raw_observations and
the normalized rows in the same transaction, so every row can be traced to its source.

Jobs only write market data. They never read strategy state or place orders.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autolp.collector.runs import JobResult
from autolp.config import Settings
from autolp.db.base import utcnow
from autolp.db.models import (
    Candle5m,
    HolderSnapshot,
    MarketSnapshot,
    MarketWindowStats,
    Pool,
    PoolSnapshot,
    SwapQuote,
    Token,
    TokenDiscovery,
    TokenSecurity,
    TrackingStatus,
)
from autolp.db.repo import raw_observation_from
from autolp.log import get_logger
from autolp.providers import dexscreener, geckoterminal, helius, jupiter
from autolp.providers._util import chunks
from autolp.providers.dexscreener import DexScreenerClient
from autolp.providers.geckoterminal import GeckoTerminalClient
from autolp.providers.helius import HeliusClient
from autolp.providers.http import ProviderError, ProviderResponse
from autolp.providers.jupiter import JupiterClient
from autolp.universe import classify

log = get_logger(__name__)
Factory = async_sessionmaker[AsyncSession]


class AllRequestsFailed(Exception):
    """Raised when every provider request in a job failed, so the run is marked FAILED."""


async def store_raw(session: AsyncSession, resp: ProviderResponse, run_id: int) -> int:
    raw = raw_observation_from(resp, run_id)
    session.add(raw)
    await session.flush()
    return raw.id


@dataclass(frozen=True)
class LatestSnapshot:
    mint: str
    observed_at: datetime
    price_usd: float | None
    liquidity_usd: float | None
    volume_24h_usd: float | None


async def latest_jupiter_snapshots(
    session: AsyncSession, since: datetime, mints: Sequence[str] | None = None
) -> dict[str, LatestSnapshot]:
    s, w = MarketSnapshot, MarketWindowStats
    q = (
        select(s.token_mint, s.observed_at, s.price_usd, s.liquidity_usd, w.volume_usd)
        .outerjoin(
            w,
            and_(
                w.token_mint == s.token_mint,
                w.source == s.source,
                w.observed_at == s.observed_at,
                w.window == "24h",
            ),
        )
        .where(s.source == jupiter.SOURCE, s.observed_at >= since)
        .ext(distinct_on(s.token_mint))
        .order_by(s.token_mint, s.observed_at.desc())
    )
    if mints is not None:
        q = q.where(s.token_mint.in_(mints))
    rows = (await session.execute(q)).all()
    return {r[0]: LatestSnapshot(*r) for r in rows}


class Collector:
    def __init__(
        self,
        settings: Settings,
        factory: Factory,
        *,
        jup: JupiterClient,
        dex: DexScreenerClient,
        gecko: GeckoTerminalClient,
        rpc: HeliusClient,
    ) -> None:
        self.settings = settings
        self.cfg = settings.collection
        self.factory = factory
        self.jup, self.dex, self.gecko, self.rpc = jup, dex, gecko, rpc

    @classmethod
    def from_settings(cls, settings: Settings, factory: Factory) -> "Collector":
        return cls(
            settings,
            factory,
            jup=JupiterClient(settings.jupiter),
            dex=DexScreenerClient(settings.dexscreener),
            gecko=GeckoTerminalClient(settings.geckoterminal),
            rpc=HeliusClient(settings.helius),
        )

    async def aclose(self) -> None:
        for client in (self.jup, self.dex, self.gecko, self.rpc):
            await client.aclose()

    async def _mints(self, *statuses: TrackingStatus) -> list[str]:
        async with self.factory() as session:
            q = select(Token.mint).where(Token.tracking_status.in_(statuses)).order_by(Token.mint)
            return list((await session.scalars(q)).all())

    # ------------------------------------------------------------------ discovery

    async def discover(self, run_id: int) -> JobResult:
        result, failures, lists = JobResult(), 0, 0
        manual = frozenset(self.cfg.manual_exclude_mints)
        for category in self.cfg.discovery_categories:
            for interval in self.cfg.discovery_intervals:
                lists += 1
                list_name = f"{category}/{interval}"
                try:
                    resp = await self.jup.top_list(category, interval, self.cfg.discovery_limit)
                except ProviderError as exc:
                    failures += 1
                    log.warning("discovery_list_failed", list=list_name, error=str(exc))
                    continue
                result.api_credits_used += 1
                async with self.factory() as session, session.begin():
                    await store_raw(session, resp, run_id)
                    tokens = jupiter.parse_tokens(resp.payload)
                    existing = {
                        t.mint: t
                        for t in await session.scalars(
                            select(Token).where(Token.mint.in_([t.mint for t in tokens]))
                        )
                    }
                    for rank, jt in enumerate(tokens, start=1):
                        c = classify(jt.mint, jt.tags, jt.launchpad, manual)
                        tok = existing.get(jt.mint)
                        if tok is None:
                            tok = Token(
                                mint=jt.mint,
                                first_seen_at=resp.fetched_at,
                                discovery_source=f"{jupiter.SOURCE}:{list_name}",
                                tracking_status=TrackingStatus.ACTIVE,
                                tracking_reason="discovered",
                                tracking_changed_at=resp.fetched_at,
                            )
                            session.add(tok)
                            existing[jt.mint] = tok
                            result.rows_written += 1
                        self._apply_metadata(tok, jt, c, resp.fetched_at)
                        session.add(
                            TokenDiscovery(
                                token_mint=jt.mint,
                                discovered_at=resp.fetched_at,
                                source=jupiter.SOURCE,
                                list_name=list_name,
                                rank=rank,
                                collector_run_id=run_id,
                            )
                        )
                        result.rows_written += 1
        if failures == lists:
            raise AllRequestsFailed(f"all {lists} discovery lists failed")
        return result

    @staticmethod
    def _apply_metadata(tok: Token, jt: jupiter.JupiterToken, c: Any, at: datetime) -> None:
        tok.symbol, tok.name = jt.symbol, jt.name
        tok.decimals = jt.decimals if jt.decimals is not None else tok.decimals
        tok.token_program, tok.launchpad, tok.tags = jt.token_program, jt.launchpad, jt.tags
        tok.pair_created_at = tok.pair_created_at or jt.first_pool_created_at
        tok.last_discovered_at = at
        tok.classification, tok.classification_reason = c.classification, c.reason
        tok.classification_version = c.version
        if c.excluded and tok.tracking_status != TrackingStatus.EXCLUDED:
            tok.tracking_status, tok.tracking_reason = TrackingStatus.EXCLUDED, c.reason
            tok.tracking_changed_at = at
        elif not c.excluded and tok.tracking_status != TrackingStatus.ACTIVE:
            # Re-appearing in a discovery list means the token is active again.
            tok.tracking_status, tok.tracking_reason = TrackingStatus.ACTIVE, "rediscovered"
            tok.tracking_changed_at = at

    # ------------------------------------------------------------------ jupiter snapshots

    async def snapshots(self, run_id: int, status: TrackingStatus) -> JobResult:
        mints = await self._mints(status)
        result, failures, batches = JobResult(), 0, chunks(mints, self.cfg.jupiter_batch_size)
        for batch in batches:
            try:
                resp = await self.jup.search(batch)
            except ProviderError as exc:
                failures += 1
                log.warning("snapshot_batch_failed", size=len(batch), error=str(exc))
                continue
            result.api_credits_used += 1
            wanted = set(batch)
            tokens = [t for t in jupiter.parse_tokens(resp.payload) if t.mint in wanted]
            missing = len(wanted) - len({t.mint for t in tokens})
            if missing:
                log.warning("snapshot_tokens_missing", requested=len(wanted), missing=missing)
            async with self.factory() as session, session.begin():
                raw_id = await store_raw(session, resp, run_id)
                result.rows_written += await self._insert_snapshots(
                    session, tokens, resp.fetched_at, raw_id
                )
        if batches and failures == len(batches):
            raise AllRequestsFailed(f"all {len(batches)} snapshot batches failed")
        return result

    @staticmethod
    async def _insert_snapshots(
        session: AsyncSession, tokens: list[jupiter.JupiterToken], at: datetime, raw_id: int
    ) -> int:
        if not tokens:
            return 0
        snaps = [
            {
                "token_mint": t.mint,
                "source": jupiter.SOURCE,
                "observed_at": at,
                "source_updated_at": t.updated_at,
                "price_usd": t.price_usd,
                "market_cap": t.market_cap,
                "fdv": t.fdv,
                "liquidity_usd": t.liquidity_usd,
                "circ_supply": t.circ_supply,
                "total_supply": t.total_supply,
                "holder_count": t.holder_count,
                "organic_score": t.organic_score,
                "mint_authority_disabled": t.mint_authority_disabled,
                "freeze_authority_disabled": t.freeze_authority_disabled,
                "top_holders_pct": t.top_holders_pct,
                "dev_balance_pct": t.dev_balance_pct,
                "raw_observation_id": raw_id,
            }
            for t in tokens
        ]
        windows = [
            {
                "token_mint": t.mint,
                "source": jupiter.SOURCE,
                "observed_at": at,
                "window": w.window,
                "price_change_pct": w.price_change_pct,
                "liquidity_change_pct": w.liquidity_change_pct,
                "holder_change_pct": w.holder_change_pct,
                "volume_usd": w.volume_usd,
                "buy_volume_usd": w.buy_volume_usd,
                "sell_volume_usd": w.sell_volume_usd,
                "organic_buy_volume_usd": w.organic_buy_volume_usd,
                "organic_sell_volume_usd": w.organic_sell_volume_usd,
                "num_buys": w.num_buys,
                "num_sells": w.num_sells,
                "num_traders": w.num_traders,
                "num_net_buyers": w.num_net_buyers,
                "num_organic_buyers": w.num_organic_buyers,
            }
            for t in tokens
            for w in t.windows.values()
        ]
        await session.execute(insert(MarketSnapshot).values(snaps).on_conflict_do_nothing())
        if windows:
            await session.execute(
                insert(MarketWindowStats).values(windows).on_conflict_do_nothing()
            )
        return len(snaps) + len(windows)

    # ------------------------------------------------------------------ dex screener pools

    async def pools(self, run_id: int) -> JobResult:
        mints = await self._mints(TrackingStatus.ACTIVE)
        result, failures, batches = JobResult(), 0, chunks(mints, self.cfg.dexscreener_batch_size)
        for batch in batches:
            try:
                resp = await self.dex.tokens(batch)
            except ProviderError as exc:
                failures += 1
                log.warning("pools_batch_failed", size=len(batch), error=str(exc))
                continue
            result.api_credits_used += 1
            pairs = dexscreener.parse_pairs(resp.payload, set(batch))
            async with self.factory() as session, session.begin():
                raw_id = await store_raw(session, resp, run_id)
                result.rows_written += await self._store_pairs(session, pairs, resp, raw_id)
        if batches and failures == len(batches):
            raise AllRequestsFailed(f"all {len(batches)} pool batches failed")
        return result

    @staticmethod
    async def _store_pairs(
        session: AsyncSession,
        pairs: list[dexscreener.PairObservation],
        resp: ProviderResponse,
        raw_id: int,
    ) -> int:
        if not pairs:
            return 0
        await session.execute(
            insert(Pool)
            .values(
                [
                    {
                        "address": p.pool_address,
                        "token_mint": p.token_mint,
                        "dex_id": p.dex_id,
                        "quote_mint": p.quote_mint,
                        "quote_symbol": p.quote_symbol,
                        "labels": p.labels,
                        "pair_created_at": p.pair_created_at,
                        "first_seen_at": resp.fetched_at,
                    }
                    for p in pairs
                ]
            )
            .on_conflict_do_nothing()
        )
        await session.execute(
            insert(PoolSnapshot)
            .values(
                [
                    {
                        "pool_address": p.pool_address,
                        "observed_at": resp.fetched_at,
                        "token_mint": p.token_mint,
                        "price_usd": p.price_usd,
                        "price_native": p.price_native,
                        "liquidity_usd": p.liquidity_usd,
                        "reserve_base": p.reserve_base,
                        "reserve_quote": p.reserve_quote,
                        "market_cap": p.market_cap,
                        "fdv": p.fdv,
                        "volume_5m": p.volume["m5"],
                        "volume_1h": p.volume["h1"],
                        "volume_6h": p.volume["h6"],
                        "volume_24h": p.volume["h24"],
                        "buys_5m": p.buys["m5"],
                        "sells_5m": p.sells["m5"],
                        "buys_1h": p.buys["h1"],
                        "sells_1h": p.sells["h1"],
                        "buys_24h": p.buys["h24"],
                        "sells_24h": p.sells["h24"],
                        "price_change_1h": p.price_change["h1"],
                        "price_change_24h": p.price_change["h24"],
                        "raw_observation_id": raw_id,
                    }
                    for p in pairs
                ]
            )
            .on_conflict_do_nothing()
        )
        # Primary pool = deepest pool where the token is the base asset.
        best: dict[str, dexscreener.PairObservation] = {}
        for p in pairs:
            cur = best.get(p.token_mint)
            if cur is None or (p.liquidity_usd or 0) > (cur.liquidity_usd or 0):
                best[p.token_mint] = p
        for mint, p in best.items():
            await session.execute(
                update(Token)
                .where(Token.mint == mint, Token.primary_pool.is_distinct_from(p.pool_address))
                .values(primary_pool=p.pool_address)
            )
        return 2 * len(pairs)

    # ------------------------------------------------------------------ candles

    async def _liquid_active_tokens(
        self, min_liq: float, extra: Any = None, *, require_pool: bool = True
    ) -> list[tuple[str, str | None]]:
        """Active tokens with fresh liquidity >= min_liq, deepest first, as (mint, pool)."""
        since = utcnow() - timedelta(minutes=self.cfg.stale_after_minutes)
        async with self.factory() as session:
            q = select(Token.mint, Token.primary_pool).where(
                Token.tracking_status == TrackingStatus.ACTIVE
            )
            if require_pool:
                q = q.where(Token.primary_pool.is_not(None))
            if extra is not None:
                q = q.where(extra)
            rows = (await session.execute(q)).all()
            latest = await latest_jupiter_snapshots(session, since, [r[0] for r in rows])
        targets = [
            (mint, pool, latest[mint].liquidity_usd or 0)
            for mint, pool in rows
            if mint in latest and (latest[mint].liquidity_usd or 0) >= min_liq
        ]
        targets.sort(key=lambda t: t[2], reverse=True)
        return [(m, p) for m, p, _ in targets]

    async def candles(self, run_id: int) -> JobResult:
        targets = (await self._liquid_active_tokens(self.cfg.candles_min_liquidity_usd))[
            : self.cfg.candles_max_tokens
        ]
        result, failures = JobResult(), 0
        for mint, pool in targets:
            assert pool is not None  # require_pool=True
            try:
                n = await self._fetch_candles(run_id, mint, pool, self.cfg.candles_lookback, None)
            except ProviderError as exc:
                failures += 1
                log.warning("candles_failed", mint=mint, pool=pool, error=str(exc))
                continue
            result.api_credits_used += 1
            result.rows_written += n
        if targets and failures == len(targets):
            raise AllRequestsFailed(f"all {len(targets)} candle requests failed")
        return result

    async def backfill_candles(self, run_id: int) -> JobResult:
        """Point-in-time OHLCV history for newly tracked tokens (GeckoTerminal, 1000/page)."""
        targets = (
            await self._liquid_active_tokens(
                self.cfg.candles_min_liquidity_usd, Token.candles_backfilled_at.is_(None)
            )
        )[: self.cfg.backfill_tokens_per_run]
        pages = math.ceil(self.cfg.backfill_days * 288 / 1000)
        result = JobResult()
        for mint, pool in targets:
            assert pool is not None  # require_pool=True
            before: datetime | None = None
            try:
                for _ in range(pages):
                    n, earliest = await self._fetch_candles_page(run_id, mint, pool, before)
                    result.api_credits_used += 1
                    result.rows_written += n
                    if n < 1000 or earliest is None:
                        break
                    before = earliest
            except ProviderError as exc:
                log.warning("backfill_failed", mint=mint, pool=pool, error=str(exc))
                continue
            async with self.factory() as session, session.begin():
                await session.execute(
                    update(Token).where(Token.mint == mint).values(candles_backfilled_at=utcnow())
                )
        return result

    async def _fetch_candles(
        self, run_id: int, mint: str, pool: str, limit: int, before: datetime | None
    ) -> int:
        resp = await self.gecko.ohlcv_5m(pool, limit, before)
        return await self._store_candles(run_id, mint, pool, resp)

    async def _fetch_candles_page(
        self, run_id: int, mint: str, pool: str, before: datetime | None
    ) -> tuple[int, datetime | None]:
        resp = await self.gecko.ohlcv_5m(pool, 1000, before)
        candles = geckoterminal.parse_ohlcv(resp.payload)
        n = await self._store_candles(run_id, mint, pool, resp)
        return n, (candles[0].bucket if candles else None)

    async def _store_candles(
        self, run_id: int, mint: str, pool: str, resp: ProviderResponse
    ) -> int:
        candles = geckoterminal.parse_ohlcv(resp.payload)
        async with self.factory() as session, session.begin():
            await store_raw(session, resp, run_id)
            if not candles:
                return 0
            stmt = insert(Candle5m).values(
                [
                    {
                        "pool_address": pool,
                        "source": geckoterminal.SOURCE,
                        "bucket": c.bucket,
                        "token_mint": mint,
                        "open": c.open,
                        "high": c.high,
                        "low": c.low,
                        "close": c.close,
                        "volume_usd": c.volume_usd,
                        "is_complete": c.is_complete(resp.fetched_at),
                        "fetched_at": resp.fetched_at,
                    }
                    for c in candles
                ]
            )
            # A completed candle is final; only still-forming candles get overwritten.
            ex = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=["pool_address", "source", "bucket"],
                set_={
                    "open": ex.open,
                    "high": ex.high,
                    "low": ex.low,
                    "close": ex.close,
                    "volume_usd": ex.volume_usd,
                    "is_complete": ex.is_complete,
                    "fetched_at": ex.fetched_at,
                },
                where=Candle5m.is_complete.is_(False),
            )
            await session.execute(stmt)
        return len(candles)

    # ------------------------------------------------------------------ on-chain security

    async def security(self, run_id: int) -> JobResult:
        cutoff = utcnow() - timedelta(hours=self.cfg.security_refresh_hours)
        async with self.factory() as session:
            recent = select(TokenSecurity.token_mint).where(TokenSecurity.checked_at >= cutoff)
            q = (
                select(Token.mint)
                .where(
                    Token.tracking_status.in_([TrackingStatus.ACTIVE, TrackingStatus.DORMANT]),
                    Token.mint.not_in(recent),
                )
                .order_by(Token.first_seen_at)
                .limit(500)
            )
            mints = list((await session.scalars(q)).all())
        result, failures, batches = JobResult(), 0, chunks(mints, 100)
        for batch in batches:
            try:
                resp = await self.rpc.get_multiple_accounts(batch)
                accounts = helius.rpc_result(resp)["value"]
            except (ProviderError, KeyError, TypeError) as exc:
                failures += 1
                log.warning("security_batch_failed", size=len(batch), error=str(exc))
                continue
            result.api_credits_used += 1
            async with self.factory() as session, session.begin():
                raw_id = await store_raw(session, resp, run_id)
                for mint, account in zip(batch, accounts, strict=True):
                    sec = helius.parse_mint_account(mint, account)
                    session.add(
                        TokenSecurity(
                            token_mint=mint,
                            checked_at=resp.fetched_at,
                            status=sec.status,
                            token_program=sec.token_program,
                            mint_authority=sec.mint_authority,
                            freeze_authority=sec.freeze_authority,
                            supply_raw=sec.supply_raw,
                            decimals=sec.decimals,
                            extensions=sec.extensions,
                            risk_flags=sec.risk_flags,
                            raw_observation_id=raw_id,
                        )
                    )
                    result.rows_written += 1
        if batches and failures == len(batches):
            raise AllRequestsFailed(f"all {len(batches)} security batches failed")
        return result

    # ------------------------------------------------------------------ holders

    async def holders(self, run_id: int) -> JobResult:
        cutoff = utcnow() - timedelta(hours=self.cfg.holders_refresh_hours)
        recent = select(HolderSnapshot.token_mint).where(
            HolderSnapshot.observed_at >= cutoff, HolderSnapshot.error.is_(None)
        )
        targets = (
            await self._liquid_active_tokens(
                self.cfg.holders_min_liquidity_usd, Token.mint.not_in(recent), require_pool=False
            )
        )[: self.cfg.holders_tokens_per_run]
        result, failures = JobResult(), 0
        for mint, _pool in targets:
            try:
                snap, calls = await self._holder_snapshot(run_id, mint)
            except (ProviderError, KeyError, TypeError, ValueError) as exc:
                failures += 1
                calls = 0
                snap = HolderSnapshot(
                    token_mint=mint,
                    observed_at=utcnow(),
                    method_version=helius.HOLDER_METHOD_VERSION,
                    accounts=[],
                    error=f"{type(exc).__name__}: {exc}",
                )
                log.warning("holders_failed", mint=mint, error=snap.error)
            result.api_credits_used += calls
            async with self.factory() as session, session.begin():
                session.add(snap)
            result.rows_written += 1
        if targets and failures == len(targets):
            raise AllRequestsFailed(f"all {len(targets)} holder requests failed")
        return result

    async def _holder_snapshot(self, run_id: int, mint: str) -> tuple[HolderSnapshot, int]:
        largest_resp = await self.rpc.get_token_largest_accounts(mint)
        largest = helius.rpc_result(largest_resp)["value"]
        token_accounts = [a["address"] for a in largest]
        accounts_resp = await self.rpc.get_multiple_accounts([mint, *token_accounts])
        mint_acc, *tas = helius.rpc_result(accounts_resp)["value"]
        sec = helius.parse_mint_account(mint, mint_acc)
        if sec.supply_raw is None or sec.decimals is None:
            raise ValueError("mint supply unavailable")
        ta_owner: dict[str, str | None] = {}
        for address, acc in zip(token_accounts, tas, strict=True):
            info = (((acc or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
            ta_owner[address] = info.get("owner")
        owners = sorted({o for o in ta_owner.values() if o})
        owners_resp = await self.rpc.get_multiple_accounts(owners, parsed=False)
        owner_program = {
            o: (acc or {}).get("owner")
            for o, acc in zip(owners, helius.rpc_result(owners_resp)["value"], strict=True)
        }
        analysis = helius.analyze_holders(
            largest, ta_owner, owner_program, sec.supply_raw, sec.decimals
        )
        async with self.factory() as session, session.begin():
            for resp in (largest_resp, accounts_resp, owners_resp):
                await store_raw(session, resp, run_id)
        snap = HolderSnapshot(
            token_mint=mint,
            observed_at=largest_resp.fetched_at,
            method_version=helius.HOLDER_METHOD_VERSION,
            supply_ui=analysis.supply_ui,
            top10_pct_raw=analysis.top10_pct_raw,
            top10_pct_adjusted=analysis.top10_pct_adjusted,
            excluded_pct=analysis.excluded_pct,
            accounts=analysis.accounts,
        )
        return snap, 3

    # ------------------------------------------------------------------ swap quotes

    async def quotes(self, run_id: int) -> JobResult:
        since = utcnow() - timedelta(minutes=self.cfg.stale_after_minutes)
        async with self.factory() as session:
            latest = await latest_jupiter_snapshots(session, since)
            rows = await session.execute(
                select(Token.mint, Token.decimals).where(
                    Token.tracking_status == TrackingStatus.ACTIVE,
                    Token.mint.in_(list(latest)),
                    Token.decimals.is_not(None),
                )
            )
            decimals: dict[str, int] = {m: d for m, d in rows.all() if d is not None}
        targets = [
            s
            for m, s in latest.items()
            if m in decimals
            and s.price_usd
            and (s.liquidity_usd or 0) >= self.cfg.quote_min_liquidity_usd
            and (s.volume_24h_usd or 0) >= self.cfg.quote_min_volume_24h_usd
        ]
        result, failures = JobResult(), 0
        notional = self.cfg.quote_sell_usd
        for snap in targets:
            assert snap.price_usd is not None
            amount_raw = int(notional / snap.price_usd * 10 ** decimals[snap.mint])
            row = SwapQuote(
                token_mint=snap.mint,
                side="sell",
                notional_usd=notional,
                ref_price_usd=snap.price_usd,
                ref_price_observed_at=snap.observed_at,
                in_amount_raw=amount_raw,
            )
            raw_resp: ProviderResponse | None = None
            try:
                raw_resp = await self.jup.quote(snap.mint, jupiter.USDC_MINT, amount_raw)
                q = jupiter.parse_quote(raw_resp.payload)
                out_usd = q.out_amount_raw / 10**jupiter.USDC_DECIMALS
                row.quoted_at = raw_resp.fetched_at
                row.out_amount_raw = q.out_amount_raw
                row.out_usd = out_usd
                row.slippage_pct = (notional - out_usd) / notional * 100
                row.price_impact_pct = q.price_impact_pct
                row.route_labels = q.route_labels
                row.context_slot = q.context_slot
            except (ProviderError, KeyError, ValueError) as exc:
                failures += 1
                row.quoted_at = utcnow()
                row.error = f"{type(exc).__name__}: {exc}"
            result.api_credits_used += 1
            async with self.factory() as session, session.begin():
                if raw_resp is not None:
                    await store_raw(session, raw_resp, run_id)
                session.add(row)
            result.rows_written += 1
        if targets and failures == len(targets):
            raise AllRequestsFailed(f"all {len(targets)} quotes failed")
        return result

    # ------------------------------------------------------------------ tracking tiers

    async def update_tracking(self, run_id: int) -> JobResult:
        """Active tokens below the tracking floor for `dormant_after_hours` become dormant;
        dormant tokens back above the floor become active. Nothing is ever deleted."""
        now = utcnow()
        window_start = now - timedelta(hours=self.cfg.dormant_after_hours)
        s, w = MarketSnapshot, MarketWindowStats
        above_floor = (
            select(s.token_mint)
            .join(
                w,
                and_(
                    w.token_mint == s.token_mint,
                    w.source == s.source,
                    w.observed_at == s.observed_at,
                    w.window == "24h",
                ),
            )
            .where(
                s.source == jupiter.SOURCE,
                s.liquidity_usd >= self.cfg.tracking_min_liquidity_usd,
                w.volume_usd >= self.cfg.tracking_min_volume_24h_usd,
            )
        )
        result = JobResult()
        async with self.factory() as session, session.begin():
            to_dormant = await session.execute(
                update(Token)
                .where(
                    Token.tracking_status == TrackingStatus.ACTIVE,
                    func.coalesce(Token.tracking_changed_at, Token.first_seen_at) < window_start,
                    Token.mint.not_in(above_floor.where(s.observed_at >= window_start)),
                )
                .values(
                    tracking_status=TrackingStatus.DORMANT,
                    tracking_reason="below_tracking_floor",
                    tracking_changed_at=now,
                )
            )
            recent = now - timedelta(minutes=self.cfg.dormant_snapshot_minutes * 2)
            to_active = await session.execute(
                update(Token)
                .where(
                    Token.tracking_status == TrackingStatus.DORMANT,
                    Token.mint.in_(above_floor.where(s.observed_at >= recent)),
                )
                .values(
                    tracking_status=TrackingStatus.ACTIVE,
                    tracking_reason="above_tracking_floor",
                    tracking_changed_at=now,
                )
            )
        result.rows_written = to_dormant.rowcount + to_active.rowcount  # type: ignore[attr-defined]
        log.info(
            "tracking_updated",
            dormant=to_dormant.rowcount,  # type: ignore[attr-defined]
            reactivated=to_active.rowcount,  # type: ignore[attr-defined]
        )
        return result
