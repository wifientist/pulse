"""SSE events endpoint smoke tests.

We test the auth gate through the HTTP surface and the snapshot content by calling
`build_snapshot()` directly. Testing the streaming response against httpx's
ASGITransport is fragile — the transport buffers body chunks in ways real HTTP doesn't,
so the useful assertion is on `build_snapshot()`'s return shape, not the SSE framing.
The framing itself is ~5 lines of string formatting in `_sse_frame` and is exercised by
the real browser at runtime.
"""

from __future__ import annotations

import pytest

from pulse_server.routers.events import build_snapshot

from .test_poll import _enroll_and_approve


async def test_events_requires_admin(client) -> None:
    r = await client.get("/v1/admin/events")
    assert r.status_code in (401, 403)


async def test_events_bad_bearer(client) -> None:
    r = await client.get(
        "/v1/admin/events", headers={"Authorization": "Bearer nope"}
    )
    assert r.status_code == 401


async def test_build_snapshot_has_expected_shape(app, client, admin_headers) -> None:
    """Snapshot contains every key the frontend depends on, with an approved agent."""
    uid_a, _ = await _enroll_and_approve(client, admin_headers, "ha", "10.0.0.5")

    async with app.state.sessionmaker() as db:
        payload = await build_snapshot(db)

    assert "emitted_at" in payload
    for key in (
        "agents",
        "pending_enrollments",
        "link_states",
        "peer_assignments",
        "recent_alerts",
    ):
        assert key in payload, f"missing key {key}"
    assert any(a["agent_uid"] == uid_a for a in payload["agents"])
    # Sanity: agent payload includes both primary_ip (test IP) and management_ip slot.
    agent_row = next(a for a in payload["agents"] if a["agent_uid"] == uid_a)
    assert "primary_ip" in agent_row
    assert "management_ip" in agent_row


async def test_build_snapshot_surfaces_pending_enrollments(app, client, admin_headers) -> None:
    # Mint a token and partially enroll (no approval) so a pending row exists.
    r = await client.post(
        "/v1/admin/enrollment-tokens",
        headers=admin_headers,
        json={"label": "ev-test"},
    )
    plaintext = r.json()["plaintext"]

    from pulse_shared.version import PROTOCOL_VERSION

    r = await client.post(
        "/v1/enroll",
        json={
            "enrollment_token": plaintext,
            "hostname": "pending-one",
            "reported_ip": "10.0.0.9",
            "caps": {
                "os": "linux",
                "raw_icmp": True,
                "container": False,
                "iperf3_available": False,
                "agent_version": "0.1.0",
                "protocol_version": PROTOCOL_VERSION,
            },
        },
    )
    assert r.status_code == 200

    async with app.state.sessionmaker() as db:
        payload = await build_snapshot(db)

    assert any(
        p["reported_hostname"] == "pending-one" for p in payload["pending_enrollments"]
    )
