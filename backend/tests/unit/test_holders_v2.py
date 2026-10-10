import os

import pytest

from autolp.providers.helius import INCINERATOR, SYSTEM_PROGRAM, analyze_holders
from autolp.providers.solana_keys import b58decode, is_on_curve

RAYDIUM_AUTHORITY = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"  # a PDA
WALLET = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"


def test_on_curve() -> None:
    assert not is_on_curve(RAYDIUM_AUTHORITY)
    assert is_on_curve(WALLET)
    assert not is_on_curve("short")
    assert len(b58decode(WALLET)) == 32


def test_random_keys_are_about_half_on_curve() -> None:
    import hashlib

    from autolp.providers.solana_keys import _B58

    def enc(b: bytes) -> str:
        n, s = int.from_bytes(b, "big"), ""
        while n:
            n, r = divmod(n, 58)
            s = _B58[r] + s
        return s

    hits = sum(is_on_curve(enc(hashlib.sha256(os.urandom(8)).digest())) for _ in range(2000))
    assert 0.44 < hits / 2000 < 0.56


def test_v2_aggregates_by_owner_and_excludes_pdas_and_infrastructure() -> None:
    largest = [
        {"address": "vault", "amount": "300"},  # Raydium vault: owner is a PDA
        {"address": "burn", "amount": "100"},
        {"address": "cex", "amount": "150"},
        {"address": "w1a", "amount": "60"},  # same owner, two accounts
        {"address": "w1b", "amount": "40"},
        {"address": "w2", "amount": "50"},
    ]
    owners = {
        "vault": RAYDIUM_AUTHORITY,
        "burn": INCINERATOR,
        "cex": "ExchangeWallet",
        "w1a": WALLET,
        "w1b": WALLET,
        "w2": "OtherWallet",
    }
    programs = {RAYDIUM_AUTHORITY: SYSTEM_PROGRAM, WALLET: SYSTEM_PROGRAM}
    a = analyze_holders(largest, owners, programs, 1000, 0, {"ExchangeWallet": "Example CEX"})
    by_owner = {x["owner"]: x for x in a.accounts}
    assert by_owner[RAYDIUM_AUTHORITY]["excluded"] == "program"  # System-owned, but off-curve
    assert by_owner[INCINERATOR]["excluded"] == "burn"
    assert by_owner["ExchangeWallet"]["excluded"] == "infrastructure:Example CEX"
    assert by_owner[WALLET]["amount_raw"] == 100 and by_owner[WALLET]["token_accounts"] == [
        "w1a",
        "w1b",
    ]
    assert a.top1_pct_adjusted == pytest.approx(100 / 900 * 100)
    assert a.top10_pct_adjusted == pytest.approx(150 / 900 * 100)
