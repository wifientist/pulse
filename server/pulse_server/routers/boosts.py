"""Admin endpoints to toggle per-agent boost (1 Hz ping cadence, auto-expiring)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import Agent
from pulse_server.db.session import get_db
from pulse_server.security.deps import require_admin
from pulse_server.services import boost_service

router = APIRouter(
    prefix="/v1/admin/agents",
    tags=["admin", "boost"],
    dependencies=[Depends(require_admin)],
)


class BoostStartBody(BaseModel):
    duration_s: int = Field(
        default=boost_service.DEFAULT_DURATION_S,
        ge=1,
        le=boost_service.MAX_DURATION_S,
    )


class BoostView(BaseModel):
    agent_id: int
    agent_uid: str
    started_at: int
    expires_at: int


@router.post("/{agent_id}/boost", response_model=BoostView)
async def start_boost(
    body: BoostStartBody,
    agent_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> BoostView:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    row = await boost_service.start_or_extend(db, agent_id, body.duration_s)
    await db.commit()
    return BoostView(
        agent_id=row.agent_id,
        agent_uid=agent.agent_uid,
        started_at=row.started_at,
        expires_at=row.expires_at,
    )


@router.delete("/{agent_id}/boost", status_code=204)
async def cancel_boost(
    agent_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> None:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    await boost_service.cancel(db, agent_id)
    await db.commit()
