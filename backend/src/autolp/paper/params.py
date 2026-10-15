"""Paper trading parameters, version paper-v2 (docs/PAPER_TRADING.md).

Changing a value here is a paper-trading change: bump PAPER_VERSION and record it in the
document. Entry and exit rules, sizing and risk limits are the strategies' (strat-v1).
"""

from dataclasses import dataclass

PAPER_VERSION = "paper-v2"


@dataclass(frozen=True)
class PaperParams:
    account: str = "main"
    tick_minutes: int = 1  # entries, exit checks and the equity mark run every minute
    # Every fill pairs the full-size Jupiter quote with a quote of this size, taken just
    # before; the realised cost is measured between the two (as for `swap_quotes`).
    reference_usd: float = 10
    # An order whose quote did not complete within this (e.g. a crash between the
    # decision and the fill) is cancelled as interrupted on the next tick.
    order_timeout_minutes: float = 2
    # The backtest-model price of a fill needs the next candle; give up after this.
    model_wait_hours: float = 3


PAPER = PaperParams()
