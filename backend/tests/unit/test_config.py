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
    monkeypatch.setenv("JUPITER__BASE_URL", "https://api.jup.ag")
    s = Settings(_env_file=None)
    assert s.app_env is AppEnv.TEST
    assert s.jupiter.api_key is not None
    assert s.jupiter.api_key.get_secret_value() == "secret-key"
    assert "secret-key" not in repr(s)


def test_invalid_env_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_live_trading_cannot_be_enabled() -> None:
    s = Settings(_env_file=None)
    with pytest.raises(ValidationError):
        s.live_trading_enabled = True
