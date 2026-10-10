"""Meme-coin universe classification (versioned).

Jupiter's `meme` tag is sparse (most meme coins lack it), so classification works by
exclusion: anything tagged or listed as a non-meme asset class is excluded with a reason;
the rest are meme coins (positively identified) or meme candidates (nothing excluded them).
"""

from dataclasses import dataclass

CLASSIFIER_VERSION = "v1"

EXCLUDED_TAGS: dict[str, str] = {
    "stocks": "tokenized_asset",
    "xstocks": "tokenized_asset",
    "equities": "tokenized_asset",
    "prestocks": "tokenized_asset",
    "pre-ipo": "tokenized_asset",
    "rwa": "tokenized_asset",
    "ondo": "tokenized_asset",
    "stable": "stablecoin",
    "lst": "liquid_staking",
    "original-lst": "liquid_staking",
    "yield": "yield_bearing",
    "major": "major_asset",
    "defi": "protocol_token",
    "infra": "protocol_token",
}

EXCLUDED_MINTS: dict[str, str] = {
    "So11111111111111111111111111111111111111112": "major_asset",  # wrapped SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "stablecoin",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "stablecoin",  # USDT
    "USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB": "stablecoin",  # USD1
    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh": "major_asset",  # wrapped BTC (Wormhole)
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs": "major_asset",  # wrapped ETH (Wormhole)
    "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij": "major_asset",  # cbBTC
}

MEME_LAUNCHPADS = {
    "pump.fun",
    "letsbonk.fun",
    "moonshot",
    "bags.fun",
    "met-dbc",
    "raydium-launchlab",
    "jup-studio",
    "believe",
    "boop.fun",
}


@dataclass(frozen=True)
class Classification:
    classification: str  # meme | meme_candidate | excluded
    reason: str
    version: str = CLASSIFIER_VERSION

    @property
    def excluded(self) -> bool:
        return self.classification == "excluded"


def classify(
    mint: str, tags: list[str], launchpad: str | None, manual_excludes: frozenset[str] = frozenset()
) -> Classification:
    if mint in manual_excludes:
        return Classification("excluded", "manual_exclude")
    if mint in EXCLUDED_MINTS:
        return Classification("excluded", f"mint:{EXCLUDED_MINTS[mint]}")
    for tag in tags:
        if tag in EXCLUDED_TAGS:
            return Classification("excluded", f"tag:{tag}:{EXCLUDED_TAGS[tag]}")
    if "meme" in tags:
        return Classification("meme", "tag:meme")
    if launchpad in MEME_LAUNCHPADS:
        return Classification("meme", f"launchpad:{launchpad}")
    return Classification("meme_candidate", "no_exclusion_matched")
