"""Orchestration for the `/v1/agent/poll` hot path.

The router calls :func:`handle_poll` with an authenticated Agent and the parsed request
body. This service updates the agent row, ingests samples, records command results,
leases fresh commands, and materializes the response including peer assignments when the
agent's version is stale.
"""

from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.config import Settings
from pulse_server.db.models import Agent
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
    if agent.state == AgentState.STALE.value:
        agent.state = AgentState.ACTIVE.value

    # 2. Ingest ping samples (bulk).
    await ping_repo.insert_samples(db, agent.id, body.ping_samples)

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
