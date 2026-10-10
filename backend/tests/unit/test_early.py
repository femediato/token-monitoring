import pytest

from autolp.selection.early import early_score, match_distance, signals, state_of
from autolp.selection.early_report import build_report, stats

ALL_ON = {
    "volume_1h": 30_000.0,
    "volume_24h": 253_000.0,  # baseline (253000-30000)/23 = 9696 -> 3.1x
    "trader_growth_ratio": 1.4,
    "buy_share_1h": 0.6,
    "depth_ratio_6h": 1.15,
    "ret_1h_pct": 3.0,
    "ema20_gap": 0.01,
    "organic_label": "medium",
}


def test_all_signals_fire() -> None:
    e = early_score(ALL_ON)
    assert all(e.signals.values()) and e.score == 100 and e.state == "high_priority"


@pytest.mark.parametrize(
    ("patch", "signal"),
    [
        ({"volume_1h": 14_000.0, "volume_24h": 237_000.0}, "volume_acceleration"),  # 1.44x
        ({"trader_growth_ratio": 1.29}, "buyer_growth"),
        ({"buy_share_1h": 0.549}, "buying_pressure"),
        ({"depth_ratio_6h": 1.09}, "liquidity_growth"),
        ({"ret_1h_pct": 8.0}, "price_restraint"),
        ({"ema20_gap": 0.0}, "positive_trend"),
        ({"organic_label": "low"}, "participation_quality"),
    ],
)
def test_each_threshold(patch: dict, signal: str) -> None:
    s = signals({**ALL_ON, **patch})
    assert s[signal] is False
    assert sum(1 for v in s.values() if v) == 6


def test_missing_inputs_do_not_fire() -> None:
    s = signals({})
    assert all(v is None for v in s.values())
    assert early_score({}).score == 0 and early_score({}).state == "none"


def test_states_follow_signal_counts() -> None:
    assert state_of(100 * 6 / 7) == "high_priority"
    assert state_of(100 * 5 / 7) == "emerging"
    assert state_of(100 * 4 / 7) == "monitor"
    assert state_of(100 * 3 / 7) == "none"


def test_match_distance() -> None:
    a = {"market_cap": 10e6, "liquidity_usd": 1e6}
    assert match_distance(a, {"market_cap": 100e6, "liquidity_usd": 1e6}) == pytest.approx(1.0)
    assert match_distance(a, {"market_cap": None, "liquidity_usd": 1e6}) is None


def test_stats_and_ci() -> None:
    s = stats([1.0, 2.0, 3.0, 4.0])
    assert s.n == 4 and s.mean == 2.5 and s.median == 2.5 and s.win_rate == 1.0
    assert s.ci95 is not None and s.ci95[0] < 2.5 < s.ci95[1]
    assert stats([]).mean is None


def test_report_uplift_is_paired_by_alert() -> None:
    obs = [
        {
            "alert_id": 1,
            "role": "alert",
            "ret_1h_pct": 5.0,
            "ret_4h_pct": None,
            "ret_12h_pct": None,
            "ret_24h_pct": None,
            "mfe_24h_pct": None,
            "mae_24h_pct": None,
        },
        {
            "alert_id": 1,
            "role": "control",
            "ret_1h_pct": 1.0,
            "ret_4h_pct": None,
            "ret_12h_pct": None,
            "ret_24h_pct": None,
            "mfe_24h_pct": None,
            "mae_24h_pct": None,
        },
        {
            "alert_id": 1,
            "role": "control",
            "ret_1h_pct": 3.0,
            "ret_4h_pct": None,
            "ret_12h_pct": None,
            "ret_24h_pct": None,
            "mfe_24h_pct": None,
            "mae_24h_pct": None,
        },
        {
            "alert_id": 2,
            "role": "alert",
            "ret_1h_pct": -2.0,
            "ret_4h_pct": None,
            "ret_12h_pct": None,
            "ret_24h_pct": None,
            "mfe_24h_pct": None,
            "mae_24h_pct": None,
        },  # no controls
    ]
    alerts = [
        {
            "id": 1,
            "pre_return_1h_pct": 2.0,
            "pre_return_4h_pct": None,
            "entry_cost_pct": 0.5,
            "entry_quoted_at": "x",
            "entry_error": None,
        },
        {
            "id": 2,
            "pre_return_1h_pct": 6.0,
            "pre_return_4h_pct": None,
            "entry_cost_pct": None,
            "entry_quoted_at": None,
            "entry_error": "entry_quote_missed",
        },
    ]
    r = build_report(obs, alerts)
    h1 = r["horizons"]["ret_1h_pct"]
    assert h1["alerts"]["n"] == 2 and h1["controls"]["n"] == 2
    assert h1["uplift"]["n"] == 1 and h1["uplift"]["mean"] == pytest.approx(3.0)  # 5 - mean(1,3)
    assert r["pre_alert"]["pre_return_1h_pct"]["mean"] == pytest.approx(4.0)
    assert r["entry"] == {"checked": 2, "available": 1, "cost": r["entry"]["cost"]}
    assert r["with_controls"] == 1
