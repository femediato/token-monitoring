import pytest
from pydantic import ValidationError

from autolp.config import AppEnv, Settings


def test_defaults_are_dev_and_no_live_trading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    s = Settings(_env_file=None)
    assert s.app_env is AppEnv.DEV
    assert s.live_trading_enabled is False


def test_env_overrides_and_nested_provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("JUPITER__API_KEY", "secret-key")
    s = Settings(_env_file=None)
    assert s.app_env is AppEnv.TEST
    assert s.jupiter.api_key is not None
    assert s.jupiter.api_key.get_secret_value() == "secret-key"
    assert s.jupiter.base_url == "https://api.jup.ag"  # default kept
    assert "secret-key" not in repr(s)


def test_blank_api_key_means_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HELIUS__API_KEY", "")
    s = Settings(_env_file=None)
    assert s.helius.api_key is None
    assert s.helius.base_url == "https://mainnet.helius-rpc.com"


def test_invalid_env_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_live_trading_cannot_be_enabled() -> None:
    s = Settings(_env_file=None)
    with pytest.raises(ValidationError):
        s.live_trading_enabled = True
