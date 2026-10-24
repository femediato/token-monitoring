"""Jupiter Tokens V2 (discovery + token-level market data), Price V3 and Swap quote API."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from autolp.config import JupiterSettings
from autolp.providers._util import as_float, as_int, parse_iso
from autolp.providers.http import ProviderClient, ProviderResponse

SOURCE = "jupiter"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDC_DECIMALS = 6
WINDOWS = {"5m": "stats5m", "1h": "stats1h", "6h": "stats6h", "24h": "stats24h"}


@dataclass(frozen=True)
class WindowStats:
    window: str
    price_change_pct: float | None
    liquidity_change_pct: float | None
    holder_change_pct: float | None
    buy_volume_usd: float | None
    sell_volume_usd: float | None
    organic_buy_volume_usd: float | None
    organic_sell_volume_usd: float | None
    num_buys: int | None
    num_sells: int | None
    num_traders: int | None
    num_net_buyers: int | None
    num_organic_buyers: int | None

    @property
    def volume_usd(self) -> float | None:
        if self.buy_volume_usd is None or self.sell_volume_usd is None:
            return None
        return self.buy_volume_usd + self.sell_volume_usd


@dataclass(frozen=True)
class JupiterToken:
    mint: str
    symbol: str | None
    name: str | None
    decimals: int | None
    token_program: str | None
    launchpad: str | None
    tags: list[str]
    first_pool_created_at: datetime | None
    updated_at: datetime | None
    price_usd: float | None
    market_cap: float | None
    fdv: float | None
    liquidity_usd: float | None
    circ_supply: float | None
    total_supply: float | None
    holder_count: int | None
    organic_score: float | None
    organic_score_label: str | None
    mint_authority_disabled: bool | None
    freeze_authority_disabled: bool | None
    top_holders_pct: float | None
    dev_balance_pct: float | None
    windows: dict[str, WindowStats] = field(default_factory=dict)


def _window(name: str, d: dict[str, Any]) -> WindowStats:
    return WindowStats(
        window=name,
        price_change_pct=as_float(d.get("priceChange")),
        liquidity_change_pct=as_float(d.get("liquidityChange")),
        holder_change_pct=as_float(d.get("holderChange")),
        buy_volume_usd=as_float(d.get("buyVolume")),
        sell_volume_usd=as_float(d.get("sellVolume")),
        organic_buy_volume_usd=as_float(d.get("buyOrganicVolume")),
        organic_sell_volume_usd=as_float(d.get("sellOrganicVolume")),
        num_buys=as_int(d.get("numBuys")),
        num_sells=as_int(d.get("numSells")),
        num_traders=as_int(d.get("numTraders")),
        num_net_buyers=as_int(d.get("numNetBuyers")),
        num_organic_buyers=as_int(d.get("numOrganicBuyers")),
    )


def _bool(v: Any) -> bool | None:
    return v if isinstance(v, bool) else None


def parse_token(d: dict[str, Any]) -> JupiterToken:
    audit = d.get("audit") or {}
    return JupiterToken(
        mint=d["id"],
        symbol=d.get("symbol"),
        name=d.get("name"),
        decimals=as_int(d.get("decimals")),
        token_program=d.get("tokenProgram"),
        launchpad=d.get("launchpad"),
        tags=list(d.get("tags") or []),
        first_pool_created_at=parse_iso((d.get("firstPool") or {}).get("createdAt")),
        updated_at=parse_iso(d.get("updatedAt")),
        price_usd=as_float(d.get("usdPrice")),
        market_cap=as_float(d.get("mcap")),
        fdv=as_float(d.get("fdv")),
        liquidity_usd=as_float(d.get("liquidity")),
        circ_supply=as_float(d.get("circSupply")),
        total_supply=as_float(d.get("totalSupply")),
        holder_count=as_int(d.get("holderCount")),
        organic_score=as_float(d.get("organicScore")),
        organic_score_label=d.get("organicScoreLabel"),
        mint_authority_disabled=_bool(audit.get("mintAuthorityDisabled")),
        freeze_authority_disabled=_bool(audit.get("freezeAuthorityDisabled")),
        top_holders_pct=as_float(audit.get("topHoldersPercentage")),
        dev_balance_pct=as_float(audit.get("devBalancePercentage")),
        windows={w: _window(w, d[k]) for w, k in WINDOWS.items() if isinstance(d.get(k), dict)},
    )


def parse_tokens(payload: Any) -> list[JupiterToken]:
    return [parse_token(d) for d in payload if isinstance(d, dict) and d.get("id")]


@dataclass(frozen=True)
class Quote:
    in_amount_raw: int
    out_amount_raw: int
    price_impact_pct: float | None
    route_labels: list[str]
    context_slot: int | None


def parse_quote(payload: dict[str, Any]) -> Quote:
    impact = as_float(payload.get("priceImpactPct"))
    return Quote(
        in_amount_raw=int(payload["inAmount"]),
        out_amount_raw=int(payload["outAmount"]),
        # Jupiter reports impact as a fraction; store percent.
        price_impact_pct=None if impact is None else impact * 100,
        route_labels=[
            (step.get("swapInfo") or {}).get("label", "?")
            for step in payload.get("routePlan") or []
        ],
        context_slot=as_int(payload.get("contextSlot")),
    )


PRICE_MAX_IDS = 50  # Price V3 accepts at most 50 mints per request


@dataclass(frozen=True)
class Price:
    """One Price V3 entry: the USD price derived from recent trades, and the slot
    (`blockId`) it was last updated at."""

    mint: str
    usd_price: float
    block_id: int | None
    liquidity_usd: float | None


def parse_prices(payload: Any) -> list[Price]:
    """Mints without a usable price (unknown or no recent trade) are left out."""
    out: list[Price] = []
    if not isinstance(payload, dict):
        return out
    for mint, d in sorted(payload.items()):
        if not isinstance(d, dict):
            continue
        price = as_float(d.get("usdPrice"))
        if price is None or price <= 0:
            continue
        out.append(Price(mint, price, as_int(d.get("blockId")), as_float(d.get("liquidity"))))
    return out


class JupiterClient:
    def __init__(self, settings: JupiterSettings, **kw: Any) -> None:
        headers = {"x-api-key": settings.api_key.get_secret_value()} if settings.api_key else None
        self.http = ProviderClient(SOURCE, settings, headers=headers, **kw)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def top_list(self, category: str, interval: str, limit: int) -> ProviderResponse:
        return await self.http.get_json(f"/tokens/v2/{category}/{interval}", {"limit": limit})

    async def search(self, mints: list[str]) -> ProviderResponse:
        return await self.http.get_json("/tokens/v2/search", {"query": ",".join(mints)})

    async def prices(self, mints: list[str]) -> ProviderResponse:
        if not 0 < len(mints) <= PRICE_MAX_IDS:
            raise ValueError(f"prices() takes 1-{PRICE_MAX_IDS} mints, got {len(mints)}")
        return await self.http.get_json("/price/v3", {"ids": ",".join(mints)})

    async def quote(
        self, input_mint: str, output_mint: str, amount_raw: int, slippage_bps: int = 300
    ) -> ProviderResponse:
        return await self.http.get_json(
            "/swap/v1/quote",
            {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount_raw,
                "slippageBps": slippage_bps,
                "swapMode": "ExactIn",
            },
        )
