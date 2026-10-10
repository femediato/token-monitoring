from autolp.selection.eligibility import check

GOOD = {
    "snapshot_age_minutes": 3,
    "age_days": 30,
    "market_cap": 20e6,
    "liquidity_usd": 400e3,
    "volume_24h": 2e6,
    "trades_24h": 5000,
    "traders_24h": 900,
    "pool_age_hours": 300,
    "security_status": "ok",
    "risk_flags": [],
    "security_age_hours": 2,
    "top10_pct": 25,
    "holders_age_hours": 3,
    "sell_cost_pct": 0.6,
    "quote_error": None,
    "quote_age_minutes": 20,
}


def test_good_token_is_eligible() -> None:
    e = check(GOOD)
    assert e.eligible and not e.critical and e.reasons == [] and e.market_qualified


def test_each_threshold_has_a_reason_code() -> None:
    cases = {
        "age": {"age_days": 6.9},
        "market_cap_below": {"market_cap": 999_999},
        "market_cap_above": {"market_cap": 300_000_001},
        "liquidity": {"liquidity_usd": 149_999},
        "volume_24h": {"volume_24h": 499_999},
        "trades_24h": {"trades_24h": 499},
        "traders_24h": {"traders_24h": 149},
        "pool_age": {"pool_age_hours": 47},
        "top10_concentration": {"top10_pct": 40.1},
        "sell_cost": {"sell_cost_pct": 2.01},
        "stale_snapshot": {"snapshot_age_minutes": 16},
    }
    for code, patch in cases.items():
        e = check({**GOOD, **patch})
        assert not e.eligible and e.reasons == [code], code
        assert not e.critical


def test_missing_safety_data_is_ineligible_never_safe() -> None:
    for key, code in [
        ("security_status", "security_missing"),
        ("top10_pct", "holders_missing"),
        ("quote_age_minutes", "quote_missing"),
    ]:
        e = check({**GOOD, key: None})
        assert not e.eligible and code in e.reasons
    assert "security_missing" in check({**GOOD, "security_age_hours": 49}).reasons


def test_critical_failures() -> None:
    e = check({**GOOD, "security_status": "risky", "risk_flags": ["transfer_fee"]})
    assert e.critical and e.reasons == ["security:transfer_fee"]
    e = check({**GOOD, "security_status": "unknown", "risk_flags": ["unparseable"]})
    assert e.critical and e.reasons == ["security_unknown"]
    e = check({**GOOD, "quote_error": "No routes found", "sell_cost_pct": None})
    assert e.critical and e.reasons == ["no_sell_route"]


def test_market_qualified_ignores_safety_data() -> None:
    e = check({**GOOD, "top10_pct": None})
    assert not e.eligible and e.market_qualified
