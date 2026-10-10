"""Collection acceptance report.

    python -m autolp.monitoring.report --hours 72

PASS requires, over the window: every job ran; 5-minute jobs >= 95% coverage with no gap
longer than 30 minutes; median per-token snapshot coverage >= 95%; <= 5% stale active tokens.
"""

import argparse
import asyncio
import sys
from datetime import timedelta

from sqlalchemy import text

from autolp.collector.schedule import expected_minutes
from autolp.config import get_settings
from autolp.db.base import utcnow
from autolp.db.session import make_engine, make_sessionmaker
from autolp.monitoring.health import freshness, job_health

TOKEN_COVERAGE_SQL = text(
    """
    WITH per_token AS (
        SELECT s.token_mint, count(DISTINCT time_bucket('5 minutes', s.observed_at)) AS buckets
        FROM market_snapshots s
        JOIN tokens t ON t.mint = s.token_mint
        WHERE s.source = 'jupiter' AND s.observed_at >= :since
          AND t.tracking_status = 'active'
          AND coalesce(t.tracking_changed_at, t.first_seen_at) <= :since
        GROUP BY s.token_mint
    )
    SELECT count(*),
           percentile_cont(0.5) WITHIN GROUP (ORDER BY buckets),
           percentile_cont(0.1) WITHIN GROUP (ORDER BY buckets)
    FROM per_token
    """
)

ROWS_SQL = text(
    """
    SELECT 'raw_observations' AS t, count(*) FROM raw_observations WHERE fetched_at >= :since
    UNION ALL SELECT 'market_snapshots', count(*) FROM market_snapshots WHERE observed_at >= :since
    UNION ALL SELECT 'pool_snapshots', count(*) FROM pool_snapshots WHERE observed_at >= :since
    UNION ALL SELECT 'candles_5m', count(*) FROM candles_5m WHERE fetched_at >= :since
    UNION ALL SELECT 'token_security', count(*) FROM token_security WHERE checked_at >= :since
    UNION ALL SELECT 'holder_snapshots', count(*) FROM holder_snapshots WHERE observed_at >= :since
    UNION ALL SELECT 'swap_quotes', count(*) FROM swap_quotes WHERE quoted_at >= :since
    UNION ALL SELECT 'token_discoveries', count(*) FROM token_discoveries
              WHERE discovered_at >= :since
    """
)

CREDITS_SQL = text(
    """
    SELECT job, sum(api_credits_used) FROM collector_runs WHERE started_at >= :since
    GROUP BY job ORDER BY job
    """
)


async def build_report(hours: float) -> tuple[str, bool]:
    settings = get_settings()
    engine = make_engine(settings)
    since = utcnow() - timedelta(hours=hours)
    expected = expected_minutes(settings)
    lines: list[str] = [
        f"Collection report: last {hours:g}h (since {since:%Y-%m-%d %H:%M} UTC)",
        "",
    ]
    failures: list[str] = []
    try:
        async with make_sessionmaker(engine)() as session:
            jobs = await job_health(session, expected, since)
            fresh = await freshness(session, settings)
            n_tokens, p50, p10 = (await session.execute(TOKEN_COVERAGE_SQL, {"since": since})).one()
            rows = (await session.execute(ROWS_SQL, {"since": since})).all()
            credits = (await session.execute(CREDITS_SQL, {"since": since})).all()
    finally:
        await engine.dispose()

    lines.append(
        f"{'job':18} {'every':>5} {'runs':>5} {'ok':>5} {'fail':>4} {'cover%':>7} "
        f"{'maxgap':>7} {'gaps':>4}  status"
    )
    for j in jobs:
        lines.append(
            f"{j.job:18} {j.expected_every_minutes:>4}m {j.runs:>5} {j.successes:>5} "
            f"{j.failures:>4} {j.coverage_pct:>7} {j.max_gap_minutes or 0:>6}m {j.gaps:>4}  "
            f"{j.status}"
        )
        if j.status == "never_ran":
            failures.append(f"{j.job} never ran")
        if j.expected_every_minutes <= 5:
            if j.coverage_pct < 95:
                failures.append(f"{j.job} coverage {j.coverage_pct}% < 95%")
            if (j.max_gap_minutes or 0) > 30:
                failures.append(f"{j.job} max gap {j.max_gap_minutes}m > 30m")

    expected_buckets = hours * 60 / settings.collection.snapshot_minutes
    lines += ["", "Per-token snapshot coverage (tokens active for the whole window):"]
    if n_tokens:
        p50_pct, p10_pct = p50 / expected_buckets * 100, p10 / expected_buckets * 100
        lines.append(f"  tokens={n_tokens}  median={p50_pct:.1f}%  p10={p10_pct:.1f}%")
        if p50_pct < 95:
            failures.append(f"median token coverage {p50_pct:.1f}% < 95%")
    else:
        lines.append("  no tokens were active for the whole window yet")
        failures.append("no tokens tracked for the whole window")

    lines += ["", "Freshness:"] + [f"  {k}: {v}" for k, v in fresh.as_dict().items()]
    if fresh.tracked_active and fresh.active_stale / fresh.tracked_active > 0.05:
        failures.append(f"{fresh.active_stale} of {fresh.tracked_active} active tokens stale")

    lines += ["", "Rows written:"] + [f"  {t:18} {n:>10}" for t, n in rows]
    lines += ["", "Provider requests by job:"] + [f"  {j:18} {n:>10}" for j, n in credits]
    passed = not failures
    lines += ["", "RESULT: " + ("PASS" if passed else "FAIL")] + [f"  - {f}" for f in failures]
    return "\n".join(lines), passed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=72)
    args = parser.parse_args()
    report, passed = asyncio.run(build_report(args.hours))
    print(report)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
