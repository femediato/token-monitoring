"""sweep-v3 robustness checks and events-v1 event studies (pure functions)."""

from datetime import UTC, datetime, timedelta

import pytest

from autolp.backtest import events
from autolp.backtest import robustness as rb

D0 = datetime(2026, 10, 10, tzinfo=UTC)


def trade(day: float, ret: float, pnl: float | None = None, regime: str = "down") -> dict:
    at = D0 + timedelta(days=day)
    return {
        "entry_at": at.isoformat(),
        "return_pct": ret,
        "pnl_usd": ret if pnl is None else pnl,
        "regime": regime,
    }


def test_regime_thresholds() -> None:
    assert [rb.regime(x) for x in (None, 60, 50, 40)] == ["unknown", "up", "flat", "down"]


def test_day_block_bootstrap_is_deterministic_and_brackets_the_mean() -> None:
    ts = [trade(d + 0.1 * i, r) for d, r in enumerate([1, -1, 2, 0.5]) for i in range(3)]
    end = D0 + timedelta(days=10)
    ci = rb.bootstrap_ci(ts, D0, end, seed="x")
    assert ci is not None and ci[0] <= 0.625 <= ci[1]
    assert ci == rb.bootstrap_ci(ts, D0, end, seed="x")
    assert rb.bootstrap_ci(ts[:3], D0, end) is None  # 95% >= 2 x 2%, on its one day


def test_folds_roll_and_walk_forward_measures_the_choice_out_of_sample() -> None:
    fs = rb.folds(D0, D0 + timedelta(days=9), fit_days=4, test_days=2)
    assert [(f.fit_start - D0).days for f in fs] == [0, 2]  # a third fold would end on day 10
    assert all(f.test_end <= D0 + timedelta(days=9) for f in fs)
    # A wins in every fitting window but loses afterwards; B is flat.
    a = [trade(d, 10 if d < 4 else -5) for d in range(9)]
    b = [trade(d, 0.1) for d in range(9)]
    wf = rb.walk_forward({"A": a, "B": b}, fs[:1], equity=100, min_trades=2)
    f = wf["folds"][0]
    assert f["chosen"] == "A" and f["fit_pnl_pct"] == 40 and f["test_pnl_pct"] == -10
    assert wf["best_test_pnl_pct"] == -10 == wf["best_if_positive_test_pnl_pct"]
    assert rb.fold_consistency(b, fs, 100) == 100 and rb.fold_consistency([], fs, 100) is None
    none = rb.walk_forward({"A": a}, fs[:1], equity=100, min_trades=50)
    assert none["folds"][0]["chosen"] is None and none["best_test_pnl_pct"] == 0


def test_by_regime_and_verdict() -> None:
    ts = [trade(0, 2, regime="up"), trade(1, -1), trade(2, -3)]
    r = rb.by_regime(ts, D0, D0 + timedelta(days=5))
    assert r["up"] == {"trades": 1, "expectancy_pct": 2} and r["down"]["expectancy_pct"] == -2
    good = {
        "dev_return_pct": 3, "val_trades": 150, "val_return_pct": 1, "val_ci_pct": [0.1, 0.9],
        "baseline": "rand", "uplift_expectancy_pct": 0.5, "group": "g",
        "worst_neighbour_return_pct": 1, "stress_dev_return_pct": 0.2,
    }  # fmt: skip
    assert rb.verdict(good) == []
    bad = {**good, "val_trades": 20, "val_ci_pct": [-0.2, 1], "worst_neighbour_return_pct": -1}
    assert rb.verdict(bad) == [
        "fewer than 100 out-of-sample trades",
        "validation 95% CI includes 0",
        "a neighbour loses",
    ]


def obs(mint: str, h: int, price: float, **f: float | None) -> events.Obs:
    return events.Obs(
        mint, D0 + timedelta(hours=h), f.pop("eligible", 0) == 1, {"price_usd": price, **f}
    )


def test_event_conditions_forward_returns_and_bar() -> None:
    o = [
        obs("A", 0, 1.0, ret_1h_pct=12.0),
        obs("B", 0, 1.0, ret_1h_pct=None),
        obs("C", 0, 2.0, ret_1h_pct=3.0),
    ]
    assert [x.mint for x in o if events.matches(x, {"ret_1h_pct": [10, None]})] == ["A"]
    assert [x.mint for x in o if events.matches(x, {"ret_1h_pct": [None, 10]})] == [
        "C"
    ]  # missing never matches
    prices = {("A", D0 + timedelta(hours=4)): 0.9, ("C", D0 + timedelta(hours=4)): 600.0}
    xs, missing = events.forward(o, prices, 4, D0 + timedelta(days=1))
    assert [x for _, x in xs] == pytest.approx([-10.0, 200.0])
    assert missing == 1  # B has no later price; C is clipped at +200%
    s = events.summarize(xs, missing, round_trip_pct=2.0, multiple=2, seed="s")
    assert (
        s["n"] == 2 and s["net_mean_pct"] == pytest.approx(93.0) and s["clears_bar"] is True
    )  # 95% >= 2 x 2%, on its one day
    assert events.forward(o, prices, 4, D0 + timedelta(hours=3)) == ([], 0)  # horizon not over
