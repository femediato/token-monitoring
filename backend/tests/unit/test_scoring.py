import pytest

from autolp.selection.scoring import COMPONENTS, N, group_of, plateau, score, tent

# A token whose every input is present and "healthy".
FULL = {
    "relvol_ratio": 1.75,
    "trader_growth_ratio": 1.1,
    "tx_accel_ratio": 1.225,
    "active_buckets": 9,
    "buckets_observed": 12,
    "ret_1h_pct": 8.0,
    "ret_4h_pct": 15.0,
    "ema_ratio": 0.02,
    "dist_from_4h_high_pct": 5.0,
    "liquidity_usd": 10**5.7385,
    "vol_liq_ratio": 3.0,
    "sell_cost_pct": 1.1,
    "depth_ratio_24h": 0.85,
    "buy_share_1h": 0.55,
    "net_buy_liq_1h": 0.025,
    "positive_buckets": 6,
    "top10_pct": 27.5,
    "top1_pct": 9.0,
    "organic_label": "medium",
}


def test_normalization_helpers() -> None:
    assert N(1, 0, 2) == 50 and N(-1, 0, 2) == 0 and N(5, 0, 2) == 100 and N(None, 0, 1) is None
    assert tent(-5, -5, 8, 30) == 0 and tent(8, -5, 8, 30) == 100 and tent(30, -5, 8, 30) == 0
    assert tent(19, -5, 8, 30) == pytest.approx(50)
    assert plateau(0.5, 1, 5, 15) == 50 and plateau(3, 1, 5, 15) == 100
    assert plateau(10, 1, 5, 15) == pytest.approx(50) and plateau(20, 1, 5, 15) == 0


def test_component_weights_sum_to_one() -> None:
    for cat, comps in COMPONENTS.items():
        assert sum(w for _, w, _ in comps) == pytest.approx(1.0), cat


def test_full_feature_score_is_reproducible() -> None:
    s = score(FULL)
    assert s.coverage == pytest.approx(1.0)
    c = s.components
    assert c["activity"]["relative_volume"] == pytest.approx(50)
    assert c["activity"]["trader_growth"] == pytest.approx(50)
    assert c["activity"]["transaction_acceleration"] == pytest.approx(50)
    assert c["activity"]["volume_consistency"] == pytest.approx(75)
    assert c["momentum"]["return_1h"] == 100 and c["momentum"]["return_4h"] == 100
    assert c["momentum"]["trend_strength"] == pytest.approx(50)
    assert c["liquidity"]["dex_liquidity"] == pytest.approx(50, abs=0.01)
    assert c["liquidity"]["sell_execution_cost"] == pytest.approx(50)
    assert c["liquidity"]["liquidity_stability"] == pytest.approx(50)
    assert c["buying"]["buy_volume_share"] == pytest.approx(50)
    assert c["buying"]["net_buying_vs_liquidity"] == pytest.approx(50)
    assert c["risk"]["top10_concentration"] == pytest.approx(50)
    assert c["risk"]["largest_holder"] == pytest.approx(50)
    assert c["risk"]["liquidity_control"] == 50 and c["risk"]["trading_quality"] == 50
    assert s.categories["activity"] == pytest.approx(0.85 * 50 + 0.15 * 75)
    expected = sum(
        w * s.categories[k]  # type: ignore[operator]
        for k, w in {
            "activity": 0.25,
            "momentum": 0.25,
            "liquidity": 0.2,
            "buying": 0.15,
            "risk": 0.15,
        }.items()
    )
    assert s.total == pytest.approx(expected)
    assert score(FULL) == s  # deterministic


def test_missing_inputs_reduce_coverage_not_score() -> None:
    f = dict(FULL)
    del f["depth_ratio_24h"]  # token tracked < 24h
    s = score(f)
    assert s.components["liquidity"]["liquidity_stability"] is None
    assert s.coverage == pytest.approx(1 - 0.20 * 0.15)
    # The category is averaged over the components that have data.
    assert s.categories["liquidity"] == pytest.approx(
        (0.35 * 50 + 0.2 * 100 + 0.3 * 50) / 0.85, abs=0.01
    )


def test_flat_activity_lands_mid_scale_not_zero() -> None:
    flat = score({"relvol_ratio": 1.0, "trader_growth_ratio": 1.0, "tx_accel_ratio": 1.0})
    a = flat.components["activity"]
    assert a["relative_volume"] == pytest.approx(20)
    assert a["trader_growth"] == pytest.approx(37.5)
    assert a["transaction_acceleration"] == pytest.approx(28.57, abs=0.01)


def test_few_buckets_observed_is_missing() -> None:
    s = score({"active_buckets": 5, "buckets_observed": 6})
    assert s.components["activity"]["volume_consistency"] is None


@pytest.mark.parametrize(
    ("features", "activity", "group"),
    [
        ({"market_cap": 80e6}, 10, "established"),
        ({"market_cap": 20e6}, 60, "emerging"),
        (
            {"market_cap": 20e6, "ema_ratio": 0.01, "ret_1h_pct": 2, "dist_from_4h_high_pct": 4},
            30,
            "breakout",
        ),
        (
            {"market_cap": 3e6, "ema_ratio": 0.01, "ret_1h_pct": 7, "dist_from_4h_high_pct": 4},
            30,
            "other",
        ),
        ({"market_cap": None}, 90, None),
    ],
)
def test_groups(features: dict, activity: float, group: str | None) -> None:
    assert group_of(features, activity) == group
