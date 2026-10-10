import pytest

from autolp.providers.helius import (
    INCINERATOR,
    SYSTEM_PROGRAM,
    analyze_holders,
    parse_mint_account,
)
from tests.conftest import load_fixture

T22 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PE4D1zN7DgFHu8X"


def mint_account(info: dict, owner: str = T22) -> dict:
    return {"owner": owner, "data": {"parsed": {"type": "mint", "info": info}}}


def test_bonk_mint_is_ok() -> None:
    acc = load_fixture("helius_mint_account.json")["result"]["value"]
    sec = parse_mint_account("bonk", acc)
    assert sec.status == "ok" and sec.risk_flags == []
    assert sec.decimals == 5 and sec.supply_raw == 8799436445480264730


def test_missing_account_is_unknown_not_safe() -> None:
    sec = parse_mint_account("m", None)
    assert sec.status == "unknown" and sec.risk_flags == ["unparseable"]


def test_active_authorities_and_dangerous_extensions_flagged() -> None:
    info = {
        "mintAuthority": "Auth1",
        "freezeAuthority": "Auth2",
        "supply": "100",
        "decimals": 6,
        "extensions": [
            {"extension": "permanentDelegate", "state": {"delegate": "D"}},
            {"extension": "transferHook", "state": {"programId": "P"}},
            {
                "extension": "transferFeeConfig",
                "state": {
                    "newerTransferFee": {"transferFeeBasisPoints": 0},
                    "olderTransferFee": {"transferFeeBasisPoints": 100},
                },
            },
            {"extension": "pausableConfig", "state": {"authority": "A"}},
            {"extension": "metadataPointer", "state": {}},
        ],
    }
    sec = parse_mint_account("m", mint_account(info))
    assert sec.status == "risky"
    assert sec.risk_flags == [
        "mint_authority_active",
        "freeze_authority_active",
        "permanent_delegate",
        "transfer_hook",
        "transfer_fee",
        "pausable",
    ]
    assert sec.token_program == T22


def test_disabled_extensions_not_flagged() -> None:
    info = {
        "mintAuthority": None,
        "freezeAuthority": None,
        "supply": "1",
        "decimals": 0,
        "extensions": [
            {"extension": "permanentDelegate", "state": {"delegate": None}},
            {"extension": "transferHook", "state": {"programId": None}},
            {"extension": "transferFeeConfig", "state": {"newerTransferFee": {}}},
        ],
    }
    assert parse_mint_account("m", mint_account(info)).status == "ok"


def test_holder_exclusions() -> None:
    largest = [
        {"address": "pool_vault", "amount": "300"},
        {"address": "burn_acc", "amount": "200"},
        {"address": "whale", "amount": "100"},
        {"address": "unresolved", "amount": "50"},
        *({"address": f"w{i}", "amount": "10"} for i in range(10)),
    ]
    owners = {
        "pool_vault": "amm_pda",
        "burn_acc": INCINERATOR,
        "whale": "wallet1",
        **{f"w{i}": f"wallet_w{i}" for i in range(10)},
    }  # "unresolved" has no known owner
    programs = {"amm_pda": "AMMProgram111", "wallet1": SYSTEM_PROGRAM, INCINERATOR: None}
    a = analyze_holders(largest, owners, programs, supply_raw=1000, decimals=0)

    reasons = {x["owner"] or x["token_accounts"][0]: x["excluded"] for x in a.accounts}
    assert reasons["amm_pda"] == "program" and reasons[INCINERATOR] == "burn"
    assert reasons["wallet1"] is None and reasons["unresolved"] is None  # counted, not excluded
    assert a.top10_pct_raw == pytest.approx((300 + 200 + 100 + 50 + 6 * 10) / 10)
    # Adjusted: whale + unresolved + 8 small wallets over supply minus burn (800).
    assert a.top10_pct_adjusted == pytest.approx((100 + 50 + 80) / 800 * 100)
    assert a.top1_pct_adjusted == pytest.approx(100 / 800 * 100)
    assert a.excluded_pct == pytest.approx(50.0)


def test_holder_analysis_rejects_zero_supply() -> None:
    with pytest.raises(ValueError):
        analyze_holders([], {}, {}, supply_raw=0, decimals=0)
