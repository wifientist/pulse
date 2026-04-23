"""Bulk ingest path for ping samples.

Resolves `target_agent_uid` to internal ids in one query, then issues one multi-row
INSERT. At <25 agents, a single poll yields tens-to-low-hundreds of samples; SQLite
handles this with ease in WAL mode.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import Agent, PassivePingSampleRaw, PingSampleRaw
from pulse_shared.contracts import PingSample

# Sentinel prefix used on target_agent_uid for passive targets. The agent
# doesn't care what the uid looks like — it just pings the IP we send in the
# peer assignment — so we reuse the existing wire contract and route by prefix
# on the server side.
PASSIVE_UID_PREFIX = "passive:"


def _passive_id_from_uid(uid: str) -> int | None:
    if not uid.startswith(PASSIVE_UID_PREFIX):
        return None
    tail = uid[len(PASSIVE_UID_PREFIX):]
    try:
        return int(tail)
    except ValueError:
        return None


async def insert_samples(
    db: AsyncSession,
    source_agent_id: int,
    samples: Iterable[PingSample],
) -> int:
    samples = list(samples)
    if not samples:
        return 0

    # Split inbound samples: passive-target sentinel → separate raw table,
    # everything else → agent peer path.
    agent_samples: list[PingSample] = []
    passive_rows: list[dict] = []
    for s in samples:
        pid = _passive_id_from_uid(s.target_agent_uid)
        if pid is not None:
            passive_rows.append(
                {
                    "source_agent_id": source_agent_id,
                    "passive_target_id": pid,
                    "ts_ms": s.ts_ms,
                    "rtt_ms": s.rtt_ms,
                    "lost": s.lost,
                    "seq": s.seq,
                }
            )
        else:
            agent_samples.append(s)

    if passive_rows:
        await db.execute(insert(PassivePingSampleRaw), passive_rows)

    wanted_uids = {s.target_agent_uid for s in agent_samples}
    uid_to_id: dict[str, int] = {}
    if wanted_uids:
        rows = (
            await db.execute(
                select(Agent.id, Agent.agent_uid).where(Agent.agent_uid.in_(wanted_uids))
            )
        ).all()
        uid_to_id = {uid: pk for pk, uid in rows}

    agent_rows: list[dict] = []
    for s in agent_samples:
        target_id = uid_to_id.get(s.target_agent_uid)
        if target_id is None:
            continue
        agent_rows.append(
            {
                "source_agent_id": source_agent_id,
                "target_agent_id": target_id,
                "ts_ms": s.ts_ms,
                "rtt_ms": s.rtt_ms,
                "lost": s.lost,
                "seq": s.seq,
            }
        )
    if agent_rows:
        await db.execute(insert(PingSampleRaw), agent_rows)
    return len(agent_rows) + len(passive_rows)
