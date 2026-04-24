"""Peer-assignment computation.

v1 policy: full mesh of all ACTIVE agents. Every ordered pair becomes a peer_assignment
with `source='auto'`. Admin-created `source='manual'` rows are preserved across
recomputes and never replaced. Disabled rows stay disabled.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import Agent, AgentInterface, PeerAssignment
from pulse_server.repo import meta_repo
from pulse_shared.enums import AgentState


async def _target_ip_for_agent(db: AsyncSession, agent: Agent) -> str:
    """Resolve the IP other agents should ping to reach `agent`.

    Prefer the agent's primary_test interface's current_ip (MAC-tracked, survives DHCP
    churn). Fall back to `Agent.primary_ip` (the legacy agent-reported field) for
    agents that predate the interfaces feature.
    """
    primary = (
        await db.execute(
            select(AgentInterface).where(
                AgentInterface.agent_id == agent.id,
                AgentInterface.role == "test",
            )
        )
    ).scalar_one_or_none()
    if primary is not None and primary.current_ip:
        return primary.current_ip
    return agent.primary_ip or ""


@dataclass(frozen=True)
class RecomputeSummary:
    version: int
    added: int
    removed: int
    kept: int


async def recompute_full_mesh(db: AsyncSession) -> RecomputeSummary:
    active = (
        (await db.execute(select(Agent).where(Agent.state == AgentState.ACTIVE.value)))
        .scalars()
        .all()
    )
    # Monitor-role agents sit outside the ping mesh: they don't initiate pings
    # and nothing pings them. Identify by presence of any role=monitor iface.
    monitor_ids = set(
        (
            await db.execute(
                select(AgentInterface.agent_id).where(
                    AgentInterface.role == "monitor"
                )
            )
        ).scalars().all()
    )
    active = [a for a in active if a.id not in monitor_ids]
    by_id = {a.id: a for a in active}

    existing = (await db.execute(select(PeerAssignment))).scalars().all()
    existing_by_pair = {(pa.source_agent_id, pa.target_agent_id): pa for pa in existing}

    desired_pairs = {(s.id, t.id) for s in active for t in active if s.id != t.id}

    added = 0
    removed = 0
    kept = 0

    # 1. Insert any missing auto pairs; refresh snapshotted target IP on existing.
    for pair in desired_pairs:
        src_id, tgt_id = pair
        existing_row = existing_by_pair.get(pair)
        target_ip = await _target_ip_for_agent(db, by_id[tgt_id])
        if existing_row is None:
            db.add(
                PeerAssignment(
                    source_agent_id=src_id,
                    target_agent_id=tgt_id,
                    target_ip=target_ip,
                    interval_s=None,
                    enabled=True,
                    source="auto",
                )
            )
            added += 1
        else:
            # Keep manual rows as-is except refresh the IP so the agent sees the latest.
            if existing_row.target_ip != target_ip and target_ip:
                existing_row.target_ip = target_ip
            kept += 1

    # 2. Delete auto rows whose endpoints are no longer active. Never touch manual rows.
    for (src_id, tgt_id), pa in existing_by_pair.items():
        if (src_id, tgt_id) in desired_pairs:
            continue
        if pa.source == "manual":
            continue
        await db.delete(pa)
        removed += 1

    version = await meta_repo.bump(db, meta_repo.PEER_ASSIGNMENTS_VERSION)
    await db.commit()
    return RecomputeSummary(version=version, added=added, removed=removed, kept=kept)


async def assignments_for_source(
    db: AsyncSession, source_agent_id: int
) -> list[PeerAssignment]:
    return list(
        (
            await db.execute(
                select(PeerAssignment).where(
                    PeerAssignment.source_agent_id == source_agent_id,
                    PeerAssignment.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
