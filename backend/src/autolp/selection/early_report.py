"""Does early detection have predictive value? Alerts vs. matched controls.

For each alert, uplift = alert return − mean return of its matched controls (same moment,
nearest market cap and liquidity). Only alerts with at least one control that has data for
that horizon contribute to the uplift. A positive mean uplift whose 95% interval excludes
zero, across enough alerts, is evidence of predictive value; pre-alert returns show whether
alerts merely follow moves that already happened.
"""

import math
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

HORIZONS = ("ret_1h_pct", "ret_4h_pct", "ret_12h_pct", "ret_24h_pct")


@dataclass
class Stats:
    n: int
    mean: float | None
    median: float | None
    win_rate: float | None  # share > 0
    se: float | None  # standard error of the mean

    @property
    def ci95(self) -> tuple[float, float] | None:
        if self.mean is None or self.se is None:
            return None
        return (self.mean - 1.96 * self.se, self.mean + 1.96 * self.se)


def stats(values: list[float]) -> Stats:
    if not values:
        return Stats(0, None, None, None, None)
    se = statistics.stdev(values) / math.sqrt(len(values)) if len(values) > 1 else None
    return Stats(
        len(values),
        statistics.fmean(values),
        statistics.median(values),
        sum(1 for v in values if v > 0) / len(values),
        se,
    )


def _dump(s: Stats) -> dict[str, Any]:
    return {**asdict(s), "ci95": s.ci95}


def build_report(
    observations: list[dict[str, Any]], alerts: list[dict[str, Any]]
) -> dict[str, Any]:
    """`observations`: rows of alert_observations; `alerts`: rows of early_alerts."""
    by_alert: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: {"alert": [], "control": []}
    )
    for o in observations:
        by_alert[o["alert_id"]][o["role"]].append(o)
    horizons = {}
    for h in HORIZONS:
        alert_vals, control_vals, uplifts = [], [], []
        for group in by_alert.values():
            a = [o[h] for o in group["alert"] if o[h] is not None]
            c = [o[h] for o in group["control"] if o[h] is not None]
            alert_vals += a
            control_vals += c
            if a and c:
                uplifts.append(a[0] - statistics.fmean(c))
        horizons[h] = {
            "alerts": _dump(stats(alert_vals)),
            "controls": _dump(stats(control_vals)),
            "uplift": _dump(stats(uplifts)),
        }
    excursions = {
        role: {
            k: _dump(stats([o[k] for o in observations if o["role"] == role and o[k] is not None]))
            for k in ("mfe_24h_pct", "mae_24h_pct")
        }
        for role in ("alert", "control")
    }
    pre = {
        k: _dump(stats([a[k] for a in alerts if a[k] is not None]))
        for k in ("pre_return_1h_pct", "pre_return_4h_pct")
    }
    entries = [a for a in alerts if a.get("entry_quoted_at") or a.get("entry_error")]
    entry_ok = [
        a for a in entries if a.get("entry_cost_pct") is not None and a["entry_cost_pct"] <= 2
    ]
    return {
        "alerts": len(alerts),
        "with_controls": sum(1 for g in by_alert.values() if g["control"]),
        "horizons": horizons,
        "excursions": excursions,
        "pre_alert": pre,
        "entry": {
            "checked": len(entries),
            "available": len(entry_ok),
            "cost": _dump(
                stats([a["entry_cost_pct"] for a in entries if a.get("entry_cost_pct") is not None])
            ),
        },
    }
