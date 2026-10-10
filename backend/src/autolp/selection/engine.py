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
from autolp.db.models import ScoreEvaluation, WatchlistEvent, WatchlistMember
from autolp.log import get_logger
from autolp.selection import eligibility, scoring, watchlist
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

    eligible = sum(1 for r in rows.values() if r["eligible"])
    log.info(
        "scoring_done",
        evaluated=len(rows),
        eligible=eligible,
        hourly=hourly,
        changes=len(changes),
    )
    return JobResult(rows_written=len(rows) + len(changes))


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
