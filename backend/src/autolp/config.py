"""Environment-based configuration.

Secrets come only from the environment (or a gitignored .env file). Each
APP_ENV profile has its own database so tests never touch dev/prod data.
"""

from enum import StrEnum
from functools import lru_cache

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    DEV = "dev"
    TEST = "test"
    PROD = "prod"


class ProviderSettings(BaseModel):
    """Per-provider endpoint, credentials and request budget."""

    base_url: str
    api_key: SecretStr | None = None
    requests_per_second: float = 1.0
    timeout_seconds: float = 15.0
    max_retries: int = 4
    rate_limited_backoff_seconds: float = 15.0  # minimum wait after an HTTP 429

    @field_validator("api_key", mode="before")
    @classmethod
    def _blank_key_is_none(cls, v: object) -> object:
        return None if v == "" else v


# Each provider has its own class so that setting one field (e.g. JUPITER__API_KEY)
# keeps the other defaults instead of replacing the whole nested object.
class JupiterSettings(ProviderSettings):
    base_url: str = "https://api.jup.ag"
    requests_per_second: float = 1.0


class DexScreenerSettings(ProviderSettings):
    base_url: str = "https://api.dexscreener.com"
    requests_per_second: float = 4.0


class HeliusSettings(ProviderSettings):
    base_url: str = "https://mainnet.helius-rpc.com"
    requests_per_second: float = 5.0


class GeckoTerminalSettings(ProviderSettings):
    base_url: str = "https://api.geckoterminal.com/api/v2"
    # Advertised free limit is 30/min, but in practice (2026-10-10) sustained use above
    # ~4/min drew constant 429s. Budget ~1.5/min on average, 6/min peak.
    requests_per_second: float = 0.1
    rate_limited_backoff_seconds: float = 30.0


class CollectionSettings(BaseModel):
    """Cadences, batch sizes and tracking floors for the collector (Phase 2)."""

    discovery_minutes: int = 15
    discovery_categories: list[str] = ["toptrending", "toptraded", "toporganicscore"]
    discovery_intervals: list[str] = ["5m", "1h", "6h", "24h"]
    discovery_limit: int = 100

    snapshot_minutes: int = 5
    dormant_snapshot_minutes: int = 60
    jupiter_batch_size: int = 100
    dexscreener_batch_size: int = 30

    # Eligible tokens first (the strategy universe); the job takes ~10 s per token.
    candles_minutes: int = 15
    candles_max_tokens: int = 24
    candles_lookback: int = 8  # 5-minute candles per refresh (40 min, overlapping the cadence)
    candles_min_liquidity_usd: float = 100_000
    backfill_days: int = 14
    backfill_tokens_per_run: int = 2

    security_refresh_hours: int = 24
    holders_refresh_hours: int = 24
    holders_priority_refresh_hours: int = 6  # tokens meeting the market eligibility rules
    holders_min_liquidity_usd: float = 100_000
    holders_tokens_per_run: int = 40

    quotes_minutes: int = 15
    quote_sell_usd: float = 1_000
    quote_reference_usd: float = 10  # paired small quote: the price reference for cost
    quote_min_liquidity_usd: float = 150_000
    quote_min_volume_24h_usd: float = 500_000

    # Tokens below this floor for `dormant_after_hours` are polled hourly instead of every
    # 5 minutes. They are never deleted, so failed tokens stay in the dataset.
    tracking_min_liquidity_usd: float = 25_000
    tracking_min_volume_24h_usd: float = 25_000
    dormant_after_hours: int = 24

    stale_after_minutes: int = 15

    # Fast prices (Jupiter Price V3) for the shortlist: tokens eligible in the last
    # `fast_prices_eligible_hours` plus open paper positions, one request per poll. On the
    # free Jupiter tier (1 req/s shared by every Jupiter job) 2 s fits; 1 s needs a Pro key.
    fast_prices_enabled: bool = True
    fast_prices_seconds: float = 2.0
    fast_prices_max_tokens: int = 50  # Price V3 limit per request
    fast_prices_shortlist_minutes: float = 5
    fast_prices_eligible_hours: float = 2
    fast_prices_flush_seconds: float = 10
    manual_exclude_mints: list[str] = []


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app_env: AppEnv = AppEnv.DEV
    log_level: str = "INFO"
    log_json: bool = True

    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://autolp:autolp@localhost:5432/autolp_dev"
    )
    db_pool_size: int = 5

    # Live trading is out of scope for v1; this flag exists so code can assert on it.
    live_trading_enabled: bool = Field(default=False, frozen=True)

    jupiter: JupiterSettings = JupiterSettings()
    dexscreener: DexScreenerSettings = DexScreenerSettings()
    helius: HeliusSettings = HeliusSettings()
    geckoterminal: GeckoTerminalSettings = GeckoTerminalSettings()

    collector_heartbeat_seconds: int = 60
    collection: CollectionSettings = CollectionSettings()


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.live_trading_enabled:
        raise RuntimeError("Live trading is not supported in v1")
    return settings
