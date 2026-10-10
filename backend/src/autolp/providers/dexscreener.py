"""DEX Screener public API: pool-level liquidity, reserves, volume and trade counts."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from autolp.config import DexScreenerSettings
from autolp.providers._util import as_float, as_int, from_epoch_ms
from autolp.providers.http import ProviderClient, ProviderResponse

SOURCE = "dexscreener"


@dataclass(frozen=True)
class PairObservation:
    pool_address: str
    token_mint: str
    dex_id: str | None
    quote_mint: str | None
    quote_symbol: str | None
    labels: list[str]
    pair_created_at: datetime | None
    price_usd: float | None
    price_native: float | None
    liquidity_usd: float | None
    reserve_base: float | None
    reserve_quote: float | None
    market_cap: float | None
    fdv: float | None
    volume: dict[str, float | None]
    buys: dict[str, int | None]
    sells: dict[str, int | None]
    price_change: dict[str, float | None]


def parse_pair(d: dict[str, Any]) -> PairObservation:
    liq = d.get("liquidity") or {}
    vol = d.get("volume") or {}
    txns = d.get("txns") or {}
    chg = d.get("priceChange") or {}
    return PairObservation(
        pool_address=d["pairAddress"],
        token_mint=d["baseToken"]["address"],
        dex_id=d.get("dexId"),
        quote_mint=(d.get("quoteToken") or {}).get("address"),
        quote_symbol=(d.get("quoteToken") or {}).get("symbol"),
        labels=list(d.get("labels") or []),
        pair_created_at=from_epoch_ms(d.get("pairCreatedAt")),
        price_usd=as_float(d.get("priceUsd")),
        price_native=as_float(d.get("priceNative")),
        liquidity_usd=as_float(liq.get("usd")),
        reserve_base=as_float(liq.get("base")),
        reserve_quote=as_float(liq.get("quote")),
        market_cap=as_float(d.get("marketCap")),
        fdv=as_float(d.get("fdv")),
        volume={w: as_float(vol.get(w)) for w in ("m5", "h1", "h6", "h24")},
        buys={w: as_int((txns.get(w) or {}).get("buys")) for w in ("m5", "h1", "h24")},
        sells={w: as_int((txns.get(w) or {}).get("sells")) for w in ("m5", "h1", "h24")},
        price_change={w: as_float(chg.get(w)) for w in ("h1", "h24")},
    )


def parse_pairs(payload: Any, mints: set[str]) -> list[PairObservation]:
    """Pairs where one of `mints` is the base token, on Solana. Other pairs are ignored."""
    out = []
    for d in payload or []:
        if d.get("chainId") != "solana" or not d.get("pairAddress"):
            continue
        if (d.get("baseToken") or {}).get("address") in mints:
            out.append(parse_pair(d))
    return out


class DexScreenerClient:
    def __init__(self, settings: DexScreenerSettings, **kw: Any) -> None:
        self.http = ProviderClient(SOURCE, settings, **kw)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def tokens(self, mints: list[str]) -> ProviderResponse:
        if len(mints) > 30:
            raise ValueError("DEX Screener accepts at most 30 token addresses per request")
        return await self.http.get_json(f"/tokens/v1/solana/{','.join(mints)}")
