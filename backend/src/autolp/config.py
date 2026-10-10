"""Environment-based configuration.

Secrets come only from the environment (or a gitignored .env file). Each
APP_ENV profile has its own database so tests never touch dev/prod data.
"""

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    DEV = "dev"
    TEST = "test"
    PROD = "prod"


class ProviderSettings(BaseSettings):
    """Per-provider endpoint, credentials and request budget."""

    base_url: str
    api_key: SecretStr | None = None
    requests_per_second: float = 1.0
    timeout_seconds: float = 15.0
    max_retries: int = 4


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

    jupiter: ProviderSettings = ProviderSettings(
        base_url="https://api.jup.ag", requests_per_second=1.0
    )
    dexscreener: ProviderSettings = ProviderSettings(
        base_url="https://api.dexscreener.com", requests_per_second=4.0
    )
    helius: ProviderSettings = ProviderSettings(
        base_url="https://mainnet.helius-rpc.com", requests_per_second=5.0
    )
    geckoterminal: ProviderSettings = ProviderSettings(
        base_url="https://api.geckoterminal.com/api/v2", requests_per_second=0.5
    )

    collector_heartbeat_seconds: int = 60


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.live_trading_enabled:
        raise RuntimeError("Live trading is not supported in v1")
    return settings
