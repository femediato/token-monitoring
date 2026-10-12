"""GeckoTerminal public API: 5-minute OHLCV candles per pool."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from autolp.config import GeckoTerminalSettings
from autolp.providers.http import ProviderClient, ProviderResponse

SOURCE = "geckoterminal"
CANDLE = timedelta(minutes=5)


@dataclass(frozen=True)
class Candle:
    bucket: datetime
    open: float
    high: float
    low: float
    close: float
    volume_usd: float

    def is_complete(self, as_of: datetime) -> bool:
        return self.bucket + CANDLE <= as_of


def parse_ohlcv(payload: dict[str, Any]) -> list[Candle]:
    rows = ((payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    # The API occasionally repeats a bucket; keep the first (rows are newest-first).
    by_bucket: dict[datetime, Candle] = {}
    for r in rows:
        bucket = datetime.fromtimestamp(int(r[0]), UTC)
        by_bucket.setdefault(bucket, Candle(bucket, *(float(x) for x in r[1:6])))
    return sorted(by_bucket.values(), key=lambda c: c.bucket)


class GeckoTerminalClient:
    def __init__(self, settings: GeckoTerminalSettings, **kw: Any) -> None:
        self.http = ProviderClient(SOURCE, settings, headers={"accept": "application/json"}, **kw)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def ohlcv_5m(
        self, pool: str, limit: int, before: datetime | None = None, token: str = "base"
    ) -> ProviderResponse:
        """5-minute candles of `token`'s USD price in `pool`. Pass the token's address: the
        pool's base asset is not always the token we track (a token/token pool can list
        it as the quote), and "base" would then return the other token's price."""
        params: dict[str, Any] = {
            "aggregate": 5,
            "limit": limit,
            "currency": "usd",
            "token": token,
        }
        if before is not None:
            params["before_timestamp"] = int(before.timestamp())
        return await self.http.get_json(f"/networks/solana/pools/{pool}/ohlcv/minute", params)
