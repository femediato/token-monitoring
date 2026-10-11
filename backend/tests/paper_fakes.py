"""Test double for Jupiter quotes (tests only, never production data).

Prices are set per mint in USD per token. A quote pays a 0.25% pool fee plus price impact
of 1% per $1,000, so the $10 reference quote is almost free and a full-size quote costs
about 1.25% more. `fail` makes every quote for a mint raise a ProviderError; `fail_full` only quotes above
$100 (the price checks still work, the swap does not).
"""

from datetime import UTC, datetime
from typing import Any

from autolp.providers.http import ProviderError, ProviderResponse
from autolp.providers.jupiter import USDC_DECIMALS, USDC_MINT

POOL_FEE = 0.0025
IMPACT_PER_USD = 0.01 / 1000


class FakeQuoter:
    def __init__(self, decimals: dict[str, int], prices: dict[str, float]) -> None:
        self.decimals, self.prices = decimals, prices
        self.fail: set[str] = set()
        self.fail_full: set[str] = set()
        self.calls: list[tuple[str, str, int]] = []
        self.now = datetime(2026, 10, 12, tzinfo=UTC)

    async def quote(
        self, input_mint: str, output_mint: str, amount_raw: int, slippage_bps: int = 300
    ) -> ProviderResponse:
        self.calls.append((input_mint, output_mint, amount_raw))
        mint = output_mint if input_mint == USDC_MINT else input_mint
        if mint in self.fail:
            raise ProviderError("no route")
        units, usdc = 10 ** self.decimals[mint], 10**USDC_DECIMALS
        price = self.prices[mint]
        usd = amount_raw / usdc if input_mint == USDC_MINT else amount_raw / units * price
        if mint in self.fail_full and usd > 100:
            raise ProviderError("route failed")
        if input_mint == USDC_MINT:
            out = usd * (1 - POOL_FEE - usd * IMPACT_PER_USD) / price * units
        else:
            out = usd * (1 - POOL_FEE - usd * IMPACT_PER_USD) * usdc
        payload: dict[str, Any] = {
            "inAmount": str(amount_raw),
            "outAmount": str(int(out)),
            "priceImpactPct": str(usd * IMPACT_PER_USD),
            "routePlan": [{"swapInfo": {"label": "FakeAMM"}}],
        }
        return ProviderResponse("jupiter", "/swap/v1/quote", None, 200, payload, self.now)
