"""Execution assumptions for paper fills (fill model v0 / v1).

The decision engine asks this model how an order executes: how long after the decision the
entry can fill, how much the realised cost differs from the quoted one, how far a stop
fills below its trigger, and whether a swap fails. Failures are pseudo-random but
deterministic: a hash of the seed and the order's identity, so a rerun gives the same
failures.

`EXEC_V0` is the Phase 5 model (no delay, quoted cost, stops at the trigger, no failures);
the backtest scenarios in `autolp.backtest.params` are v1.
"""

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionParams:
    name: str = "v0"
    # The entry fills at the open of the first candle starting at least this long after
    # the decision.
    entry_delay_minutes: float = 0
    # Realised one-way cost = quoted cost x this (the quote is an estimate).
    cost_multiplier: float = 1.0
    # Stop exits (incl. gaps and exits without data) fill this much below the trigger.
    stop_slippage_pct: float = 0.0
    # Probability that a swap fails. A failed entry is cancelled; a failed exit is retried
    # at the open of the next candle.
    swap_failure_pct: float = 0.0
    seed: str = "autolp"

    def fails(self, *key: object) -> bool:
        if self.swap_failure_pct <= 0:
            return False
        h = hashlib.sha256("|".join(map(str, (self.seed, *key))).encode()).digest()
        return int.from_bytes(h[:8], "big") / 2**64 < self.swap_failure_pct / 100


EXEC_V0 = ExecutionParams()
