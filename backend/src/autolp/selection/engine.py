"""Scoring run: features -> eligibility -> scores -> (hourly) ranking -> watchlist changes.

Runs every 15 minutes. Evaluations at minute < 15 are the hourly evaluations used for
ranking and membership decisions; the others only apply critical safety removals.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autolp.collector.runs import JobResult
from autolp.db.models import (
    AlertObservation,
    EarlyAlert,
    EarlyScore,
    ScoreEvaluation,
    WatchlistEvent,
    WatchlistMember,
)
from autolp.log import get_logger
from autolp.selection import early, eligibility, scoring, watchlist
from autolp.selection.features import compute_features, load_contexts
from autolp.selection.params import MODEL_VERSION

log = get_logger(__name__)


def _json_safe(f: dict[str, Any]) -> dict[str, Any]:
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in f.items()}


def evaluate(f: dict[str, Any]) -> dict[str, Any]:
    """Eligibility and (for eligible tokens) scores for one feature dict."""
    el = eligibility.check(f)
    row: dict[str, Any] = {
        "eligible": el.eligible,
        "critical": el.critical,
        "reasons": el.reasons,
        "features": _json_safe(f),
        "components": None,
        "activity": None,
        "momentum": None,
        "liquidity": None,
        "buying": None,
        "risk": None,
        "total": None,
        "coverage": None,
        "group": None,
    }
    # Spec: only coins that pass the hard checks are scored.
    if el.eligible:
        s = scoring.score(f)
        row.update(
            components={
                **s.components,
                "_reason_codes": scoring.REASON_CODES,
            },
            total=s.total,
            coverage=s.coverage,
            group=scoring.group_of(f, s.categories["activity"]),
            **s.categories,
        )
    return row


async def run_scoring(
    factory: async_sessionmaker[AsyncSession], run_id: int, at: datetime | None = None
) -> JobResult:
    at = (at or datetime.now(UTC)).replace(microsecond=0)
    hourly = at.minute < 15
    async with factory() as session:
        contexts = await load_contexts(session, at)
    rows = {mint: evaluate(compute_features(ctx, at)) for mint, ctx in contexts.items()}
    if hourly:
        ranked = sorted(
            (m for m, r in rows.items() if r["eligible"] and r["total"] is not None),
            key=lambda m: rows[m]["total"],
            reverse=True,
        )
        for i, m in enumerate(ranked, start=1):
            rows[m]["rank"] = i

    async with factory() as session, session.begin():
        if rows:
            await session.execute(
                insert(ScoreEvaluation).values(
                    [
                        {
                            "token_mint": m,
                            "evaluated_at": at,
                            "model_version": MODEL_VERSION,
                            "hourly": hourly,
                            "rank": r.pop("rank", None),
                            **r,
                        }
                        for m, r in rows.items()
                    ]
                )
            )
        changes = await _apply_watchlist(session, rows, at, hourly)
        alerts = await _early_detection(session, rows, at)

    eligible = sum(1 for r in rows.values() if r["eligible"])
    log.info(
        "scoring_done",
        evaluated=len(rows),
        eligible=eligible,
        hourly=hourly,
        changes=len(changes),
        alerts=alerts,
    )
    return JobResult(rows_written=2 * len(rows) + len(changes) + alerts)


def _as_eval(r: Any) -> watchlist.Eval:
    return watchlist.Eval(
        eligible=r["eligible"],
        critical=r["critical"],
        total=r["total"],
        coverage=r["coverage"] or 0.0,
        group=r["group"],
        reasons=tuple(r["reasons"]),
    )


async def _apply_watchlist(
    session: AsyncSession, rows: dict[str, dict[str, Any]], at: datetime, hourly: bool
) -> list[watchlist.Change]:
    members = [
        watchlist.Member(m.token_mint, m.group)
        for m in await session.scalars(select(WatchlistMember))
    ]
    prev_at = await session.scalar(
        select(func.max(ScoreEvaluation.evaluated_at)).where(
            ScoreEvaluation.hourly.is_(True),
            ScoreEvaluation.model_version == MODEL_VERSION,
            ScoreEvaluation.evaluated_at < at - timedelta(minutes=30),
            ScoreEvaluation.evaluated_at >= at - timedelta(minutes=90),
        )
    )
    previous = None
    if prev_at is not None:
        prev_rows = (
            await session.execute(
                select(
                    ScoreEvaluation.token_mint,
                    ScoreEvaluation.eligible,
                    ScoreEvaluation.critical,
                    ScoreEvaluation.total,
                    ScoreEvaluation.coverage,
                    ScoreEvaluation.group,
                    ScoreEvaluation.reasons,
                ).where(
                    ScoreEvaluation.evaluated_at == prev_at,
                    ScoreEvaluation.model_version == MODEL_VERSION,
                )
            )
        ).mappings()
        previous = {r["token_mint"]: _as_eval(r) for r in prev_rows}
    day_start = at.replace(hour=0, minute=0, second=0)
    replacements_today = (
        await session.scalar(
            select(func.count()).where(
                WatchlistEvent.at >= day_start,
                WatchlistEvent.action == "remove",
                WatchlistEvent.reason.in_(["score_below_retain"])
                | WatchlistEvent.reason.like("outscored_by:%"),
            )
        )
        or 0
    )
    ever = await session.scalar(select(func.count()).select_from(WatchlistEvent))
    current = {m: _as_eval(r) for m, r in rows.items()}
    changes = watchlist.decide(
        members,
        current,
        previous,
        at,
        hourly=hourly,
        replacements_today=replacements_today,
        forming=not members and not ever,
    )
    for c in changes:
        session.add(
            WatchlistEvent(
                at=at,
                token_mint=c.mint,
                action=c.action,
                group=c.group,
                reason=c.reason,
                score=c.score,
                details={"previous_hourly_at": prev_at.isoformat() if prev_at else None},
                model_version=MODEL_VERSION,
            )
        )
        if c.action == "remove":
            await session.execute(
                delete(WatchlistMember).where(WatchlistMember.token_mint == c.mint)
            )
        else:
            session.add(
                WatchlistMember(
                    token_mint=c.mint,
                    group=c.group or "other",
                    joined_at=at,
                    entry_score=c.score or 0.0,
                    model_version=MODEL_VERSION,
                )
            )
    return changes


async def _early_detection(
    session: AsyncSession, rows: dict[str, dict[str, Any]], at: datetime
) -> int:
    """Store early-detection scores for every token; alert on eligible tokens reaching the
    alert threshold (with a per-token cooldown) and pick matched controls at the same time."""
    p = early.EARLY
    scores = {m: early.early_score(r["features"]) for m, r in rows.items()}
    if scores:
        await session.execute(
            insert(EarlyScore).values(
                [
                    {
                        "token_mint": m,
                        "evaluated_at": at,
                        "model_version": early.EARLY_MODEL_VERSION,
                        "score": e.score,
                        "state": e.state,
                        "signals": e.signals,
                        "eligible": rows[m]["eligible"],
                    }
                    for m, e in scores.items()
                ]
            )
        )
    recent = set(
        await session.scalars(
            select(EarlyAlert.token_mint).where(
                EarlyAlert.at > at - timedelta(hours=p.alert_cooldown_hours)
            )
        )
    )
    alerted = [
        m
        for m, e in scores.items()
        if rows[m]["eligible"] and e.score >= p.alert_min_score and m not in recent
    ]
    # Controls: eligible, no early signal state at this moment, not alerted recently.
    pool = [
        m
        for m, e in scores.items()
        if rows[m]["eligible"] and e.score < p.monitor and m not in recent and m not in alerted
    ]
    for m in alerted:
        f, e = rows[m]["features"], scores[m]
        alert = EarlyAlert(
            token_mint=m,
            at=at,
            model_version=early.EARLY_MODEL_VERSION,
            score=e.score,
            state=e.state,
            signals=e.signals,
            price_usd=f.get("price_usd"),
            market_cap=f.get("market_cap"),
            liquidity_usd=f.get("liquidity_usd"),
            pre_return_1h_pct=f.get("ret_1h_pct"),
            pre_return_4h_pct=f.get("ret_4h_pct"),
        )
        session.add(alert)
        await session.flush()
        session.add(
            AlertObservation(
                alert_id=alert.id,
                role="alert",
                token_mint=m,
                start_at=at,
                start_price=f.get("price_usd"),
            )
        )
        ranked = sorted(
            (
                (d, c)
                for c in pool
                if (d := early.match_distance(f, rows[c]["features"])) is not None
            ),
            key=lambda x: x[0],
        )[: p.controls_per_alert]
        for d, c in ranked:
            session.add(
                AlertObservation(
                    alert_id=alert.id,
                    role="control",
                    token_mint=c,
                    start_at=at,
                    start_price=rows[c]["features"].get("price_usd"),
                    match_distance=d,
                )
            )
        log.info("early_alert", mint=m, score=e.score, state=e.state, fired=e.fired)
    return len(alerted)
