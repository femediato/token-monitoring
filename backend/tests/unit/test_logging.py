import json

import pytest

from autolp.log import configure_logging, get_logger


def test_json_logs_have_utc_timestamp(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", json=True)
    get_logger("t").info("hello", token="mint1")
    line = capsys.readouterr().out.strip().splitlines()[-1]
    rec = json.loads(line)
    assert rec["event"] == "hello"
    assert rec["token"] == "mint1"
    assert rec["level"] == "info"
    assert rec["timestamp"].endswith("Z")


def test_level_filtering(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("WARNING", json=True)
    get_logger("t").info("hidden")
    assert "hidden" not in capsys.readouterr().out
