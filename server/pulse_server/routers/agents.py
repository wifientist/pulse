from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import Agent
from pulse_server.db.session import get_db
from pulse_server.security.deps import require_admin

router = APIRouter(
    prefix="/v1/admin/agents",
    tags=["admin", "agents"],
    dependencies=[Depends(require_admin)],
)


class AgentView(BaseModel):
    id: int
    agent_uid: str
    hostname: str
    os: str
    state: str
    primary_ip: str | None
    management_ip: str | None
    poll_interval_s: int
    ping_interval_s: int
    created_at: int
    approved_at: int | None
    last_poll_at: int | None
    agent_version: str | None
    caps: dict


def _to_view(a: Agent) -> AgentView:
    return AgentView(
        id=a.id,
        agent_uid=a.agent_uid,
        hostname=a.hostname,
        os=a.os,
        state=a.state,
        primary_ip=a.primary_ip,
        management_ip=a.management_ip,
        poll_interval_s=a.poll_interval_s,
        ping_interval_s=a.ping_interval_s,
        created_at=a.created_at,
        approved_at=a.approved_at,
        last_poll_at=a.last_poll_at,
        agent_version=a.agent_version,
        caps=a.platform_caps if isinstance(a.platform_caps, dict) else {},
    )


@router.get("", response_model=list[AgentView])
async def list_agents(db: AsyncSession = Depends(get_db)) -> list[AgentView]:
    rows = (await db.execute(select(Agent).order_by(desc(Agent.created_at)))).scalars().all()
    return [_to_view(a) for a in rows]


@router.get("/{agent_id}", response_model=AgentView)
async def get_agent(agent_id: int, db: AsyncSession = Depends(get_db)) -> AgentView:
    row = await db.get(Agent, agent_id)
    if row is None:
        raise HTTPException(404, "agent not found")
    return _to_view(row)
