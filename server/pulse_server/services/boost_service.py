"""Per-agent ping-cadence boost.

A boost is a time-bounded flag on an agent: while the row exists and
`expires_at > now`, the agent's outbound pings run at 1 Hz. This supersedes the
old DeepDiveSession concept — the fine-grained data lives in `ping_samples_raw`
and is surfaced through the Trends page's raw tier.
"""

from __future__ import annotations

import time

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import AgentBoost
from pulse_server.repo import meta_repo

BOOST_PING_INTERVAL_S = 1
DEFAULT_DURATION_S = 300  # 5 minutes
MAX_DURATION_S = 3600  # 1 hour


def _now_ms() -> int:
    return int(time.time() * 1000)


async def is_agent_boosted(db: AsyncSession, agent_id: int) -> bool:
    now = _now_ms()
    row = (
        await db.execute(
            select(AgentBoost).where(
                AgentBoost.agent_id == agent_id,
                AgentBoost.expires_at > now,
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def start_or_extend(
    db: AsyncSession, agent_id: int, duration_s: int
) -> AgentBoost:
    """Create or replace the boost row for this agent. A second boost while one is
    already running just extends the expiry — no weird overlapping state."""
    duration_s = max(1, min(int(duration_s), MAX_DURATION_S))
    now = _now_ms()
    payload = {
        "agent_id": agent_id,
        "started_at": now,
        "expires_at": now + duration_s * 1000,
    }
    stmt = sqlite_insert(AgentBoost).values(**payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=["agent_id"],
        set_={
            "started_at": stmt.excluded.started_at,
            "expires_at": stmt.excluded.expires_at,
        },
    )
    await db.execute(stmt)
    # Bump so the agent re-fetches peer_assignments with interval=1 on its next poll.
    await meta_repo.bump(db, meta_repo.PEER_ASSIGNMENTS_VERSION)
    row = (
        await db.execute(
            select(AgentBoost).where(AgentBoost.agent_id == agent_id)
        )
    ).scalar_one_or_none()
    assert row is not None
    return row


async def cancel(db: AsyncSession, agent_id: int) -> bool:
    result = await db.execute(
        delete(AgentBoost).where(AgentBoost.agent_id == agent_id)
    )
    if (result.rowcount or 0) > 0:
        await meta_repo.bump(db, meta_repo.PEER_ASSIGNMENTS_VERSION)
        return True
    return False


async def prune_expired(db: AsyncSession) -> int:
    """Scheduler helper. Deletes rows whose expires_at has passed and bumps
    peer_assignments_version so agents drop back to default cadence promptly."""
    now = _now_ms()
    result = await db.execute(
        delete(AgentBoost).where(AgentBoost.expires_at <= now)
    )
    n = result.rowcount or 0
    if n:
        await meta_repo.bump(db, meta_repo.PEER_ASSIGNMENTS_VERSION)
    return n


async def list_active(db: AsyncSession) -> list[AgentBoost]:
    now = _now_ms()
    rows = (
        await db.execute(
            select(AgentBoost).where(AgentBoost.expires_at > now)
        )
    ).scalars().all()
    return list(rows)
