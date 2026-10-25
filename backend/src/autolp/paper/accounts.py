"""Paper accounts run side by side (paper-v2, docs/PAPER_TRADING.md).

`main` trades the approved strategy version (STRATEGY_VERSION) and stays untouched: it is the control. Research
accounts forward-test strategy variants from the research sweeps on live data the sweeps
never saw. Each account has its own cash, positions and risk limits; all of them only
request quotes. An account is identified by its name, and its overrides are part of its
definition: changing them means a new account name, so a record never mixes two rule sets.
"""

from dataclasses import dataclass, field
from typing import Any

from autolp.paper.params import PAPER, PaperParams
from autolp.strategies.params import PARAMS, StrategyParams, with_overrides


@dataclass(frozen=True)
class AccountSpec:
    name: str
    description: str
    overrides: dict[str, Any] = field(default_factory=dict)  # strategy parameter overrides

    def params(self) -> StrategyParams:
        return with_overrides(PARAMS, self.overrides)

    def paper(self) -> PaperParams:
        return PaperParams(**{**PAPER.__dict__, "account": self.name})


MAIN = AccountSpec("main", "the approved strategy version (control)")

ACCOUNTS: tuple[AccountSpec, ...] = (MAIN,)


def spec(name: str) -> AccountSpec | None:
    return next((a for a in ACCOUNTS if a.name == name), None)
