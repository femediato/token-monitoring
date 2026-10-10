from datetime import UTC, datetime, timedelta

import pytest

from autolp.selection.features import Snapshot, TokenContext, compute_features

AT = datetime(2026, 10, 11, 12, 4, tzinfo=UTC)


def snap(
    minutes_ago: float,
    price: float,
    v1: float = 6000,
    traders: int = 100,
    buys: int = 40,
    sells: int = 40,
    v5: float = 500,
    b5: float = 300,
    s5: float = 200,
) -> Snapshot:
    return Snapshot(
        at=AT - timedelta(minutes=minutes_ago),
        price=price,
        market_cap=10e6,
        liquidity=300e3,
        organic_label="high",
        windows={
            "5m": {"volume_usd": v5, "buy_volume_usd": b5, "sell_volume_usd": s5},
            "1h": {
                "volume_usd": v1,
                "buy_volume_usd": 0.6 * v1,
                "sell_volume_usd": 0.4 * v1,
                "num_traders": traders,
                "num_buys": buys,
                "num_sells": sells,
                "price_change_pct": 4.0,
            },
            "24h": {"volume_usd": 96000, "num_traders": 900, "num_buys": 3000, "num_sells": 2000},
        },
    )


def ctx(snaps: list[Snapshot], **kw) -> TokenContext:
    return TokenContext(
        mint="M",
        pair_created_at=AT - timedelta(days=30),
        pool_created_at=AT - timedelta(days=10),
        snapshots=snaps,
        depth_now=kw.get("depth_now"),
        depth_24h=kw.get("depth_24h"),
        depth_6h=kw.get("depth_6h"),
        security=None,
        holders=None,
        quote=None,
    )


def series() -> list[Snapshot]:
    # 5 hours of 5-minute samples, price rising linearly from 1.0 to 1.6.
    out = [snap(300 - 5 * i, 1.0 + 0.01 * i) for i in range(61)]
    out[-13] = snap(60, out[-13].price, traders=80, buys=30, sells=30)  # one hour ago
    return out


def test_activity_and_buying_features() -> None:
    f = compute_features(ctx(series()), AT)
    assert f["snapshot_age_minutes"] == 0
    assert f["relvol_ratio"] == pytest.approx(6000 / (96000 / 24))
    assert f["trader_growth_ratio"] == pytest.approx(100 / 80)
    assert f["tx_accel_ratio"] == pytest.approx(80 / 60)
    assert f["buckets_observed"] == 12 and f["active_buckets"] == 12  # 500 >= 6000/24
    assert f["positive_buckets"] == 12
    assert f["buy_share_1h"] == pytest.approx(0.6)
    assert f["net_buy_liq_1h"] == pytest.approx(0.2 * 6000 / 300e3)
    assert f["trades_24h"] == 5000 and f["volume_24h"] == 96000


def test_momentum_features() -> None:
    f = compute_features(ctx(series()), AT)
    assert f["ret_4h_pct"] == pytest.approx((1.6 / 1.12 - 1) * 100)
    assert f["dist_from_4h_high_pct"] == pytest.approx(0)
    assert f["ema_ratio"] > 0 and f["prices_n"] == 61


def test_depth_ratio_and_missing_history() -> None:
    f = compute_features(ctx(series(), depth_now=90.0, depth_24h=100.0), AT)
    assert f["depth_ratio_24h"] == pytest.approx(0.9)
    short = compute_features(ctx(series()[-10:]), AT)
    assert short["ema_ratio"] is None and short["ret_4h_pct"] is None
    assert short["depth_ratio_24h"] is None
    assert short["trader_growth_ratio"] is None  # no snapshot near one hour ago


def test_no_look_ahead() -> None:
    base = compute_features(ctx(series()), AT)
    future = series() + [snap(-5, 99.0, traders=9999), snap(-60, 0.01)]
    assert compute_features(ctx(future), AT) == base


def test_stale_snapshot_age() -> None:
    f = compute_features(ctx(series()[:-6]), AT)
    assert f["snapshot_age_minutes"] == pytest.approx(30)
