from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import Agent, Alert, LinkStateRow
from pulse_server.db.session import get_db
from pulse_server.security.deps import require_admin

router = APIRouter(
    prefix="/v1/admin",
    tags=["admin", "alerts"],
    dependencies=[Depends(require_admin)],
)


class AlertView(BaseModel):
    id: int
    source_agent_uid: str
    target_agent_uid: str
    from_state: str
    to_state: str
    at_ts: int
    context: dict


class LinkStateView(BaseModel):
    source_agent_uid: str
    target_agent_uid: str
    state: str
    since_ts: int
    loss_pct_1m: float | None
    rtt_p95_1m: float | None


async def _uid_map(db: AsyncSession, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = (
        await db.execute(select(Agent.id, Agent.agent_uid).where(Agent.id.in_(ids)))
    ).all()
    return {pk: uid for pk, uid in rows}


@router.get("/alerts", response_model=list[AlertView])
async def list_alerts(
    db: AsyncSession = Depends(get_db),
    since_ts: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[AlertView]:
    stmt = select(Alert).order_by(desc(Alert.at_ts)).limit(limit)
    if since_ts is not None:
        stmt = stmt.where(Alert.at_ts >= since_ts)
    rows = (await db.execute(stmt)).scalars().all()
    ids = {r.source_agent_id for r in rows} | {r.target_agent_id for r in rows}
    uid = await _uid_map(db, ids)
    return [
        AlertView(
            id=r.id,
            source_agent_uid=uid.get(r.source_agent_id, ""),
            target_agent_uid=uid.get(r.target_agent_id, ""),
            from_state=r.from_state,
            to_state=r.to_state,
            at_ts=r.at_ts,
            context=r.context if isinstance(r.context, dict) else {},
        )
        for r in rows
    ]


@router.get("/pings/latest", response_model=list[LinkStateView])
async def latest_link_states(db: AsyncSession = Depends(get_db)) -> list[LinkStateView]:
    rows = (await db.execute(select(LinkStateRow))).scalars().all()
    ids = {r.source_agent_id for r in rows} | {r.target_agent_id for r in rows}
    uid = await _uid_map(db, ids)
    return [
        LinkStateView(
            source_agent_uid=uid.get(r.source_agent_id, ""),
            target_agent_uid=uid.get(r.target_agent_id, ""),
            state=r.state,
            since_ts=r.since_ts,
            loss_pct_1m=r.loss_pct_1m,
            rtt_p95_1m=r.rtt_p95_1m,
        )
        for r in rows
    ]
