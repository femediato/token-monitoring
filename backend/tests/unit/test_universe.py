import pytest

from autolp.universe import CLASSIFIER_VERSION, classify


@pytest.mark.parametrize(
    ("mint", "tags", "launchpad", "expected", "reason"),
    [
        ("m", ["verified", "meme"], None, "meme", "tag:meme"),
        ("m", ["verified"], "pump.fun", "meme", "launchpad:pump.fun"),
        ("m", ["verified"], None, "meme_candidate", "no_exclusion_matched"),
        ("m", ["verified", "stocks", "rwa"], None, "excluded", "tag:stocks:tokenized_asset"),
        ("m", ["meme", "defi"], "pump.fun", "excluded", "tag:defi:protocol_token"),
        ("m", ["lst"], None, "excluded", "tag:lst:liquid_staking"),
        (
            "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            ["meme"],
            None,
            "excluded",
            "mint:stablecoin",
        ),
    ],
)
def test_classify(
    mint: str, tags: list[str], launchpad: str | None, expected: str, reason: str
) -> None:
    c = classify(mint, tags, launchpad)
    assert (c.classification, c.reason, c.version) == (expected, reason, CLASSIFIER_VERSION)


def test_manual_exclusion_wins() -> None:
    c = classify("bad", ["meme"], "pump.fun", frozenset({"bad"}))
    assert c.excluded and c.reason == "manual_exclude"
