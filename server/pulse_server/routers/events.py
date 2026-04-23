"""Server-Sent Events stream for the admin UI.

One endpoint, one connection per UI client. Emits two SSE event types:

    event: snapshot   -- the full admin dashboard state, every ~3s
    event: heartbeat  -- lightweight keepalive, every ~15s

The snapshot is a bundle of the same queries that individual admin routes expose
(agents, pending enrollments, peer assignments, link states, recent alerts). Keeping
everything in one stream means each UI client opens exactly one connection, regardless
of which pages are mounted.

Auth: `require_admin` (same bearer gate as every other admin route). Browsers cannot
set custom headers on native `EventSource`, so the UI uses `fetch-event-source` which
does — nothing special needed server-side.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pulse_server.db.models import (
    AccessPoint,
    AccessPointBssid,
    Agent,
    AgentBoost,
    AgentInterface,
    Alert,
    EnrollmentToken,
    LinkStateRow,
    PassiveLinkStateRow,
    PassiveTarget,
    PeerAssignment,
    PendingEnrollment,
)
from pulse_server.db.session import get_db
from pulse_server.security.deps import require_admin

router = APIRouter(
    prefix="/v1/admin",
    tags=["admin", "events"],
    dependencies=[Depends(require_admin)],
)


SNAPSHOT_INTERVAL_S = 3.0
HEARTBEAT_INTERVAL_S = 15.0
RECENT_ALERTS_WINDOW_MS = 60 * 60 * 1000  # last hour
RECENT_ALERTS_LIMIT = 100


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _uid_map(db: AsyncSession, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = (
        await db.execute(select(Agent.id, Agent.agent_uid).where(Agent.id.in_(ids)))
    ).all()
    return {pk: uid for pk, uid in rows}


async def build_snapshot(db: AsyncSession) -> dict:
    """Assemble the admin dashboard state in a single snapshot.

    Structure mirrors the individual admin routes' view-models. Keys:
      - emitted_at: server epoch ms when the snapshot was built
      - agents: list of full AgentView-shaped dicts
      - pending_enrollments: PendingEnrollmentView-shaped
      - peer_assignments: PeerAssignmentView-shaped (source/target resolved to uids)
      - link_states: LinkStateView-shaped
      - recent_alerts: AlertView-shaped, last hour, newest first, capped
    """
    now = _now_ms()

    agent_rows = (
        await db.execute(select(Agent).order_by(Agent.created_at.desc()))
    ).scalars().all()

    # Pull every interface in one query, then bucket per agent. Avoids N+1.
    iface_rows = (
        await db.execute(select(AgentInterface).order_by(AgentInterface.last_seen.desc()))
    ).scalars().all()
    ifaces_by_agent: dict[int, list[dict]] = {}
    for i in iface_rows:
        ifaces_by_agent.setdefault(i.agent_id, []).append(
            {
                "id": i.id,
                "mac": i.mac,
                "current_ip": i.current_ip,
                "iface_name": i.iface_name,
                "role": i.role,
                "ssid": i.ssid,
                "bssid": i.bssid,
                "signal_dbm": i.signal_dbm,
                "first_seen": i.first_seen,
                "last_seen": i.last_seen,
            }
        )

    agents_payload = [
        {
            "id": a.id,
            "agent_uid": a.agent_uid,
            "hostname": a.hostname,
            "os": a.os,
            "state": a.state,
            "primary_ip": a.primary_ip,
            "management_ip": a.management_ip,
            "poll_interval_s": a.poll_interval_s,
            "ping_interval_s": a.ping_interval_s,
            "created_at": a.created_at,
            "approved_at": a.approved_at,
            "last_poll_at": a.last_poll_at,
            "agent_version": a.agent_version,
            "caps": a.platform_caps if isinstance(a.platform_caps, dict) else {},
            "interfaces": ifaces_by_agent.get(a.id, []),
        }
        for a in agent_rows
    ]

    pending_rows = (
        await db.execute(
            select(PendingEnrollment).where(PendingEnrollment.approved.is_(False))
        )
    ).scalars().all()
    pending_payload = [
        {
            "id": p.id,
            "agent_uid": p.agent_uid_candidate,
            "reported_hostname": p.reported_hostname,
            "reported_ip": p.reported_ip,
            "caps": p.caps if isinstance(p.caps, dict) else {},
            "created_at": p.created_at,
            "approved": p.approved,
        }
        for p in pending_rows
    ]

    # Peer assignments → resolve source/target ids to uids.
    peer_rows = (await db.execute(select(PeerAssignment))).scalars().all()
    link_rows = (await db.execute(select(LinkStateRow))).scalars().all()
    alert_rows = (
        await db.execute(
            select(Alert)
            .where(Alert.at_ts >= now - RECENT_ALERTS_WINDOW_MS)
            .order_by(desc(Alert.at_ts))
            .limit(RECENT_ALERTS_LIMIT)
        )
    ).scalars().all()

    agent_ids = (
        {r.source_agent_id for r in peer_rows}
        | {r.target_agent_id for r in peer_rows}
        | {r.source_agent_id for r in link_rows}
        | {r.target_agent_id for r in link_rows}
        | {r.source_agent_id for r in alert_rows}
        | {r.target_agent_id for r in alert_rows}
    )
    id_to_uid = await _uid_map(db, agent_ids)

    peers_payload = [
        {
            "id": r.id,
            "source_agent_uid": id_to_uid.get(r.source_agent_id, ""),
            "target_agent_uid": id_to_uid.get(r.target_agent_id, ""),
            "target_ip": r.target_ip,
            "interval_s": r.interval_s,
            "enabled": r.enabled,
            "source": r.source,
        }
        for r in peer_rows
    ]
    links_payload = [
        {
            "source_agent_uid": id_to_uid.get(r.source_agent_id, ""),
            "target_agent_uid": id_to_uid.get(r.target_agent_id, ""),
            "state": r.state,
            "since_ts": r.since_ts,
            "loss_pct_1m": r.loss_pct_1m,
            "rtt_p95_1m": r.rtt_p95_1m,
        }
        for r in link_rows
    ]
    alerts_payload = [
        {
            "id": a.id,
            "source_agent_uid": id_to_uid.get(a.source_agent_id, ""),
            "target_agent_uid": id_to_uid.get(a.target_agent_id, ""),
            "from_state": a.from_state,
            "to_state": a.to_state,
            "at_ts": a.at_ts,
            "context": a.context if isinstance(a.context, dict) else {},
        }
        for a in alert_rows
    ]

    # Enrollment tokens — summary fields only. Plaintext is never persisted and never
    # leaves the mint response, so there's no way for the SSE stream to leak it.
    token_rows = (
        await db.execute(select(EnrollmentToken).order_by(desc(EnrollmentToken.created_at)))
    ).scalars().all()
    tokens_payload = [
        {
            "id": t.id,
            "label": t.label,
            "created_at": t.created_at,
            "expires_at": t.expires_at,
            "uses_remaining": t.uses_remaining,
            "revoked": t.revoked,
        }
        for t in token_rows
    ]

    ap_rows = (
        await db.execute(select(AccessPoint).order_by(AccessPoint.name))
    ).scalars().all()
    bssid_rows = (
        await db.execute(select(AccessPointBssid))
    ).scalars().all()
    bssids_by_ap: dict[int, list[str]] = {}
    for bss in bssid_rows:
        bssids_by_ap.setdefault(int(bss.access_point_id), []).append(bss.bssid)
    for lst in bssids_by_ap.values():
        lst.sort()
    aps_payload = [
        {
            "id": r.id,
            "name": r.name,
            "bssids": bssids_by_ap.get(r.id, []),
            "location": r.location,
            "notes": r.notes,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
        }
        for r in ap_rows
    ]

    boost_rows = (await db.execute(select(AgentBoost))).scalars().all()
    boost_agent_ids = {int(b.agent_id) for b in boost_rows}
    boost_uid_map = await _uid_map(db, boost_agent_ids)
    boosts_payload = [
        {
            "agent_id": b.agent_id,
            "agent_uid": boost_uid_map.get(b.agent_id, ""),
            "started_at": b.started_at,
            "expires_at": b.expires_at,
        }
        for b in boost_rows
    ]

    passive_rows = (
        await db.execute(select(PassiveTarget).order_by(PassiveTarget.name))
    ).scalars().all()
    passive_payload = [
        {
            "id": p.id,
            "name": p.name,
            "ip": p.ip,
            "notes": p.notes,
            "enabled": p.enabled,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        }
        for p in passive_rows
    ]

    passive_link_rows = (
        await db.execute(select(PassiveLinkStateRow))
    ).scalars().all()
    plink_src_ids = {r.source_agent_id for r in passive_link_rows}
    plink_uid_map = await _uid_map(db, plink_src_ids)
    passive_links_payload = [
        {
            "source_agent_uid": plink_uid_map.get(r.source_agent_id, ""),
            "passive_target_id": r.passive_target_id,
            "state": r.state,
            "since_ts": r.since_ts,
            "loss_pct_1m": r.loss_pct_1m,
            "rtt_p95_1m": r.rtt_p95_1m,
        }
        for r in passive_link_rows
    ]

    return {
        "emitted_at": now,
        "agents": agents_payload,
        "pending_enrollments": pending_payload,
        "peer_assignments": peers_payload,
        "link_states": links_payload,
        "recent_alerts": alerts_payload,
        "enrollment_tokens": tokens_payload,
        "access_points": aps_payload,
        "boosts": boosts_payload,
        "passive_targets": passive_payload,
        "passive_link_states": passive_links_payload,
    }


def _sse_frame(event: str, data: dict) -> bytes:
    payload = json.dumps(data, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode()


async def _snapshot_generator(
    request: Request,
    sessionmaker: async_sessionmaker,
) -> AsyncIterator[bytes]:
    last_heartbeat = 0.0

    while True:
        if await request.is_disconnected():
            return

        async with sessionmaker() as db:
            snapshot = await build_snapshot(db)
        yield _sse_frame("snapshot", snapshot)

        # Sleep until the next snapshot tick, but emit a heartbeat between ticks if the
        # inter-snapshot interval grows past the heartbeat budget.
        elapsed = 0.0
        while elapsed < SNAPSHOT_INTERVAL_S:
            await asyncio.sleep(min(1.0, SNAPSHOT_INTERVAL_S - elapsed))
            elapsed += 1.0
            if await request.is_disconnected():
                return
            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL_S:
                yield _sse_frame("heartbeat", {"ts": _now_ms()})
                last_heartbeat = now


@router.get("/events")
async def admin_events(request: Request) -> StreamingResponse:
    sessionmaker = request.app.state.sessionmaker
    generator = _snapshot_generator(request, sessionmaker)
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
