"""Bulk ingest path for ping samples.

Resolves `target_agent_uid` to internal ids in one query, then issues one multi-row
INSERT. At <25 agents, a single poll yields tens-to-low-hundreds of samples; SQLite
handles this with ease in WAL mode.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import Agent, PingSampleRaw
from pulse_shared.contracts import PingSample


async def insert_samples(
    db: AsyncSession,
    source_agent_id: int,
    samples: Iterable[PingSample],
) -> int:
    samples = list(samples)
    if not samples:
        return 0

    wanted_uids = {s.target_agent_uid for s in samples}
    rows = (
        await db.execute(select(Agent.id, Agent.agent_uid).where(Agent.agent_uid.in_(wanted_uids)))
    ).all()
    uid_to_id = {uid: pk for pk, uid in rows}

    to_insert: list[dict] = []
    dropped_unknown = 0
    for s in samples:
        target_id = uid_to_id.get(s.target_agent_uid)
        if target_id is None:
            dropped_unknown += 1
            continue
        to_insert.append(
            {
                "source_agent_id": source_agent_id,
                "target_agent_id": target_id,
                "ts_ms": s.ts_ms,
                "rtt_ms": s.rtt_ms,
                "lost": s.lost,
                "seq": s.seq,
            }
        )
    if to_insert:
        await db.execute(insert(PingSampleRaw), to_insert)
    return len(to_insert)
