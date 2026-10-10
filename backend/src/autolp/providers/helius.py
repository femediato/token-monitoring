"""Helius Solana RPC: mint permissions, Token-2022 extensions and largest holders.

Missing or unparseable on-chain data is reported as `unknown`, never as safe.
"""

from dataclasses import dataclass
from typing import Any

from autolp.config import HeliusSettings
from autolp.providers.http import ProviderClient, ProviderError, ProviderResponse
from autolp.providers.solana_keys import is_pda

SOURCE = "helius"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
INCINERATOR = "1nc1nerator11111111111111111111111111111111"
HOLDER_METHOD_VERSION = "v2"


class RpcError(ProviderError):
    pass


def rpc_result(resp: ProviderResponse) -> Any:
    payload = resp.payload
    if not isinstance(payload, dict) or "error" in payload:
        err = payload.get("error") if isinstance(payload, dict) else payload
        raise RpcError(f"helius rpc error: {err}")
    return payload.get("result")


# ---------------------------------------------------------------- mint security


@dataclass(frozen=True)
class MintSecurity:
    mint: str
    status: str  # ok | risky | unknown
    token_program: str | None
    mint_authority: str | None
    freeze_authority: str | None
    supply_raw: int | None
    decimals: int | None
    extensions: list[str] | None
    risk_flags: list[str]


def _extension_flags(extensions: list[dict[str, Any]]) -> list[str]:
    flags = []
    for ext in extensions:
        name, state = ext.get("extension"), ext.get("state") or {}
        if name == "permanentDelegate" and state.get("delegate"):
            flags.append("permanent_delegate")
        elif name == "transferHook" and state.get("programId"):
            flags.append("transfer_hook")
        elif name == "transferFeeConfig":
            fees = [
                (state.get(k) or {}).get("transferFeeBasisPoints") or 0
                for k in ("newerTransferFee", "olderTransferFee")
            ]
            if max(fees) > 0:
                flags.append("transfer_fee")
        elif name == "nonTransferable":
            flags.append("non_transferable")
        elif name == "pausableConfig" and state.get("authority"):
            flags.append("pausable")
        elif name == "defaultAccountState" and state.get("accountState") == "frozen":
            flags.append("default_frozen")
        elif name == "mintCloseAuthority" and state.get("closeAuthority"):
            flags.append("mint_close_authority")
    return flags


def parse_mint_account(mint: str, account: dict[str, Any] | None) -> MintSecurity:
    parsed = ((account or {}).get("data") or {}).get("parsed") if account else None
    if not isinstance(parsed, dict) or parsed.get("type") != "mint":
        return MintSecurity(mint, "unknown", None, None, None, None, None, None, ["unparseable"])
    info = parsed.get("info") or {}
    extensions = info.get("extensions") or []
    flags = []
    if info.get("mintAuthority"):
        flags.append("mint_authority_active")
    if info.get("freezeAuthority"):
        flags.append("freeze_authority_active")
    flags += _extension_flags(extensions)
    return MintSecurity(
        mint=mint,
        status="risky" if flags else "ok",
        token_program=account.get("owner") if account else None,
        mint_authority=info.get("mintAuthority"),
        freeze_authority=info.get("freezeAuthority"),
        supply_raw=int(info["supply"]) if info.get("supply") is not None else None,
        decimals=info.get("decimals"),
        extensions=[e.get("extension") for e in extensions],
        risk_flags=flags,
    )


# ---------------------------------------------------------------- holders


@dataclass(frozen=True)
class HolderAnalysis:
    supply_ui: float
    top10_pct_raw: float  # top-10 token accounts, no exclusions
    top10_pct_adjusted: float  # top-10 owners after exclusions, of supply minus burns
    top1_pct_adjusted: float  # largest owner after exclusions
    excluded_pct: float
    accounts: list[dict[str, Any]]  # one entry per owner (token accounts aggregated)


def analyze_holders(
    largest: list[dict[str, Any]],
    token_account_owners: dict[str, str | None],
    owner_programs: dict[str, str | None],
    supply_raw: int,
    decimals: int,
    infrastructure: dict[str, str] | None = None,
) -> HolderAnalysis:
    """Holder concentration with documented exclusions (method v2).

    Token accounts are aggregated by owner. Owners are excluded when they are:
      * burn            - the incinerator (also removed from the denominator)
      * program         - off the ed25519 curve (a PDA: AMM vault authority, multisig,
                          locker, bonding curve) or an account owned by a non-System program
      * infrastructure  - listed in the curated infrastructure-wallet file (exchanges,
                          custodians), with the label recorded
    Unresolvable owners are counted as holders (keyed by token account), so missing data
    never lowers concentration. Only the 20 largest token accounts are available from RPC.
    """
    if supply_raw <= 0:
        raise ValueError("supply must be positive")
    infrastructure = infrastructure or {}
    owners: dict[str, dict[str, Any]] = {}
    for acc in largest:
        address, amount = acc["address"], int(acc["amount"])
        owner = token_account_owners.get(address)
        key = owner or address
        entry = owners.setdefault(
            key,
            {
                "owner": owner,
                "owner_program": owner_programs.get(owner) if owner else None,
                "off_curve": is_pda(owner) if owner else None,
                "token_accounts": [],
                "amount_raw": 0,
            },
        )
        entry["token_accounts"].append(address)
        entry["amount_raw"] += amount
    rows = []
    for e in owners.values():
        owner, program = e["owner"], e["owner_program"]
        if owner == INCINERATOR:
            reason = "burn"
        elif owner in infrastructure:
            reason = f"infrastructure:{infrastructure[owner]}"
        elif owner is not None and (e["off_curve"] or program not in (None, SYSTEM_PROGRAM)):
            reason = "program"
        else:
            reason = None
        rows.append({**e, "pct_of_supply": e["amount_raw"] / supply_raw * 100, "excluded": reason})
    rows.sort(key=lambda a: a["amount_raw"], reverse=True)
    burned = sum(a["amount_raw"] for a in rows if a["excluded"] == "burn")
    excluded = sum(a["amount_raw"] for a in rows if a["excluded"])
    counted = [a["amount_raw"] for a in rows if not a["excluded"]]
    effective_supply = max(supply_raw - burned, 1)
    raw_top10 = sorted((int(a["amount"]) for a in largest), reverse=True)[:10]
    return HolderAnalysis(
        supply_ui=supply_raw / 10**decimals,
        top10_pct_raw=sum(raw_top10) / supply_raw * 100,
        top10_pct_adjusted=sum(counted[:10]) / effective_supply * 100,
        top1_pct_adjusted=(counted[0] / effective_supply * 100) if counted else 0.0,
        excluded_pct=excluded / supply_raw * 100,
        accounts=rows,
    )


class HeliusClient:
    def __init__(self, settings: HeliusSettings, **kw: Any) -> None:
        params = {"api-key": settings.api_key.get_secret_value()} if settings.api_key else None
        self.http = ProviderClient(SOURCE, settings, default_params=params, **kw)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def rpc(self, method: str, params: list[Any]) -> ProviderResponse:
        return await self.http.post_json(
            "/", {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        )

    async def get_multiple_accounts(
        self, addresses: list[str], *, parsed: bool = True
    ) -> ProviderResponse:
        if len(addresses) > 100:
            raise ValueError("getMultipleAccounts accepts at most 100 addresses")
        opts: dict[str, Any] = (
            {"encoding": "jsonParsed"}
            if parsed
            else {"encoding": "base64", "dataSlice": {"offset": 0, "length": 0}}
        )
        return await self.rpc("getMultipleAccounts", [addresses, opts])

    async def get_token_largest_accounts(self, mint: str) -> ProviderResponse:
        return await self.rpc("getTokenLargestAccounts", [mint])
