"""Read-only API for paper trading (docs/PAPER_TRADING.md)."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select, text

from autolp.api.explorer import Session, _rows
from autolp.db.models import PaperEvent, PaperFill, PaperPosition
from autolp.paper import report as rep
from autolp.paper.params import PAPER_VERSION

router = APIRouter(prefix="/api/paper", tags=["paper"])


def _row(obj: Any) -> dict[str, Any]:
    return {c.name: getattr(obj, c.key) for c in obj.__table__.columns}


async def _symbols(session: Session, mints: set[str]) -> dict[str, str | None]:
    rows = await session.execute(
        text("SELECT mint, symbol FROM tokens WHERE mint = ANY(:m)"), {"m": sorted(mints)}
    )
    return {m: s for m, s in rows}


@router.get("")
async def paper(session: Session) -> dict[str, Any]:
    """Account, metrics, execution quality, reconciliation, open positions, closed trades,
    the equity curve (15-minute points) and the latest events."""
    acct = await rep.account(session)
    if acct is None:
        return {"version": PAPER_VERSION, "account": None}
    r = await rep.summary(session, acct)
    open_ = [
        _row(p)
        for p in await session.scalars(
            select(PaperPosition)
            .where(
                PaperPosition.account_id == acct.id, PaperPosition.status.in_(["open", "pending"])
            )
            .order_by(PaperPosition.id)
        )
    ]
    curve = _rows(
        await session.execute(
            text(
                """
                SELECT DISTINCT ON (time_bucket('15 minutes', at)) at, equity_usd, cash_usd,
                       positions_usd, open_positions
                FROM paper_equity WHERE account_id = :a
                ORDER BY time_bucket('15 minutes', at), at DESC
                """
            ),
            {"a": acct.id},
        )
    )
    events = [
        _row(e)
        for e in await session.scalars(
            select(PaperEvent)
            .where(PaperEvent.account_id == acct.id)
            .order_by(PaperEvent.id.desc())
            .limit(100)
        )
    ]
    mints = {p["token_mint"] for p in open_} | {t["mint"] for t in r["trades"]}
    mints |= {e["token_mint"] for e in events if e["token_mint"]}
    r["trades"] = r["trades"][::-1][:200]
    return {**r, "open": open_, "equity": curve, "events": events,
            "symbols": await _symbols(session, mints)}  # fmt: skip


@router.get("/positions/{position_id}")
async def position(session: Session, position_id: int) -> dict[str, Any]:
    """One position from signal to exit: its events, every fill attempt with the raw quote
    ids, and the reconciliation result."""
    pos = await session.get(PaperPosition, position_id)
    if pos is None:
        raise HTTPException(404, "paper position not found")
    events = list(
        await session.scalars(
            select(PaperEvent).where(PaperEvent.position_id == pos.id).order_by(PaperEvent.id)
        )
    )
    fills = list(
        await session.scalars(
            select(PaperFill).where(PaperFill.position_id == pos.id).order_by(PaperFill.id)
        )
    )
    return {
        "position": _row(pos),
        "symbol": (await _symbols(session, {pos.token_mint})).get(pos.token_mint),
        "events": [_row(e) for e in events],
        "fills": [{**_row(f), "shortfall_vs_model_pct": rep.shortfall_pct(f)} for f in fills],
        "issues": rep.check_position(pos, events, fills),
    }


@router.get("/events")
async def events(
    session: Session,
    kind: str | None = None,
    limit: int = Query(200, ge=1, le=2000),
) -> dict[str, Any]:
    acct = await rep.account(session)
    if acct is None:
        return {"items": []}
    q = select(PaperEvent).where(PaperEvent.account_id == acct.id)
    if kind:
        q = q.where(PaperEvent.kind == kind)
    rows = [_row(e) for e in await session.scalars(q.order_by(PaperEvent.id.desc()).limit(limit))]
    return {"items": rows}
