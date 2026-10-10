"""Candle helpers: completed 5-minute bars, clock-aligned hourly bars and EMAs."""

from dataclasses import dataclass
from datetime import datetime, timedelta

FIVE_MIN = timedelta(minutes=5)


@dataclass(frozen=True)
class Bar:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume_usd: float = 0.0

    @property
    def end(self) -> datetime:
        return self.start + FIVE_MIN

    @property
    def bullish(self) -> bool:
        return self.close > self.open


@dataclass(frozen=True)
class HourBar:
    start: datetime
    high: float
    low: float
    close: float
    candles: int  # 5-minute candles present (of 12)


def completed(bars: list[Bar], at: datetime) -> list[Bar]:
    """Bars that had closed by `at`, ascending."""
    return sorted((b for b in bars if b.end <= at), key=lambda b: b.start)


def hour_bar(bars: list[Bar], hour_start: datetime) -> HourBar | None:
    inside = [b for b in bars if hour_start <= b.start < hour_start + timedelta(hours=1)]
    if not inside:
        return None
    return HourBar(
        hour_start,
        max(b.high for b in inside),
        min(b.low for b in inside),
        inside[-1].close,
        len(inside),
    )


def floor_hour(t: datetime) -> datetime:
    return t.replace(minute=0, second=0, microsecond=0)


def ema(values: list[float], n: int) -> float | None:
    """EMA seeded with the simple mean of the first n values; None with fewer than n."""
    if len(values) < n:
        return None
    k = 2 / (n + 1)
    e = sum(values[:n]) / n
    for v in values[n:]:
        e = v * k + e * (1 - k)
    return e
