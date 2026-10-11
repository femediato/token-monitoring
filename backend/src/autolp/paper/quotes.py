"""Executable Jupiter quotes used as paper fills.

A fill is a full-size ExactIn quote paired with a small reference quote taken just before
it, in the same direction. The reference price is USD per token at the small size; the
realised cost is how much worse the full size is, in percent. Nothing is signed or sent.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from autolp.providers import jupiter
from autolp.providers.http import ProviderError, ProviderResponse

USDC = jupiter.USDC_MINT
USDC_UNITS = 10**jupiter.USDC_DECIMALS
QUOTE_ERRORS = (ProviderError, KeyError, ValueError, TypeError, ZeroDivisionError)


class Quoter(Protocol):
    async def quote(
        self, input_mint: str, output_mint: str, amount_raw: int, slippage_bps: int = 300
    ) -> ProviderResponse: ...


@dataclass
class Leg:
    """One executed quote pair. On failure, `error` is set and the amounts may be missing."""

    responses: list[ProviderResponse] = field(default_factory=list)
    quoted_at: datetime | None = None
    in_raw: int | None = None
    out_raw: int | None = None
    usd: float | None = None  # USDC paid (buy) or received (sell)
    qty: float | None = None  # tokens received (buy) or sold (sell)
    ref_price: float | None = None
    fill_price: float | None = None
    cost_pct: float | None = None
    impact_pct: float | None = None
    route: list[str] | None = None
    error: str | None = None


async def _quote(q: Quoter, leg: Leg, inp: str, out: str, raw: int) -> jupiter.Quote:
    resp = await q.quote(inp, out, raw)
    leg.responses.append(resp)
    parsed = jupiter.parse_quote(resp.payload)
    if parsed.in_amount_raw <= 0 or parsed.out_amount_raw <= 0:
        raise ValueError("empty quote")
    leg.quoted_at = resp.fetched_at
    return parsed


def _fill(leg: Leg, fq: jupiter.Quote) -> None:
    leg.in_raw, leg.out_raw = fq.in_amount_raw, fq.out_amount_raw
    leg.impact_pct, leg.route = fq.price_impact_pct, fq.route_labels


async def buy(q: Quoter, mint: str, decimals: int, usd: float, ref_usd: float) -> Leg:
    """Spend `usd` USDC on the token."""
    leg, units = Leg(), 10**decimals
    try:
        rq = await _quote(q, leg, USDC, mint, int(ref_usd * USDC_UNITS))
        fq = await _quote(q, leg, USDC, mint, int(round(usd * USDC_UNITS)))
        _fill(leg, fq)
        leg.ref_price = (rq.in_amount_raw / USDC_UNITS) / (rq.out_amount_raw / units)
        leg.usd, leg.qty = fq.in_amount_raw / USDC_UNITS, fq.out_amount_raw / units
        leg.fill_price = leg.usd / leg.qty
        leg.cost_pct = (leg.fill_price / leg.ref_price - 1) * 100
    except QUOTE_ERRORS as exc:
        leg.error = f"{type(exc).__name__}: {exc}"
    return leg


async def reference_sell(
    q: Quoter, mint: str, decimals: int, held_raw: int, ref_usd: float, price_hint: float
) -> Leg:
    """Sell quote for about `ref_usd` worth of the held tokens: the mark price."""
    leg, units = Leg(), 10**decimals
    raw = max(1, min(held_raw, int(ref_usd / price_hint * units)))
    try:
        rq = await _quote(q, leg, mint, USDC, raw)
        leg.ref_price = (rq.out_amount_raw / USDC_UNITS) / (rq.in_amount_raw / units)
    except QUOTE_ERRORS as exc:
        leg.error = f"{type(exc).__name__}: {exc}"
    return leg


async def sell(q: Quoter, mint: str, decimals: int, held_raw: int, ref_price: float | None) -> Leg:
    """Sell every held token; the cost is measured against `ref_price` (the mark just taken)."""
    leg, units = Leg(), 10**decimals
    try:
        fq = await _quote(q, leg, mint, USDC, held_raw)
        _fill(leg, fq)
        leg.usd, leg.qty = fq.out_amount_raw / USDC_UNITS, fq.in_amount_raw / units
        leg.fill_price = leg.usd / leg.qty
        leg.ref_price = ref_price
        if ref_price:
            leg.cost_pct = (1 - leg.fill_price / ref_price) * 100
    except QUOTE_ERRORS as exc:
        leg.error = f"{type(exc).__name__}: {exc}"
    return leg
