"""Read-only dashboard analytics (docs/DASHBOARD.md): performance, signal accuracy and the
annotated price chart of the trading-signals screen."""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from autolp.analytics import performance as perf
from autolp.analytics.signals import ANALYTICS_VERSION, accuracy, signal_outcome
from autolp.api.explorer import Session, _rows
from autolp.db.base import utcnow
from autolp.paper import report as rep
from autolp.strategies.bars import Bar
from autolp.strategies.params import PARAMS

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

MAX_CHART_DAYS = 14


async def _bars(
    session: Session, mints: list[str], lo: datetime, hi: datetime
) -> dict[str, list[Bar]]:
    """Completed 5-minute candles of each token's primary pool: the strategies' prices."""
    rows = await session.execute(
        text(
            """
            SELECT c.token_mint, c.bucket, c.open, c.high, c.low, c.close, c.volume_usd
            FROM candles_5m c JOIN tokens t ON t.primary_pool = c.pool_address
            WHERE c.token_mint = ANY(:m) AND c.is_complete AND c.bucket >= :lo AND c.bucket < :hi
            ORDER BY c.token_mint, c.bucket
            """
        ),
        {"m": mints, "lo": lo, "hi": hi},
    )
    out: dict[str, list[Bar]] = defaultdict(list)
    for r in rows.mappings():
        out[r["token_mint"]].append(
            Bar(r["bucket"], r["open"], r["high"], r["low"], r["close"], r["volume_usd"])
        )
    return out


async def signal_rows(
    session: Session, since: datetime, until: datetime, mint: str | None = None
) -> list[dict[str, Any]]:
    """Every paper signal in [since, until) with the risk decision, the paper position it
    became (if any) and its ideal outcome on the candles."""
    acct = await rep.account(session)
    if acct is None:
        return []
    rows = _rows(
        await session.execute(
            text(
                """
                SELECT s.id, s.at, s.strategy, s.token_mint AS mint, t.symbol, s.signal_bar,
                       s.data, s.position_id, r.reason AS reject_reason,
                       p.status, p.entry_at, p.entry_price, p.exit_at, p.exit_price,
                       p.exit_reason, p.pnl_usd, p.size_usd, p.last_price, p.qty,
                       p.cancel_reason
                FROM paper_events s
                JOIN tokens t ON t.mint = s.token_mint
                LEFT JOIN paper_events r ON r.account_id = s.account_id AND r.kind = 'reject'
                     AND r.strategy = s.strategy AND r.token_mint = s.token_mint
                     AND r.signal_bar = s.signal_bar
                LEFT JOIN paper_positions p ON p.id = s.position_id
                WHERE s.account_id = :a AND s.kind = 'signal' AND s.at >= :lo AND s.at < :hi
                  AND (CAST(:mint AS text) IS NULL OR s.token_mint = :mint)
                ORDER BY s.at DESC, s.id DESC
                """
            ),
            {"a": acct.id, "lo": since, "hi": until, "mint": mint},
        )
    )
    if not rows:
        return []
    now = utcnow()
    longest = max(r["data"]["max_hold_hours"] for r in rows)
    bars = await _bars(
        session,
        sorted({r["mint"] for r in rows}),
        min(r["at"] for r in rows) - timedelta(minutes=5),
        min(now, max(r["at"] for r in rows) + timedelta(hours=longest + 1)),
    )
    out = []
    for r in rows:
        d = r.pop("data")
        o = signal_outcome(
            r["at"], d["stop"], d["target"], d["max_hold_hours"], d["cost_pct"],
            bars.get(r["mint"], []), now,
        )  # fmt: skip
        if r["position_id"] is None:
            decision = "rejected"
        else:
            decision = "taken" if r["status"] in ("open", "closed") else "cancelled"
        pnl, size = r["pnl_usd"], r["size_usd"]
        out.append(
            {
                **r,
                "entry_ref": d["entry_ref"],
                "stop": d["stop"],
                "target": d["target"],
                "max_hold_hours": d["max_hold_hours"],
                "cost_pct": d["cost_pct"],
                "decision": decision,
                "paper_return_pct": pnl / size * 100 if pnl is not None and size else None,
                "outcome": o.as_dict(),
            }
        )
    return out


@router.get("/signals")
async def signals(session: Session, days: float = Query(30, gt=0, le=365)) -> dict[str, Any]:
    """Signal accuracy: every paper signal with its ideal outcome, and the summary."""
    now = utcnow()
    rows = await signal_rows(session, now - timedelta(days=days), now + timedelta(minutes=1))
    taken_rejected = [r for r in rows if r["decision"] != "cancelled"]
    return {
        "version": ANALYTICS_VERSION,
        "days": days,
        "accuracy": accuracy(taken_rejected),
        "items": rows,
    }


@router.get("/chart/{mint}")
async def chart(
    session: Session,
    mint: str,
    start: datetime | None = None,
    end: datetime | None = None,
    hours: float = Query(48, gt=0, le=24 * MAX_CHART_DAYS),
) -> dict[str, Any]:
    """Candles of one token with every paper signal in the window: decision levels,
    entries, exits and ideal outcomes, for the annotated chart."""
    sym = await session.execute(
        text("SELECT symbol, primary_pool FROM tokens WHERE mint = :m"), {"m": mint}
    )
    tok = sym.first()
    if tok is None:
        raise HTTPException(404, "token not found")
    end = (end or utcnow()).astimezone(UTC)
    start = (start or end - timedelta(hours=hours)).astimezone(UTC)
    if not start < end or end - start > timedelta(days=MAX_CHART_DAYS):
        raise HTTPException(422, f"window must be positive and at most {MAX_CHART_DAYS} days")
    bars = (await _bars(session, [mint], start, end)).get(mint, [])
    # Signals whose holding period overlaps the window.
    longest = max(PARAMS.momentum.exit.max_hold_hours, PARAMS.pullback.exit.max_hold_hours,
                  PARAMS.range.max_hold_hours)  # fmt: skip
    sigs = await signal_rows(session, start - timedelta(hours=longest), end, mint)
    return {
        "mint": mint,
        "symbol": tok.symbol,
        "pool": tok.primary_pool,
        "start": start,
        "end": end,
        "candles": [
            {
                "t": b.start,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume_usd": b.volume_usd,
            }  # fmt: skip
            for b in bars
        ],
        "signals": sigs,
    }


@router.get("/performance")
async def performance(session: Session) -> dict[str, Any]:
    """Paper performance: summary metrics, equity curve, per-strategy comparison, daily
    results, trading costs and execution quality."""
    acct = await rep.account(session)
    if acct is None:
        return {"version": ANALYTICS_VERSION, "account": None}
    r = await rep.summary(session, acct)
    trades = r["trades"]
    curve = _rows(
        await session.execute(
            text(
                """
                SELECT DISTINCT ON (time_bucket('15 minutes', at)) at, equity_usd,
                       positions_usd, open_positions
                FROM paper_equity WHERE account_id = :a
                ORDER BY time_bucket('15 minutes', at), at DESC
                """
            ),
            {"a": acct.id},
        )
    )
    closes = await session.execute(
        text(
            """
            SELECT DISTINCT ON ((at AT TIME ZONE 'UTC')::date) (at AT TIME ZONE 'UTC')::date,
                   equity_usd
            FROM paper_equity WHERE account_id = :a
            ORDER BY (at AT TIME ZONE 'UTC')::date, at DESC
            """
        ),
        {"a": acct.id},
    )
    return {
        "version": ANALYTICS_VERSION,
        "paper_version": r["version"],
        "account": r["account"],
        "running_since": r["running_since"],
        "progress": r["progress"],
        "metrics": r["metrics"],
        "execution": r["execution"],
        "by_strategy": perf.by_strategy(trades, PARAMS.enabled),
        "daily": perf.daily(trades, [(d, e) for d, e in closes], acct.starting_equity_usd),
        "equity": curve,
    }
