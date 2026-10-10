"""ORM models. Every timestamp column is timezone-aware and stored in UTC."""

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Double,
    Enum,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autolp.db.base import Base, utcnow


class RunStatus(enum.StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class TrackingStatus(enum.StrEnum):
    ACTIVE = "active"  # polled every snapshot interval
    DORMANT = "dormant"  # below tracking floor; polled hourly, never deleted
    EXCLUDED = "excluded"  # not a meme coin (stock, stablecoin, LST, ...); not polled


class CollectorRun(Base):
    """One execution of a scheduled job; the basis for gap and staleness monitoring."""

    __tablename__ = "collector_runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status", values_callable=lambda e: [m.value for m in e]),
        default=RunStatus.RUNNING,
    )
    rows_written: Mapped[int] = mapped_column(Integer, default=0)
    api_credits_used: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_collector_runs_job_started", "job", "started_at"),)


class RawObservation(Base):
    """Verbatim provider response, persisted before any normalization (hypertable)."""

    __tablename__ = "raw_observations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(primary_key=True, default=utcnow)
    source: Mapped[str] = mapped_column(String(32))
    endpoint: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    http_status: Mapped[int] = mapped_column(SmallInteger)
    payload: Mapped[Any] = mapped_column(JSONB)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    collector_run_id: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (Index("ix_raw_observations_source_fetched", "source", "fetched_at"),)


class Token(Base):
    """A token identified by its exact mint address."""

    __tablename__ = "tokens"

    mint: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(256))
    decimals: Mapped[int | None] = mapped_column(SmallInteger)
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    pair_created_at: Mapped[datetime | None]
    discovery_source: Mapped[str] = mapped_column(String(32))
    classification: Mapped[str | None] = mapped_column(String(32))
    classification_reason: Mapped[str | None] = mapped_column(String(128))
    classification_version: Mapped[str | None] = mapped_column(String(16))
    tracking_status: Mapped[str] = mapped_column(String(16), default=TrackingStatus.ACTIVE)
    tracking_reason: Mapped[str | None] = mapped_column(String(128))
    tracking_changed_at: Mapped[datetime | None]
    token_program: Mapped[str | None] = mapped_column(String(64))
    launchpad: Mapped[str | None] = mapped_column(String(64))
    tags: Mapped[list[str] | None] = mapped_column(JSONB)
    last_discovered_at: Mapped[datetime | None]
    primary_pool: Mapped[str | None] = mapped_column(String(64))
    candles_backfilled_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_tokens_tracking_status", "tracking_status"),)


class TokenDiscovery(Base):
    """Why a token entered (or re-entered) the universe: one row per list appearance."""

    __tablename__ = "token_discoveries"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    token_mint: Mapped[str] = mapped_column(String(64))
    discovered_at: Mapped[datetime]
    source: Mapped[str] = mapped_column(String(32))
    list_name: Mapped[str] = mapped_column(String(64))
    rank: Mapped[int | None] = mapped_column(SmallInteger)
    collector_run_id: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (Index("ix_token_discoveries_mint_at", "token_mint", "discovered_at"),)


class MarketSnapshot(Base):
    """Point-in-time token-level market state from one provider (hypertable)."""

    __tablename__ = "market_snapshots"

    token_mint: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(primary_key=True)
    source_updated_at: Mapped[datetime | None]
    price_usd: Mapped[float | None] = mapped_column(Double)
    market_cap: Mapped[float | None] = mapped_column(Double)
    fdv: Mapped[float | None] = mapped_column(Double)
    liquidity_usd: Mapped[float | None] = mapped_column(Double)
    circ_supply: Mapped[float | None] = mapped_column(Double)
    total_supply: Mapped[float | None] = mapped_column(Double)
    holder_count: Mapped[int | None] = mapped_column(BigInteger)
    organic_score: Mapped[float | None] = mapped_column(Double)
    mint_authority_disabled: Mapped[bool | None] = mapped_column(Boolean)
    freeze_authority_disabled: Mapped[bool | None] = mapped_column(Boolean)
    top_holders_pct: Mapped[float | None] = mapped_column(Double)
    dev_balance_pct: Mapped[float | None] = mapped_column(Double)
    raw_observation_id: Mapped[int | None] = mapped_column(BigInteger)


class MarketWindowStats(Base):
    """Rolling-window activity stats (5m/1h/6h/24h) captured with a snapshot (hypertable)."""

    __tablename__ = "market_window_stats"

    token_mint: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(primary_key=True)
    window: Mapped[str] = mapped_column(String(8), primary_key=True)
    price_change_pct: Mapped[float | None] = mapped_column(Double)
    liquidity_change_pct: Mapped[float | None] = mapped_column(Double)
    holder_change_pct: Mapped[float | None] = mapped_column(Double)
    volume_usd: Mapped[float | None] = mapped_column(Double)
    buy_volume_usd: Mapped[float | None] = mapped_column(Double)
    sell_volume_usd: Mapped[float | None] = mapped_column(Double)
    organic_buy_volume_usd: Mapped[float | None] = mapped_column(Double)
    organic_sell_volume_usd: Mapped[float | None] = mapped_column(Double)
    num_buys: Mapped[int | None] = mapped_column(Integer)
    num_sells: Mapped[int | None] = mapped_column(Integer)
    num_traders: Mapped[int | None] = mapped_column(Integer)
    num_net_buyers: Mapped[int | None] = mapped_column(Integer)
    num_organic_buyers: Mapped[int | None] = mapped_column(Integer)


class Pool(Base):
    __tablename__ = "pools"

    address: Mapped[str] = mapped_column(String(64), primary_key=True)
    token_mint: Mapped[str] = mapped_column(String(64), index=True)
    dex_id: Mapped[str | None] = mapped_column(String(64))
    quote_mint: Mapped[str | None] = mapped_column(String(64))
    quote_symbol: Mapped[str | None] = mapped_column(String(32))
    labels: Mapped[list[str] | None] = mapped_column(JSONB)
    pair_created_at: Mapped[datetime | None]
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)


class PoolSnapshot(Base):
    """Pool-level state from DEX Screener, including reserves (hypertable)."""

    __tablename__ = "pool_snapshots"

    pool_address: Mapped[str] = mapped_column(String(64), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(primary_key=True)
    token_mint: Mapped[str] = mapped_column(String(64))
    price_usd: Mapped[float | None] = mapped_column(Double)
    price_native: Mapped[float | None] = mapped_column(Double)
    liquidity_usd: Mapped[float | None] = mapped_column(Double)
    reserve_base: Mapped[float | None] = mapped_column(Double)
    reserve_quote: Mapped[float | None] = mapped_column(Double)
    market_cap: Mapped[float | None] = mapped_column(Double)
    fdv: Mapped[float | None] = mapped_column(Double)
    volume_5m: Mapped[float | None] = mapped_column(Double)
    volume_1h: Mapped[float | None] = mapped_column(Double)
    volume_6h: Mapped[float | None] = mapped_column(Double)
    volume_24h: Mapped[float | None] = mapped_column(Double)
    buys_5m: Mapped[int | None] = mapped_column(Integer)
    sells_5m: Mapped[int | None] = mapped_column(Integer)
    buys_1h: Mapped[int | None] = mapped_column(Integer)
    sells_1h: Mapped[int | None] = mapped_column(Integer)
    buys_24h: Mapped[int | None] = mapped_column(Integer)
    sells_24h: Mapped[int | None] = mapped_column(Integer)
    price_change_1h: Mapped[float | None] = mapped_column(Double)
    price_change_24h: Mapped[float | None] = mapped_column(Double)
    raw_observation_id: Mapped[int | None] = mapped_column(BigInteger)


class Candle5m(Base):
    """5-minute OHLCV for a pool. `is_complete` is false while the bucket is still open."""

    __tablename__ = "candles_5m"

    pool_address: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    bucket: Mapped[datetime] = mapped_column(primary_key=True)
    token_mint: Mapped[str] = mapped_column(String(64))
    open: Mapped[float] = mapped_column(Double)
    high: Mapped[float] = mapped_column(Double)
    low: Mapped[float] = mapped_column(Double)
    close: Mapped[float] = mapped_column(Double)
    volume_usd: Mapped[float] = mapped_column(Double)
    is_complete: Mapped[bool] = mapped_column(Boolean)
    fetched_at: Mapped[datetime]


class TokenSecurity(Base):
    """On-chain mint permissions and risky Token-2022 extensions, append-only history."""

    __tablename__ = "token_security"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    token_mint: Mapped[str] = mapped_column(String(64))
    checked_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(16))  # ok | risky | unknown
    token_program: Mapped[str | None] = mapped_column(String(64))
    mint_authority: Mapped[str | None] = mapped_column(String(64))
    freeze_authority: Mapped[str | None] = mapped_column(String(64))
    supply_raw: Mapped[int | None] = mapped_column(Numeric(40, 0))
    decimals: Mapped[int | None] = mapped_column(SmallInteger)
    extensions: Mapped[list[str] | None] = mapped_column(JSONB)
    risk_flags: Mapped[list[str]] = mapped_column(JSONB)
    raw_observation_id: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (Index("ix_token_security_mint_checked", "token_mint", "checked_at"),)


class HolderSnapshot(Base):
    """Largest-holder concentration with documented exclusions (method versioned)."""

    __tablename__ = "holder_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    token_mint: Mapped[str] = mapped_column(String(64))
    observed_at: Mapped[datetime]
    method_version: Mapped[str] = mapped_column(String(16))
    supply_ui: Mapped[float | None] = mapped_column(Double)
    top10_pct_raw: Mapped[float | None] = mapped_column(Double)
    top10_pct_adjusted: Mapped[float | None] = mapped_column(Double)
    excluded_pct: Mapped[float | None] = mapped_column(Double)
    accounts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_holder_snapshots_mint_observed", "token_mint", "observed_at"),)


class SwapQuote(Base):
    """Executable Jupiter quote used to measure realistic slippage. Failures are recorded too."""

    __tablename__ = "swap_quotes"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    token_mint: Mapped[str] = mapped_column(String(64))
    quoted_at: Mapped[datetime]
    side: Mapped[str] = mapped_column(String(4))
    notional_usd: Mapped[float] = mapped_column(Double)
    ref_price_usd: Mapped[float | None] = mapped_column(Double)
    ref_price_observed_at: Mapped[datetime | None]
    in_amount_raw: Mapped[int | None] = mapped_column(Numeric(40, 0))
    out_amount_raw: Mapped[int | None] = mapped_column(Numeric(40, 0))
    out_usd: Mapped[float | None] = mapped_column(Double)
    slippage_pct: Mapped[float | None] = mapped_column(Double)
    price_impact_pct: Mapped[float | None] = mapped_column(Double)
    route_labels: Mapped[list[str] | None] = mapped_column(JSONB)
    context_slot: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_swap_quotes_mint_quoted", "token_mint", "quoted_at"),)
