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
    text,
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


class TrackingEvent(Base):
    """Every change of a token's tracking status, with the reason (audit trail)."""

    __tablename__ = "tracking_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    token_mint: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime]
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(128))
    collector_run_id: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (
        Index("ix_tracking_events_at", "at"),
        Index("ix_tracking_events_mint_at", "token_mint", "at"),
    )


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
    organic_score_label: Mapped[str | None] = mapped_column(String(16))
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
    top1_pct_adjusted: Mapped[float | None] = mapped_column(Double)
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
    # Paired reference quote taken at the same moment for a small notional; execution cost
    # is measured against it, so a stale reference price cannot distort the result.
    ref_notional_usd: Mapped[float | None] = mapped_column(Double)
    ref_out_usd: Mapped[float | None] = mapped_column(Double)
    cost_pct: Mapped[float | None] = mapped_column(Double)
    price_impact_pct: Mapped[float | None] = mapped_column(Double)
    route_labels: Mapped[list[str] | None] = mapped_column(JSONB)
    context_slot: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_swap_quotes_mint_quoted", "token_mint", "quoted_at"),)


class ScoreEvaluation(Base):
    """Point-in-time eligibility, features and scores for one token (hypertable).

    Every active token is evaluated; ineligible tokens carry reason codes and no scores.
    `features` holds the raw inputs so every score can be recomputed and audited.
    """

    __tablename__ = "score_evaluations"

    token_mint: Mapped[str] = mapped_column(String(64), primary_key=True)
    evaluated_at: Mapped[datetime] = mapped_column(primary_key=True)
    model_version: Mapped[str] = mapped_column(String(16), primary_key=True)
    hourly: Mapped[bool] = mapped_column(Boolean)
    eligible: Mapped[bool] = mapped_column(Boolean)
    critical: Mapped[bool] = mapped_column(Boolean)  # failed a critical safety check
    reasons: Mapped[list[str]] = mapped_column(JSONB)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB)
    components: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    activity: Mapped[float | None] = mapped_column(Double)
    momentum: Mapped[float | None] = mapped_column(Double)
    liquidity: Mapped[float | None] = mapped_column(Double)
    buying: Mapped[float | None] = mapped_column(Double)
    risk: Mapped[float | None] = mapped_column(Double)
    total: Mapped[float | None] = mapped_column(Double)
    coverage: Mapped[float | None] = mapped_column(Double)  # share of total weight with data
    group: Mapped[str | None] = mapped_column(String(16))
    rank: Mapped[int | None] = mapped_column(SmallInteger)

    __table_args__ = (Index("ix_score_evaluations_at", "evaluated_at"),)


class WatchlistMember(Base):
    """Current watchlist membership (history lives in watchlist_events)."""

    __tablename__ = "watchlist_members"

    token_mint: Mapped[str] = mapped_column(String(64), primary_key=True)
    group: Mapped[str] = mapped_column(String(16))
    joined_at: Mapped[datetime]
    entry_score: Mapped[float] = mapped_column(Double)
    model_version: Mapped[str] = mapped_column(String(16))


class WatchlistEvent(Base):
    """Every watchlist inclusion and removal with its reason code."""

    __tablename__ = "watchlist_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    at: Mapped[datetime]
    token_mint: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(8))  # add | remove
    group: Mapped[str | None] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(128))
    score: Mapped[float | None] = mapped_column(Double)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    model_version: Mapped[str] = mapped_column(String(16))

    __table_args__ = (Index("ix_watchlist_events_at", "at"),)


class EarlyScore(Base):
    """Early-detection signals for every evaluated token, every run (hypertable)."""

    __tablename__ = "early_scores"

    token_mint: Mapped[str] = mapped_column(String(64), primary_key=True)
    evaluated_at: Mapped[datetime] = mapped_column(primary_key=True)
    model_version: Mapped[str] = mapped_column(String(16), primary_key=True)
    score: Mapped[float] = mapped_column(Double)
    state: Mapped[str] = mapped_column(String(16))
    signals: Mapped[dict[str, Any]] = mapped_column(JSONB)
    eligible: Mapped[bool] = mapped_column(Boolean)


class EarlyAlert(Base):
    """An early-opportunity alert: a research signal, never an order."""

    __tablename__ = "early_alerts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    token_mint: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime]
    model_version: Mapped[str] = mapped_column(String(16))
    score: Mapped[float] = mapped_column(Double)
    state: Mapped[str] = mapped_column(String(16))
    signals: Mapped[dict[str, Any]] = mapped_column(JSONB)
    price_usd: Mapped[float | None] = mapped_column(Double)
    market_cap: Mapped[float | None] = mapped_column(Double)
    liquidity_usd: Mapped[float | None] = mapped_column(Double)
    # How far price had already moved before the alert (was it detected *after* the move?)
    pre_return_1h_pct: Mapped[float | None] = mapped_column(Double)
    pre_return_4h_pct: Mapped[float | None] = mapped_column(Double)
    # Executable $1,000 buy quote taken right after the alert.
    entry_quoted_at: Mapped[datetime | None]
    entry_cost_pct: Mapped[float | None] = mapped_column(Double)
    entry_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_early_alerts_at", "at"),
        Index("ix_early_alerts_mint_at", "token_mint", "at"),
    )


class AlertObservation(Base):
    """Forward outcome of an alerted token or one of its matched controls."""

    __tablename__ = "alert_observations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    alert_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role: Mapped[str] = mapped_column(String(8))  # alert | control
    token_mint: Mapped[str] = mapped_column(String(64))
    start_at: Mapped[datetime]
    start_price: Mapped[float | None] = mapped_column(Double)
    match_distance: Mapped[float | None] = mapped_column(Double)
    ret_1h_pct: Mapped[float | None] = mapped_column(Double)
    ret_4h_pct: Mapped[float | None] = mapped_column(Double)
    ret_12h_pct: Mapped[float | None] = mapped_column(Double)
    ret_24h_pct: Mapped[float | None] = mapped_column(Double)
    mfe_24h_pct: Mapped[float | None] = mapped_column(Double)  # max favourable excursion
    mae_24h_pct: Mapped[float | None] = mapped_column(Double)  # max adverse excursion
    missing: Mapped[list[str] | None] = mapped_column(JSONB)  # horizons with no price data
    completed_at: Mapped[datetime | None]


class StrategySignal(Base):
    """One strategy's entry check for one token at one live evaluation (hypertable).

    Every check is stored (True / False / None = input missing), with the setup levels when
    all checks pass. A setup is a paper decision only; it never places an order.
    """

    __tablename__ = "strategy_signals"

    token_mint: Mapped[str] = mapped_column(String(64), primary_key=True)
    evaluated_at: Mapped[datetime] = mapped_column(primary_key=True)
    strategy: Mapped[str] = mapped_column(String(16), primary_key=True)
    version: Mapped[str] = mapped_column(String(16))
    setup: Mapped[bool] = mapped_column(Boolean)
    checks: Mapped[dict[str, Any]] = mapped_column(JSONB)
    failed: Mapped[list[str]] = mapped_column(JSONB)
    values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    last_candle_at: Mapped[datetime | None]
    entry_ref: Mapped[float | None] = mapped_column(Double)
    stop: Mapped[float | None] = mapped_column(Double)
    target: Mapped[float | None] = mapped_column(Double)
    max_hold_hours: Mapped[float | None] = mapped_column(Double)
    cost_pct: Mapped[float | None] = mapped_column(Double)

    __table_args__ = (Index("ix_strategy_signals_at", "evaluated_at"),)


class BacktestRun(Base):
    """A stored backtest report (docs/BACKTESTING.md). `report` holds everything needed to
    audit it: parameters, splits, trades, equity curves, baselines and digests."""

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    version: Mapped[str] = mapped_column(String(16))
    strategy_version: Mapped[str] = mapped_column(String(16))
    model_version: Mapped[str] = mapped_column(String(16))
    window_start: Mapped[datetime]
    window_end: Mapped[datetime]
    sealed: Mapped[bool] = mapped_column(Boolean)  # holdout not simulated
    overrides: Mapped[dict[str, Any]] = mapped_column(JSONB)
    input_digest: Mapped[str] = mapped_column(String(64))
    result_digest: Mapped[str] = mapped_column(String(64))
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)


class PaperAccount(Base):
    """The virtual paper-trading account (docs/PAPER_TRADING.md). Cash changes only with a
    paper fill, so it can be reconciled from `paper_fills` at any time."""

    __tablename__ = "paper_accounts"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    starting_equity_usd: Mapped[float] = mapped_column(Double)
    cash_usd: Mapped[float] = mapped_column(Double)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)  # versions and parameters at creation


class PaperPosition(Base):
    """One paper order from signal to final exit. Status: pending (ordered, not filled),
    open, closed or cancelled. Levels and sizing are copied from the decision."""

    __tablename__ = "paper_positions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12))
    strategy: Mapped[str] = mapped_column(String(16))
    token_mint: Mapped[str] = mapped_column(String(64))
    strategy_version: Mapped[str] = mapped_column(String(16))
    paper_version: Mapped[str] = mapped_column(String(16))
    # Decision
    signal_at: Mapped[datetime]
    signal_bar: Mapped[datetime]
    entry_ref: Mapped[float] = mapped_column(Double)
    stop: Mapped[float] = mapped_column(Double)
    target: Mapped[float] = mapped_column(Double)
    max_hold_hours: Mapped[float] = mapped_column(Double)
    quoted_cost_pct: Mapped[float] = mapped_column(Double)  # the estimate used for sizing
    size_usd: Mapped[float] = mapped_column(Double)  # cash committed, incl. entry fee
    planned_risk_usd: Mapped[float] = mapped_column(Double)
    sizing_binding: Mapped[str] = mapped_column(String(32))
    # Entry
    entry_at: Mapped[datetime | None]
    entry_price: Mapped[float | None] = mapped_column(Double)  # reference price, before costs
    entry_cost_pct: Mapped[float | None] = mapped_column(Double)  # realised, vs. reference
    qty: Mapped[float | None] = mapped_column(Double)
    exit_by: Mapped[datetime | None]
    # While open
    last_price: Mapped[float | None] = mapped_column(Double)
    last_price_at: Mapped[datetime | None]
    pending_exit: Mapped[str | None] = mapped_column(String(16))  # trigger whose swap failed
    exit_attempts: Mapped[int] = mapped_column(Integer, default=0)
    # Stop management (paper-v2; NULL before): `stop` above is the current stop.
    initial_stop: Mapped[float | None] = mapped_column(Double)
    atr: Mapped[float | None] = mapped_column(Double)  # at the signal, price units
    high_price: Mapped[float | None] = mapped_column(Double)  # highest mark since entry
    # Exit
    exit_at: Mapped[datetime | None]
    exit_reason: Mapped[str | None] = mapped_column(String(24))
    exit_trigger_price: Mapped[float | None] = mapped_column(Double)
    exit_price: Mapped[float | None] = mapped_column(Double)  # reference price, before costs
    exit_cost_pct: Mapped[float | None] = mapped_column(Double)
    proceeds_usd: Mapped[float | None] = mapped_column(Double)
    pnl_usd: Mapped[float | None] = mapped_column(Double)
    cancel_reason: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        Index("ix_paper_positions_account_status", "account_id", "status"),
        Index("ix_paper_positions_signal_at", "signal_at"),
    )


class PaperFill(Base):
    """One executable Jupiter quote used as a paper fill, or the failed attempt to get one.

    Each fill pairs the full-size quote with a small reference quote taken immediately
    before; the cost is measured between the two, as for `swap_quotes`. `model_price` is
    what the backtest fill model would have assumed for the same order, filled in once the
    candles have arrived, so the two can be compared."""

    __tablename__ = "paper_fills"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    position_id: Mapped[int] = mapped_column(BigInteger)
    side: Mapped[str] = mapped_column(String(4))  # buy | sell
    decided_at: Mapped[datetime]  # when the order or exit was triggered
    quoted_at: Mapped[datetime | None]
    in_amount_raw: Mapped[int | None] = mapped_column(Numeric(40, 0))
    out_amount_raw: Mapped[int | None] = mapped_column(Numeric(40, 0))
    usd: Mapped[float | None] = mapped_column(Double)  # USDC paid (buy) or received (sell)
    qty: Mapped[float | None] = mapped_column(Double)  # tokens received (buy) or sold (sell)
    ref_price: Mapped[float | None] = mapped_column(Double)  # USD per token, small quote
    fill_price: Mapped[float | None] = mapped_column(Double)  # USD per token, full quote
    cost_pct: Mapped[float | None] = mapped_column(Double)
    fee_usd: Mapped[float | None] = mapped_column(Double)
    price_impact_pct: Mapped[float | None] = mapped_column(Double)
    route_labels: Mapped[list[str] | None] = mapped_column(JSONB)
    trigger_price: Mapped[float | None] = mapped_column(Double)  # entry_ref, stop or target
    raw_ids: Mapped[list[int] | None] = mapped_column(JSONB)  # raw_observations of the quotes
    error: Mapped[str | None] = mapped_column(Text)
    model_price: Mapped[float | None] = mapped_column(Double)
    model_note: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (Index("ix_paper_fills_position", "position_id"),)


class PaperEvent(Base):
    """Audit log of the paper engine: every signal, rejection, order, fill, failed swap,
    exit trigger and exit. A signal is recorded once per strategy, token and signal candle,
    which also makes the engine idempotent across restarts."""

    __tablename__ = "paper_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer)
    position_id: Mapped[int | None] = mapped_column(BigInteger)
    at: Mapped[datetime]
    kind: Mapped[str] = mapped_column(String(16))
    strategy: Mapped[str | None] = mapped_column(String(16))
    token_mint: Mapped[str | None] = mapped_column(String(64))
    signal_bar: Mapped[datetime | None]
    reason: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        Index("ix_paper_events_account_at", "account_id", "at"),
        Index("ix_paper_events_position", "position_id"),
        Index(
            "uq_paper_events_signal",
            "account_id",
            "strategy",
            "token_mint",
            "signal_bar",
            unique=True,
            postgresql_where=text("kind = 'signal'"),
        ),
    )


class PaperEquity(Base):
    """Marked paper equity at every engine tick: the paper equity curve."""

    __tablename__ = "paper_equity"

    account_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(primary_key=True)
    cash_usd: Mapped[float] = mapped_column(Double)
    positions_usd: Mapped[float] = mapped_column(Double)
    equity_usd: Mapped[float] = mapped_column(Double)
    open_positions: Mapped[int] = mapped_column(Integer)
