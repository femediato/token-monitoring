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
    requests_per_second: float = 0.5


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


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.live_trading_enabled:
        raise RuntimeError("Live trading is not supported in v1")
    return settings
