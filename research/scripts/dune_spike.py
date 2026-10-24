"""Phase 4a spike: can Dune's trade history replace waiting months for our own data?

Prerequisites: a Dune account, DUNE__API_KEY in .env, and research/dune/ohlcv_5m.sql saved
as a Dune query (parameters mints, start, end). Then, from backend/:

    uv run python ../research/scripts/dune_spike.py --query-id 1234567 [--days 30] [--tokens 5]

It picks the tokens with the most of our own 5-minute candles in the window, runs the
query, stores the rows under reports/dune/, and compares them with candles_5m:
coverage (share of our candles Dune also has), close-price agreement, run time and,
when Dune reports it, the credits used. The go / no-go goes in docs/RESEARCH.md.
"""

import argparse
import asyncio
import csv
import statistics
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from autolp.config import get_settings
from autolp.db.session import make_engine, make_sessionmaker
from autolp.providers.dune import DuneClient


def _utc(s: str) -> datetime:
    t = datetime.fromisoformat(s.replace(" UTC", "").replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--query-id", type=int, required=True)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--tokens", type=int, default=5)
    ap.add_argument("--out", type=Path, default=Path("../reports/dune"))
    args = ap.parse_args()
    settings = get_settings()
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=args.days)

    db = make_engine(settings)
    async with make_sessionmaker(db)() as s:
        mints = list(
            await s.scalars(
                text(
                    """
                    SELECT c.token_mint FROM candles_5m c JOIN tokens t ON t.primary_pool = c.pool_address
                    WHERE c.is_complete AND c.bucket >= :lo AND c.bucket < :hi
                    GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT :n
                    """
                ),
                {"lo": start, "hi": end, "n": args.tokens},
            )
        )
        ours = {
            (m, b): c
            for m, b, c in await s.execute(
                text(
                    """
                    SELECT c.token_mint, c.bucket, c.close FROM candles_5m c
                    JOIN tokens t ON t.primary_pool = c.pool_address
                    WHERE c.is_complete AND c.token_mint = ANY(:m) AND c.bucket >= :lo AND c.bucket < :hi
                    """
                ),
                {"m": mints, "lo": start, "hi": end},
            )
        }
    await db.dispose()
    print(f"{len(mints)} tokens, {start:%Y-%m-%d} -> {end:%Y-%m-%d}, {len(ours)} of our candles")

    dune = DuneClient(settings.dune)
    t0 = time.monotonic()
    try:
        rows, status, _ = await dune.run(
            args.query_id,
            {"mints": ",".join(mints), "start": f"{start:%Y-%m-%d %H:%M:%S}", "end": f"{end:%Y-%m-%d %H:%M:%S}"},
        )
    finally:
        await dune.aclose()
    secs = time.monotonic() - t0

    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"ohlcv_5m-{end:%Y%m%dT%H%M}.csv"
    if rows:
        with path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    theirs = {(r["mint"], _utc(str(r["bucket"]))): float(r["close"]) for r in rows if r.get("close")}
    both = [k for k in ours if k in theirs]
    diffs = sorted(abs(theirs[k] / ours[k] - 1) * 100 for k in both if ours[k])
    print(f"Dune: {len(rows)} rows in {secs:.0f}s; status fields: {sorted(status)}")
    for key in ("execution_cost_credits", "result_metadata"):
        if key in status:
            print(f"  {key}: {status[key]}")
    print(f"Coverage: {len(both)}/{len(ours)} of our candles ({len(both) / max(1, len(ours)) * 100:.1f}%)")
    print(f"Dune-only buckets (we have none): {len(set(theirs) - set(ours))}")
    if diffs:
        print(
            f"Close difference: median {statistics.median(diffs):.2f}%, "
            f"p90 {diffs[int(0.9 * len(diffs))]:.2f}%, max {diffs[-1]:.2f}%"
        )
    print(f"Rows written to {path}")


if __name__ == "__main__":
    asyncio.run(main())
