"""Integration tests for the agent poll hot path."""

from __future__ import annotations

import time

import pytest
from sqlalchemy import select

from pulse_server.db.models import Agent, Command, CommandResult, PingSampleRaw
from pulse_server.repo import command_repo, meta_repo
from pulse_shared.enums import CommandStatus, CommandType
from pulse_shared.version import PROTOCOL_VERSION

from .test_enrollment import CAPS


async def _enroll_and_approve(client, admin_headers, hostname: str, ip: str) -> tuple[str, str]:
    """Returns (agent_uid, agent_token)."""
    r = await client.post(
        "/v1/admin/enrollment-tokens",
        headers=admin_headers,
        json={"label": f"t-{hostname}"},
    )
    enroll_token = r.json()["plaintext"]

    r = await client.post(
        "/v1/enroll",
        json={
            "enrollment_token": enroll_token,
            "hostname": hostname,
            "reported_ip": ip,
            "caps": CAPS,
        },
    )
    agent_uid = r.json()["agent_uid"]

    r = await client.get("/v1/admin/enrollments/pending", headers=admin_headers)
    pending_id = next(p["id"] for p in r.json() if p["agent_uid"] == agent_uid)

    await client.post(
        f"/v1/admin/enrollments/{pending_id}/approve", headers=admin_headers, json={}
    )

    r = await client.post(
        "/v1/enroll/poll",
        json={"enrollment_token": enroll_token, "agent_uid": agent_uid},
    )
    agent_token = r.json()["agent_token"]
    return agent_uid, agent_token


def _agent_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _poll_body(agent_uid: str, **overrides) -> dict:
    body = {
        "agent_uid": agent_uid,
        "now_ms": int(time.time() * 1000),
        "caps": CAPS,
        "primary_ip": "10.0.0.5",
        "ping_samples": [],
        "command_results": [],
        "peers_version_seen": 0,
        "dropped_samples_since_last": 0,
    }
    body.update(overrides)
    return body


async def test_poll_requires_agent_bearer(client):
    r = await client.post("/v1/agent/poll", json=_poll_body("x"))
    assert r.status_code in (401, 403)


async def test_poll_rejects_mismatched_agent_uid(client, admin_headers):
    uid, token = await _enroll_and_approve(client, admin_headers, "h1", "10.0.0.5")
    r = await client.post(
        "/v1/agent/poll",
        headers=_agent_headers(token),
        json=_poll_body("not-my-uid"),
    )
    assert r.status_code == 403


async def test_poll_roundtrip_basic(client, admin_headers):
    uid, token = await _enroll_and_approve(client, admin_headers, "h1", "10.0.0.5")

    r = await client.post(
        "/v1/agent/poll", headers=_agent_headers(token), json=_poll_body(uid)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["config"] == {"poll_interval_s": 1, "ping_interval_s": 1}
    assert body["commands"] == []
    # After approval the recompute bumped version from 0 -> 1. Our poll sent 0, so we
    # should have received peer_assignments (empty list for a solo agent).
    assert body["peer_assignments_version"] == 1
    assert body["peer_assignments"] == []


async def test_two_agents_see_each_other_as_peers(client, admin_headers):
    uid_a, tok_a = await _enroll_and_approve(client, admin_headers, "ha", "10.0.0.1")
    uid_b, tok_b = await _enroll_and_approve(client, admin_headers, "hb", "10.0.0.2")

    r = await client.post(
        "/v1/agent/poll", headers=_agent_headers(tok_a), json=_poll_body(uid_a)
    )
    body = r.json()
    peers = body["peer_assignments"]
    assert len(peers) == 1
    assert peers[0]["target_agent_uid"] == uid_b
    assert peers[0]["target_ip"] == "10.0.0.2"
    assert peers[0]["interval_s"] == 1

    r = await client.post(
        "/v1/agent/poll", headers=_agent_headers(tok_b), json=_poll_body(uid_b)
    )
    peers = r.json()["peer_assignments"]
    assert [p["target_agent_uid"] for p in peers] == [uid_a]


async def test_peers_omitted_when_version_current(client, admin_headers):
    uid_a, tok_a = await _enroll_and_approve(client, admin_headers, "ha", "10.0.0.1")
    uid_b, tok_b = await _enroll_and_approve(client, admin_headers, "hb", "10.0.0.2")

    # First poll picks up version 2.
    r = await client.post(
        "/v1/agent/poll", headers=_agent_headers(tok_a), json=_poll_body(uid_a)
    )
    version = r.json()["peer_assignments_version"]
    assert version == 2

    # Echoing that version back → server should NOT re-send the list.
    r = await client.post(
        "/v1/agent/poll",
        headers=_agent_headers(tok_a),
        json=_poll_body(uid_a, peers_version_seen=version),
    )
    body = r.json()
    assert body["peer_assignments"] is None
    assert body["peer_assignments_version"] == version


async def test_poll_ingests_samples(client, admin_headers, app):
    uid_a, tok_a = await _enroll_and_approve(client, admin_headers, "ha", "10.0.0.1")
    uid_b, _tok_b = await _enroll_and_approve(client, admin_headers, "hb", "10.0.0.2")

    now = int(time.time() * 1000)
    samples = [
        {"target_agent_uid": uid_b, "ts_ms": now - 2000, "rtt_ms": 1.2, "lost": False, "seq": 1},
        {"target_agent_uid": uid_b, "ts_ms": now - 1000, "rtt_ms": None, "lost": True, "seq": 2},
        {"target_agent_uid": uid_b, "ts_ms": now, "rtt_ms": 1.5, "lost": False, "seq": 3},
    ]
    r = await client.post(
        "/v1/agent/poll",
        headers=_agent_headers(tok_a),
        json=_poll_body(uid_a, ping_samples=samples),
    )
    assert r.status_code == 200

    async with app.state.sessionmaker() as db:
        rows = (
            await db.execute(select(PingSampleRaw).order_by(PingSampleRaw.seq))
        ).scalars().all()
        assert [r.seq for r in rows] == [1, 2, 3]
        assert [r.lost for r in rows] == [False, True, False]
        assert rows[0].rtt_ms == pytest.approx(1.2)
        assert rows[1].rtt_ms is None


async def test_poll_delivers_and_ack_commands(client, admin_headers, app):
    uid_a, tok_a = await _enroll_and_approve(client, admin_headers, "ha", "10.0.0.1")

    now = int(time.time() * 1000)
    async with app.state.sessionmaker() as db:
        agent = (
            await db.execute(select(Agent).where(Agent.agent_uid == uid_a))
        ).scalar_one()
        cmd = await command_repo.enqueue(
            db,
            agent_id=agent.id,
            cmd_type=CommandType.TCP_PROBE,
            payload={"host": "10.0.0.9", "port": 22, "count": 1},
            deadline_ms=now + 60_000,
        )
        await db.commit()
        cmd_id = cmd.id

    # First poll should deliver the command.
    r = await client.post(
        "/v1/agent/poll", headers=_agent_headers(tok_a), json=_poll_body(uid_a)
    )
    body = r.json()
    assert len(body["commands"]) == 1
    assert body["commands"][0]["id"] == cmd_id
    assert body["commands"][0]["type"] == "tcp_probe"

    # Second poll with an ack → command should no longer be returned; CommandResult row exists.
    r = await client.post(
        "/v1/agent/poll",
        headers=_agent_headers(tok_a),
        json=_poll_body(
            uid_a,
            command_results=[
                {"command_id": cmd_id, "success": True, "result": {"successes": 1}}
            ],
        ),
    )
    assert r.status_code == 200
    assert r.json()["commands"] == []

    async with app.state.sessionmaker() as db:
        refreshed = await db.get(Command, cmd_id)
        assert refreshed.status == CommandStatus.DONE.value
        result_row = (
            await db.execute(select(CommandResult).where(CommandResult.command_id == cmd_id))
        ).scalar_one()
        assert result_row.success is True
        assert result_row.result == {"successes": 1}


async def test_expired_command_is_not_delivered(client, admin_headers, app):
    uid_a, tok_a = await _enroll_and_approve(client, admin_headers, "ha", "10.0.0.1")
    past = int(time.time() * 1000) - 10_000

    async with app.state.sessionmaker() as db:
        agent = (
            await db.execute(select(Agent).where(Agent.agent_uid == uid_a))
        ).scalar_one()
        await command_repo.enqueue(
            db,
            agent_id=agent.id,
            cmd_type=CommandType.TCP_PROBE,
            payload={},
            deadline_ms=past,
        )
        await db.commit()

    r = await client.post(
        "/v1/agent/poll", headers=_agent_headers(tok_a), json=_poll_body(uid_a)
    )
    assert r.json()["commands"] == []


async def test_last_poll_at_is_updated(client, admin_headers, app):
    uid_a, tok_a = await _enroll_and_approve(client, admin_headers, "ha", "10.0.0.1")

    async with app.state.sessionmaker() as db:
        agent = (
            await db.execute(select(Agent).where(Agent.agent_uid == uid_a))
        ).scalar_one()
        assert agent.last_poll_at is None

    await client.post(
        "/v1/agent/poll", headers=_agent_headers(tok_a), json=_poll_body(uid_a)
    )

    async with app.state.sessionmaker() as db:
        agent = (
            await db.execute(select(Agent).where(Agent.agent_uid == uid_a))
        ).scalar_one()
        assert agent.last_poll_at is not None


async def test_samples_for_unknown_target_uid_are_dropped(client, admin_headers, app):
    uid_a, tok_a = await _enroll_and_approve(client, admin_headers, "ha", "10.0.0.1")

    r = await client.post(
        "/v1/agent/poll",
        headers=_agent_headers(tok_a),
        json=_poll_body(
            uid_a,
            ping_samples=[
                {
                    "target_agent_uid": "nonexistent-uid",
                    "ts_ms": 123,
                    "rtt_ms": 1.0,
                    "lost": False,
                    "seq": 1,
                }
            ],
        ),
    )
    assert r.status_code == 200
    async with app.state.sessionmaker() as db:
        rows = (await db.execute(select(PingSampleRaw))).scalars().all()
        assert rows == []
