"""Top-30 watchlist membership rules (spec v1.0 section 5, as adopted in model v1.1).

Pure decision function: given current members, the latest evaluation, the previous hourly
evaluation and the time, return the membership changes with reason codes.

  * Critical safety failure -> removed at any evaluation (every 15 minutes).
  * Other changes only at hourly evaluations on a 6-hour boundary (00/06/12/18 UTC), or
    while the watchlist is being formed for the first time.
  * Entry: eligible, coverage >= 80%, score >= 70 in this and the previous hourly evaluation.
  * Removal: ineligible; or score < 55 in two consecutive hourly evaluations; or outscored
    by >= 10 points by a qualifying non-member in two consecutive hourly evaluations.
  * Score-based removals and swaps are capped at 6 per UTC day.
  * Group slots (10 each) fill in the order established, emerging, breakout; unfilled slots
    go to the highest-scoring remaining qualified candidates. No one enters below 70, so the
    list can hold fewer than 30 coins.
"""

from dataclasses import dataclass
from datetime import datetime

from autolp.selection.params import WATCHLIST, WatchlistParams

GROUP_ORDER = ("established", "emerging", "breakout")


@dataclass(frozen=True)
class Eval:
    eligible: bool
    critical: bool
    total: float | None
    coverage: float
    group: str | None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Member:
    mint: str
    group: str


@dataclass(frozen=True)
class Change:
    action: str  # add | remove
    mint: str
    group: str | None
    reason: str
    score: float | None
    limited: bool = False  # counts toward the daily replacement cap


def is_membership_window(now: datetime, p: WatchlistParams = WATCHLIST) -> bool:
    return now.hour % p.membership_every_hours == 0


def decide(
    members: list[Member],
    current: dict[str, Eval],
    previous: dict[str, Eval] | None,
    now: datetime,
    *,
    hourly: bool,
    replacements_today: int,
    forming: bool,
    p: WatchlistParams = WATCHLIST,
) -> list[Change]:
    changes: list[Change] = []
    kept: dict[str, Member] = {m.mint: m for m in members}

    def score_of(mint: str) -> float | None:
        e = current.get(mint)
        return e.total if e else None

    for m in list(kept.values()):
        e = current.get(m.mint)
        if e is not None and e.critical:
            reason = "critical:" + ",".join(r for r in e.reasons if ":" in r or "route" in r)
            changes.append(Change("remove", m.mint, m.group, reason, e.total))
            del kept[m.mint]

    if not hourly or previous is None or not (forming or is_membership_window(now, p)):
        return changes

    def total(evals: dict[str, Eval], mint: str) -> float:
        e = evals.get(mint)
        return e.total if e is not None and e.total is not None else float("-inf")

    budget = p.max_replacements_per_day - replacements_today
    for m in list(kept.values()):
        e = current.get(m.mint)
        if e is None or not e.eligible:
            why = "ineligible:" + ",".join(e.reasons) if e else "ineligible:not_evaluated"
            changes.append(Change("remove", m.mint, m.group, why, score_of(m.mint)))
            del kept[m.mint]
        elif total(current, m.mint) < p.retain_score and total(previous, m.mint) < p.retain_score:
            if budget > 0:
                changes.append(
                    Change("remove", m.mint, m.group, "score_below_retain", e.total, limited=True)
                )
                del kept[m.mint]
                budget -= 1

    def qualifies(mint: str) -> bool:
        e, prev = current.get(mint), previous.get(mint)
        return (
            e is not None
            and prev is not None
            and e.eligible
            and prev.eligible
            and e.coverage >= p.min_coverage
            and total(current, mint) >= p.entry_score
            and total(previous, mint) >= p.entry_score
        )

    candidates = sorted(
        (mint for mint in current if mint not in kept and qualifies(mint)),
        key=lambda mint: total(current, mint),
        reverse=True,
    )

    def add(mint: str, reason: str) -> None:
        group = current[mint].group or "other"
        kept[mint] = Member(mint, group)
        changes.append(Change("add", mint, group, reason, current[mint].total))
        candidates.remove(mint)

    for group in GROUP_ORDER:
        for mint in [c for c in candidates if current[c].group == group]:
            if sum(1 for m in kept.values() if m.group == group) >= p.group_targets.get(group, 0):
                break
            if len(kept) >= p.size:
                break
            add(mint, f"qualified:{group}")
    for mint in list(candidates):
        if len(kept) >= p.size:
            break
        add(mint, "qualified:unfilled_slot")

    # Swaps: a qualifying candidate beats the weakest member by the margin, twice in a row.
    while candidates and budget > 0 and kept:
        weakest = min(kept.values(), key=lambda m: total(current, m.mint))
        best = candidates[0]
        if not (
            total(current, best) >= total(current, weakest.mint) + p.outscore_margin
            and total(previous, best) >= total(previous, weakest.mint) + p.outscore_margin
        ):
            break
        changes.append(
            Change(
                "remove",
                weakest.mint,
                weakest.group,
                f"outscored_by:{best}",
                score_of(weakest.mint),
                limited=True,
            )
        )
        del kept[weakest.mint]
        budget -= 1
        add(best, f"replaces:{weakest.mint}")
    return changes
