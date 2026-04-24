from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import Agent, AgentInterface
from pulse_server.db.session import get_db
from pulse_server.repo import command_repo, meta_repo
from pulse_server.security.deps import require_admin, require_agent
from pulse_shared.enums import CommandType, InterfaceRole
from pulse_shared.version import AGENT_VERSION

router = APIRouter(
    prefix="/v1/admin/agents",
    tags=["admin", "agents"],
    dependencies=[Depends(require_admin)],
)

# Where the Docker build bakes the agent source tarball. See server.Dockerfile.
AGENT_PACKAGE_PATH = Path("/app/agent-source.tar.gz")


class InterfaceView(BaseModel):
    id: int
    mac: str
    current_ip: str | None
    iface_name: str | None
    role: str
    ssid: str | None
    bssid: str | None
    signal_dbm: int | None
    first_seen: int
    last_seen: int


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
    interfaces: list[InterfaceView]


class SetInterfaceRoleBody(BaseModel):
    mac: str
    role: str


def _iface_view(i: AgentInterface) -> InterfaceView:
    return InterfaceView(
        id=i.id,
        mac=i.mac,
        current_ip=i.current_ip,
        iface_name=i.iface_name,
        role=i.role,
        ssid=i.ssid,
        bssid=i.bssid,
        signal_dbm=i.signal_dbm,
        first_seen=i.first_seen,
        last_seen=i.last_seen,
    )


async def _load_interfaces(db: AsyncSession, agent_id: int) -> list[InterfaceView]:
    # Order so role=test bubbles to the top, then by recency.
    rows = (
        await db.execute(
            select(AgentInterface)
            .where(AgentInterface.agent_id == agent_id)
            .order_by(AgentInterface.role, AgentInterface.last_seen.desc())
        )
    ).scalars().all()
    return [_iface_view(i) for i in rows]


async def _to_view(db: AsyncSession, a: Agent) -> AgentView:
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
        interfaces=await _load_interfaces(db, a.id),
    )


@router.get("", response_model=list[AgentView])
async def list_agents(db: AsyncSession = Depends(get_db)) -> list[AgentView]:
    rows = (await db.execute(select(Agent).order_by(desc(Agent.created_at)))).scalars().all()
    return [await _to_view(db, a) for a in rows]


@router.get("/{agent_id}", response_model=AgentView)
async def get_agent(agent_id: int, db: AsyncSession = Depends(get_db)) -> AgentView:
    row = await db.get(Agent, agent_id)
    if row is None:
        raise HTTPException(404, "agent not found")
    return await _to_view(db, row)


@router.post("/{agent_id}/set-interface-role", response_model=AgentView)
async def set_interface_role(
    agent_id: int,
    body: SetInterfaceRoleBody,
    db: AsyncSession = Depends(get_db),
) -> AgentView:
    """Classify an agent's interface: test / management / ignored / unknown.

    At most one interface per agent may carry role=test (enforced by demoting any
    other role=test to unknown when a new test interface is assigned). Bumps
    peer_assignments_version so the mesh picks up the new target_ip on the next poll.
    """
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "agent not found")

    try:
        new_role = InterfaceRole(body.role.strip().lower())
    except ValueError:
        valid = [r.value for r in InterfaceRole]
        raise HTTPException(400, f"invalid role '{body.role}'; valid: {valid}")

    mac = body.mac.strip().lower()
    ifaces = (
        await db.execute(
            select(AgentInterface).where(AgentInterface.agent_id == agent_id)
        )
    ).scalars().all()
    target = next((i for i in ifaces if i.mac == mac), None)
    if target is None:
        raise HTTPException(400, f"unknown mac for this agent: {mac}")

    if new_role == InterfaceRole.TEST:
        # Enforce at-most-one test role per agent: demote any other role=test.
        for i in ifaces:
            if i.mac != mac and i.role == InterfaceRole.TEST.value:
                i.role = InterfaceRole.UNKNOWN.value
    prev_role = target.role
    target.role = new_role.value
    await db.flush()

    # Monitor role changes flip the agent in or out of the ping mesh — rebuild
    # peer_assignments so the existing mesh reflects it immediately.
    monitor_involved = (
        new_role == InterfaceRole.MONITOR
        or prev_role == InterfaceRole.MONITOR.value
    )
    if monitor_involved:
        from pulse_server.services import peer_service
        await peer_service.recompute_full_mesh(db)
    else:
        await meta_repo.bump(db, meta_repo.PEER_ASSIGNMENTS_VERSION)
    await db.commit()
    await db.refresh(agent)
    return await _to_view(db, agent)


class DhcpRenewBody(BaseModel):
    iface_name: str


class CommandEnqueuedResponse(BaseModel):
    command_id: int


@router.post("/{agent_id}/dhcp-renew", response_model=CommandEnqueuedResponse)
async def dhcp_renew(
    agent_id: int,
    body: DhcpRenewBody,
    db: AsyncSession = Depends(get_db),
) -> CommandEnqueuedResponse:
    """Tell the agent to force a DHCP release+renew on the given interface.

    Result comes back via the poll channel; the new IP (if changed) shows up on the
    interface's `current_ip` in the next snapshot.
    """
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "agent not found")

    now_ms = int(time.time() * 1000)
    cmd = await command_repo.enqueue(
        db,
        agent_id=agent.id,
        cmd_type=CommandType.DHCP_RENEW,
        payload={"iface_name": body.iface_name},
        deadline_ms=now_ms + 60_000,
    )
    await db.commit()
    return CommandEnqueuedResponse(command_id=cmd.id)


class UpgradeResponse(BaseModel):
    command_id: int
    target_version: str


@router.post("/{agent_id}/upgrade", response_model=UpgradeResponse)
async def upgrade_agent(
    agent_id: int,
    db: AsyncSession = Depends(get_db),
) -> UpgradeResponse:
    """Enqueue a self_upgrade command for the agent. Agent pulls the baked-in tarball
    and atomic-swaps its venv + source on the next poll.
    """
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "agent not found")
    if not AGENT_PACKAGE_PATH.is_file():
        raise HTTPException(
            500,
            "agent package not present on server (rebuild the image with the two-stage "
            "build so /app/agent-source.tar.gz is baked in)",
        )

    now_ms = int(time.time() * 1000)
    cmd = await command_repo.enqueue(
        db,
        agent_id=agent.id,
        cmd_type=CommandType.SELF_UPGRADE,
        payload={
            "url": "/v1/agent/package",
            "version": AGENT_VERSION,
        },
        deadline_ms=now_ms + 10 * 60 * 1000,  # 10 min — downloads + venv build + swap
    )
    await db.commit()
    return UpgradeResponse(command_id=cmd.id, target_version=AGENT_VERSION)


# ---------------------------------------------------------------------------
# Agent-bearer endpoint: agent downloads its own upgrade tarball.
# ---------------------------------------------------------------------------

agent_package_router = APIRouter(prefix="/v1/agent", tags=["agent", "upgrade"])


@agent_package_router.get("/package")
async def get_agent_package(
    _agent: Agent = Depends(require_agent),
) -> FileResponse:
    if not AGENT_PACKAGE_PATH.is_file():
        raise HTTPException(500, "agent package missing on server")
    return FileResponse(
        str(AGENT_PACKAGE_PATH),
        media_type="application/gzip",
        filename="pulse-agent.tar.gz",
        headers={"X-Pulse-Agent-Version": AGENT_VERSION},
    )
