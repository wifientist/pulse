from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import Agent, PeerAssignment
from pulse_server.db.session import get_db
from pulse_server.repo import meta_repo
from pulse_server.security.deps import require_admin
from pulse_server.services import peer_service

router = APIRouter(
    prefix="/v1/admin/peer-assignments",
    tags=["admin", "peers"],
    dependencies=[Depends(require_admin)],
)


class PeerAssignmentView(BaseModel):
    id: int
    source_agent_uid: str
    target_agent_uid: str
    target_ip: str
    interval_s: int | None
    enabled: bool
    source: str


class RecomputeResponse(BaseModel):
    version: int
    added: int
    removed: int
    kept: int


@router.get("", response_model=list[PeerAssignmentView])
async def list_assignments(db: AsyncSession = Depends(get_db)) -> list[PeerAssignmentView]:
    rows = (await db.execute(select(PeerAssignment))).scalars().all()
    ids = {r.source_agent_id for r in rows} | {r.target_agent_id for r in rows}
    id_to_uid: dict[int, str] = {}
    if ids:
        uid_rows = (
            await db.execute(select(Agent.id, Agent.agent_uid).where(Agent.id.in_(ids)))
        ).all()
        id_to_uid = {pk: uid for pk, uid in uid_rows}
    return [
        PeerAssignmentView(
            id=r.id,
            source_agent_uid=id_to_uid.get(r.source_agent_id, ""),
            target_agent_uid=id_to_uid.get(r.target_agent_id, ""),
            target_ip=r.target_ip,
            interval_s=r.interval_s,
            enabled=r.enabled,
            source=r.source,
        )
        for r in rows
    ]


@router.post("/recompute", response_model=RecomputeResponse)
async def recompute(db: AsyncSession = Depends(get_db)) -> RecomputeResponse:
    summary = await peer_service.recompute_full_mesh(db)
    return RecomputeResponse(
        version=summary.version,
        added=summary.added,
        removed=summary.removed,
        kept=summary.kept,
    )


@router.get("/version")
async def current_version(db: AsyncSession = Depends(get_db)) -> dict[str, int]:
    return {
        "version": await meta_repo.get_int(db, meta_repo.PEER_ASSIGNMENTS_VERSION, 0)
    }
