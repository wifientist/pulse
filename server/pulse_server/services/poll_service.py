"""Orchestration for the `/v1/agent/poll` hot path.

The router calls :func:`handle_poll` with an authenticated Agent and the parsed request
body. This service updates the agent row, ingests samples, records command results,
leases fresh commands, and materializes the response including peer assignments when the
agent's version is stale.
"""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.config import Settings
from pulse_server.db.models import Agent, AgentInterface
from pulse_server.repo import command_repo, meta_repo, ping_repo
from pulse_server.services import iperf3_orchestrator, peer_service, test_orchestrator
from pulse_shared.contracts import (
    AgentConfig,
    Command as CommandDTO,
    PeerAssignment as PeerAssignmentDTO,
    PollRequest,
    PollResponse,
)
from pulse_shared.enums import AgentState, CommandType


def _now_ms() -> int:
    return int(time.time() * 1000)


def _normalize_mac(mac: str) -> str:
    return mac.strip().lower()


async def _upsert_agent_interfaces(
    db: AsyncSession,
    agent_id: int,
    reported,  # list[AgentInterfaceDTO]
    now_ms: int,
) -> None:
    """Update agent_interfaces table from a poll's `interfaces[]`.

    New MACs get inserted. Known MACs get their `current_ip`/`iface_name`/`last_seen`
    refreshed. MACs we previously knew but weren't reported this poll are left in place
    with their old `last_seen` — staleness is observable in the UI; we don't aggressively
    delete since an interface might temporarily be down.

    If this agent has NO role=test interface yet (first ever report), the first reported
    MAC is auto-classified as role=test so the mesh has a target to ping. Admin can
    reclassify any interface afterward in the UI.
    """
    existing_rows = (
        await db.execute(
            select(AgentInterface).where(AgentInterface.agent_id == agent_id)
        )
    ).scalars().all()
    existing_by_mac = {r.mac: r for r in existing_rows}
    has_test_role = any(r.role == "test" for r in existing_rows)

    for i, iface in enumerate(reported):
        mac = _normalize_mac(iface.mac)
        if not mac:
            continue
        row = existing_by_mac.get(mac)
        if row is None:
            role = "test" if (not has_test_role and i == 0) else "unknown"
            row = AgentInterface(
                agent_id=agent_id,
                mac=mac,
                current_ip=iface.ip,
                iface_name=iface.iface_name,
                role=role,
                first_seen=now_ms,
                last_seen=now_ms,
            )
            db.add(row)
            if role == "test":
                has_test_role = True
        else:
            row.current_ip = iface.ip
            row.iface_name = iface.iface_name
            row.last_seen = now_ms


async def _primary_test_ip_changed(db: AsyncSession, agent_id: int) -> bool:
    """True if this agent's role=test interface IP differs from what peer_assignments
    are currently snapshotting. Bumping peer_assignments_version on a change triggers
    downstream agents to pick up the new target_ip on their next poll."""
    from pulse_server.db.models import PeerAssignment

    primary = (
        await db.execute(
            select(AgentInterface).where(
                AgentInterface.agent_id == agent_id,
                AgentInterface.role == "test",
            )
        )
    ).scalar_one_or_none()
    if primary is None or primary.current_ip is None:
        return False
    stale_count = (
        await db.execute(
            select(PeerAssignment).where(
                PeerAssignment.target_agent_id == agent_id,
                PeerAssignment.target_ip != primary.current_ip,
            )
        )
    ).scalars().all()
    return bool(stale_count)


async def handle_poll(
    db: AsyncSession,
    agent: Agent,
    body: PollRequest,
    settings: Settings,
    source_ip: str | None = None,
) -> PollResponse:
    now = _now_ms()

    # 1. Refresh agent metadata.
    agent.last_poll_at = now
    if body.primary_ip and body.primary_ip != agent.primary_ip:
        agent.primary_ip = body.primary_ip
    if source_ip and source_ip != agent.management_ip:
        agent.management_ip = source_ip
    if isinstance(body.caps, object) and hasattr(body.caps, "model_dump"):
        agent.platform_caps = body.caps.model_dump()
    # The dedicated agent_version column isn't just a cache of caps — the UI reads it
    # as the canonical version. Refresh on every poll so self-upgrades are visible in
    # the agents table (last_poll_at is already moving so this is no extra write).
    reported_version = getattr(body.caps, "agent_version", None)
    if reported_version and reported_version != agent.agent_version:
        agent.agent_version = reported_version
    if agent.state == AgentState.STALE.value:
        agent.state = AgentState.ACTIVE.value

    # 2a. Upsert interface inventory. MAC is the stable key — current_ip updates in
    # place on DHCP renewal without creating duplicate rows. The first interface we
    # ever see from an agent auto-becomes its primary_test so pings have a target on
    # day one; admin can change it later in the UI.
    peer_version_bumped = False
    if body.interfaces:
        await _upsert_agent_interfaces(db, agent.id, body.interfaces, now)
        # If the agent's primary_test interface IP changed from what peer_assignments
        # currently snapshot, bump the mesh version so the change propagates.
        if await _primary_test_ip_changed(db, agent.id):
            await meta_repo.bump(db, meta_repo.PEER_ASSIGNMENTS_VERSION)
            peer_version_bumped = True

    # 2b. Ingest ping samples (bulk).
    await ping_repo.insert_samples(db, agent.id, body.ping_samples)
    _ = peer_version_bumped  # read by step 5 indirectly via meta_repo

    # 3. Record command results, then fan out to the test orchestrator so any linked
    #    Test row can advance its state.
    for r in body.command_results:
        cmd = await command_repo.record_result(
            db,
            command_id=r.command_id,
            agent_id=agent.id,
            success=r.success,
            result=r.result,
            error=r.error,
            now_ms=now,
        )
        if cmd is not None:
            await db.flush()
            # iperf3 commands belong to a multi-step state machine; fall through to the
            # single-command orchestrator only when iperf3 declines.
            handled = await iperf3_orchestrator.handle_command_result(db, cmd)
            if not handled:
                await test_orchestrator.handle_command_result(db, cmd)

    # 4. Lease fresh commands. Must happen after step 3 so the same command isn't
    #    re-leased in the same round-trip it was acknowledged.
    leased = await command_repo.lease_pending_for_agent(db, agent.id, now_ms=now)
    for c in leased:
        await test_orchestrator.handle_command_lease(db, c)

    # 5. Peer-assignment versioning.
    current_version = await meta_repo.get_int(db, meta_repo.PEER_ASSIGNMENTS_VERSION, 0)
    include_peers = body.peers_version_seen != current_version
    peer_dtos: list[PeerAssignmentDTO] | None = None
    if include_peers:
        rows = await peer_service.assignments_for_source(db, agent.id)
        # Need target uids to hand back — fetch in one query via ORM relationships is
        # overkill; just select ids → uids here.
        from pulse_server.db.models import Agent as AgentModel
        from sqlalchemy import select

        target_ids = {r.target_agent_id for r in rows}
        id_to_uid = {}
        if target_ids:
            uid_rows = (
                await db.execute(
                    select(AgentModel.id, AgentModel.agent_uid).where(
                        AgentModel.id.in_(target_ids)
                    )
                )
            ).all()
            id_to_uid = {pk: uid for pk, uid in uid_rows}
        peer_dtos = [
            PeerAssignmentDTO(
                target_agent_uid=id_to_uid[r.target_agent_id],
                target_ip=r.target_ip,
                interval_s=r.interval_s or agent.ping_interval_s,
                enabled=r.enabled,
            )
            for r in rows
            if r.target_agent_id in id_to_uid
        ]

    await db.commit()

    # Refresh IDs after commit (SQLAlchemy assigned them during flush; this is belt &
    # braces in case of autoflush quirks).
    for c in leased:
        if c.id is None:
            await db.refresh(c)

    command_dtos = [
        CommandDTO(
            id=c.id,
            type=CommandType(c.type),
            payload=c.payload if isinstance(c.payload, dict) else {},
            deadline_ms=c.deadline_ms,
        )
        for c in leased
    ]

    return PollResponse(
        server_time_ms=now,
        config=AgentConfig(
            poll_interval_s=agent.poll_interval_s,
            ping_interval_s=agent.ping_interval_s,
        ),
        peer_assignments_version=current_version,
        peer_assignments=peer_dtos,
        commands=command_dtos,
    )
