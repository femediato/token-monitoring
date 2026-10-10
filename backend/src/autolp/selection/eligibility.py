"""Hard eligibility rules (spec v1.0 section 2, as adopted in model v1.1).

Missing or stale safety data makes a token ineligible; it is never assumed safe.
Critical reasons (failed safety checks, no sell route) trigger immediate watchlist removal.
"""

from dataclasses import dataclass
from typing import Any

from autolp.selection.params import ELIGIBILITY, EligibilityParams


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    critical: bool
    reasons: list[str]
    market_qualified: bool  # passes the market/activity rules (ignores safety data)


def check(f: dict[str, Any], p: EligibilityParams = ELIGIBILITY) -> Eligibility:
    market: list[str] = []
    safety: list[str] = []
    critical = False

    age = f.get("snapshot_age_minutes")
    if age is None or age > p.max_snapshot_age_minutes:
        market.append("stale_snapshot")
    if f.get("age_days") is None:
        market.append("age_unknown")
    elif f["age_days"] < p.min_age_days:
        market.append("age")
    mc = f.get("market_cap")
    if mc is None:
        market.append("market_cap_unknown")
    elif mc < p.min_market_cap:
        market.append("market_cap_below")
    elif mc > p.max_market_cap:
        market.append("market_cap_above")
    for key, minimum, code in (
        ("liquidity_usd", p.min_liquidity_usd, "liquidity"),
        ("volume_24h", p.min_volume_24h_usd, "volume_24h"),
        ("trades_24h", p.min_trades_24h, "trades_24h"),
        ("traders_24h", p.min_traders_24h, "traders_24h"),
    ):
        if f.get(key) is None:
            market.append(f"{code}_unknown")
        elif f[key] < minimum:
            market.append(code)
    if f.get("pool_age_hours") is None:
        market.append("pool_age_unknown")
    elif f["pool_age_hours"] < p.min_pool_age_hours:
        market.append("pool_age")

    sec_age = f.get("security_age_hours")
    if f.get("security_status") is None or sec_age is None or sec_age > p.max_security_age_hours:
        safety.append("security_missing")
    else:
        flags = [x for x in f.get("risk_flags") or [] if x in p.critical_flags]
        if flags:
            safety += [f"security:{x}" for x in flags]
            critical = True
        elif f["security_status"] != "ok":
            safety.append(f"security_{f['security_status']}")
            critical = True

    h_age = f.get("holders_age_hours")
    if f.get("top10_pct") is None or h_age is None or h_age > p.max_holders_age_hours:
        safety.append("holders_missing")
    elif f["top10_pct"] > p.max_top10_pct:
        safety.append("top10_concentration")

    q_age = f.get("quote_age_minutes")
    if q_age is None or q_age > p.max_quote_age_minutes:
        safety.append("quote_missing")
    elif f.get("quote_error") or f.get("sell_cost_pct") is None:
        safety.append("no_sell_route")
        critical = True
    elif f["sell_cost_pct"] > p.max_sell_cost_pct:
        safety.append("sell_cost")

    reasons = market + safety
    return Eligibility(not reasons, critical, reasons, market_qualified=not market)
