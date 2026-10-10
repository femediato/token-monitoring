from datetime import UTC, datetime

from autolp.selection.watchlist import Eval, Member, decide

WINDOW = datetime(2026, 10, 11, 6, 4, tzinfo=UTC)  # 06:04 -> membership window
OFF = datetime(2026, 10, 11, 7, 4, tzinfo=UTC)  # hourly, not a window


def ev(
    total: float | None,
    group: str = "established",
    eligible: bool = True,
    critical: bool = False,
    coverage: float = 1.0,
    reasons: tuple[str, ...] = (),
) -> Eval:
    return Eval(eligible, critical, total, coverage, group, reasons)


def run(members, current, previous, now=WINDOW, hourly=True, replacements=0, forming=False):
    return decide(
        members,
        current,
        previous,
        now,
        hourly=hourly,
        replacements_today=replacements,
        forming=forming,
    )


def test_entry_needs_two_consecutive_qualifying_evaluations() -> None:
    cur = {"A": ev(75), "B": ev(80)}
    prev = {"A": ev(72), "B": ev(65)}
    adds = [c for c in run([], cur, prev) if c.action == "add"]
    assert [c.mint for c in adds] == ["A"] and adds[0].reason == "qualified:established"


def test_no_entry_without_coverage_or_previous_evaluation() -> None:
    assert run([], {"A": ev(90, coverage=0.7)}, {"A": ev(90)}) == []
    assert run([], {"A": ev(90)}, None) == []


def test_changes_only_in_membership_window_unless_forming() -> None:
    cur, prev = {"A": ev(90)}, {"A": ev(90)}
    assert run([], cur, prev, now=OFF) == []
    assert [c.mint for c in run([], cur, prev, now=OFF, forming=True)] == ["A"]


def test_critical_failure_removes_immediately_even_off_window_and_non_hourly() -> None:
    members = [Member("A", "established")]
    cur = {
        "A": ev(None, eligible=False, critical=True, reasons=("security:mint_authority_active",))
    }
    out = run(members, cur, None, now=OFF, hourly=False)
    assert [(c.action, c.mint, c.reason) for c in out] == [
        ("remove", "A", "critical:security:mint_authority_active")
    ]


def test_retention_rules() -> None:
    members = [Member("A", "established"), Member("B", "established"), Member("C", "established")]
    cur = {"A": ev(50), "B": ev(50), "C": ev(None, eligible=False, reasons=("volume_24h",))}
    prev = {"A": ev(54), "B": ev(60), "C": ev(70)}
    out = {c.mint: c for c in run(members, cur, prev)}
    assert out["A"].reason == "score_below_retain" and out["A"].limited
    assert "B" not in out  # only one evaluation below 55
    assert out["C"].reason == "ineligible:volume_24h" and not out["C"].limited


def test_daily_replacement_cap() -> None:
    members = [Member(m, "established") for m in "ABC"]
    cur = {m: ev(40) for m in "ABC"}
    prev = {m: ev(40) for m in "ABC"}
    out = run(members, cur, prev, replacements=5)
    assert len([c for c in out if c.action == "remove"]) == 1


def test_outscored_swap_requires_margin_twice() -> None:
    members = [Member(f"M{i}", "established") for i in range(30)]
    cur = {f"M{i}": ev(80 + i * 0.1) for i in range(30)} | {"X": ev(92)}
    prev = {f"M{i}": ev(80) for i in range(30)} | {"X": ev(91)}
    out = run(members, cur, prev)
    assert [(c.action, c.mint, c.reason) for c in out] == [
        ("remove", "M0", "outscored_by:X"),
        ("add", "X", "replaces:M0"),
    ]
    prev["X"] = ev(85)  # margin not met in the previous evaluation
    assert run(members, cur, prev) == []


def test_group_targets_then_unfilled_slots() -> None:
    cur = (
        {f"E{i}": ev(90 - i) for i in range(12)}
        | {"G1": ev(75, group="emerging")}
        | {"O1": ev(71, group="other")}
    )
    prev = dict(cur)
    out = run([], cur, prev)
    adds = [c for c in out if c.action == "add"]
    by_reason = {}
    for c in adds:
        by_reason.setdefault(c.reason, []).append(c.mint)
    assert len(by_reason["qualified:established"]) == 10
    assert by_reason["qualified:emerging"] == ["G1"]
    # Leftover established coins and the "other" coin fill unfilled slots, best first.
    assert by_reason["qualified:unfilled_slot"] == ["E10", "E11", "O1"]
    assert len(adds) == 14  # nobody below 70, so fewer than 30


def test_size_cap() -> None:
    cur = {f"T{i}": ev(95 - i * 0.1, group="other") for i in range(40)}
    out = run([], cur, dict(cur))
    assert len([c for c in out if c.action == "add"]) == 30
