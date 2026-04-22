"""End-to-end enrollment flow tests.

Covers: issue pre-shared token → agent enrolls → admin approves → agent polls and gets
per-agent token. Plus: auth failures, rejection, revocation, expired tokens, usage caps,
and re-fetch-after-fetch guard.
"""

from __future__ import annotations

import time

import pytest

from pulse_shared.version import PROTOCOL_VERSION

CAPS = {
    "os": "linux",
    "platform_tag": "test",
    "raw_icmp": True,
    "container": False,
    "iperf3_available": True,
    "agent_version": "0.1.0",
    "protocol_version": PROTOCOL_VERSION,
}


async def _mint_token(client, admin_headers, **overrides) -> str:
    body = {"label": "test-token"}
    body.update(overrides)
    r = await client.post("/v1/admin/enrollment-tokens", headers=admin_headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()["plaintext"]


async def _enroll(client, token: str, hostname: str = "host-a") -> str:
    r = await client.post(
        "/v1/enroll",
        json={
            "enrollment_token": token,
            "hostname": hostname,
            "reported_ip": "10.0.0.5",
            "caps": CAPS,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pending"] is True
    return body["agent_uid"]


async def test_admin_requires_bearer(client):
    r = await client.post("/v1/admin/enrollment-tokens", json={"label": "x"})
    assert r.status_code in (401, 403)  # missing creds → unauthenticated


async def test_admin_bad_bearer(client):
    r = await client.post(
        "/v1/admin/enrollment-tokens",
        headers={"Authorization": "Bearer nope"},
        json={"label": "x"},
    )
    assert r.status_code == 401


async def test_enrollment_full_cycle(client, admin_headers):
    # 1. Admin mints a pre-shared enrollment token.
    plaintext = await _mint_token(client, admin_headers)

    # 2. Agent enrolls.
    agent_uid = await _enroll(client, plaintext)

    # 3. Agent polls before approval — pending.
    r = await client.post(
        "/v1/enroll/poll",
        json={"enrollment_token": plaintext, "agent_uid": agent_uid},
    )
    assert r.status_code == 200
    assert r.json() == {"approved": False, "agent_token": None}

    # 4. Admin lists pending, approves by id.
    r = await client.get("/v1/admin/enrollments/pending", headers=admin_headers)
    assert r.status_code == 200
    pending = r.json()
    assert len(pending) == 1
    assert pending[0]["agent_uid"] == agent_uid
    pending_id = pending[0]["id"]

    r = await client.post(
        f"/v1/admin/enrollments/{pending_id}/approve",
        headers=admin_headers,
        json={},
    )
    assert r.status_code == 200, r.text
    approve_body = r.json()
    assert approve_body["agent_uid"] == agent_uid

    # 5. Agent polls — approved, receives plaintext token exactly once.
    r = await client.post(
        "/v1/enroll/poll",
        json={"enrollment_token": plaintext, "agent_uid": agent_uid},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["approved"] is True
    agent_token = body["agent_token"]
    assert agent_token and len(agent_token) > 20

    # 6. Second poll must NOT return the token again.
    r = await client.post(
        "/v1/enroll/poll",
        json={"enrollment_token": plaintext, "agent_uid": agent_uid},
    )
    assert r.status_code == 401
    assert "already retrieved" in r.json()["detail"]


async def test_enroll_invalid_token(client, admin_headers):
    r = await client.post(
        "/v1/enroll",
        json={
            "enrollment_token": "not-a-real-token",
            "hostname": "h",
            "reported_ip": "10.0.0.1",
            "caps": CAPS,
        },
    )
    assert r.status_code == 401


async def test_enroll_uses_remaining_is_decremented(client, admin_headers):
    plaintext = await _mint_token(client, admin_headers, uses_remaining=1)
    await _enroll(client, plaintext, hostname="h1")
    # Second attempt with the same single-use token must fail.
    r = await client.post(
        "/v1/enroll",
        json={
            "enrollment_token": plaintext,
            "hostname": "h2",
            "reported_ip": "10.0.0.2",
            "caps": CAPS,
        },
    )
    assert r.status_code == 401


async def test_revoke_enrollment_token_blocks_future_enroll(client, admin_headers):
    plaintext = await _mint_token(client, admin_headers)
    # Find id, then revoke.
    r = await client.get("/v1/admin/enrollment-tokens", headers=admin_headers)
    tokens = r.json()
    token_id = tokens[0]["id"]
    r = await client.delete(
        f"/v1/admin/enrollment-tokens/{token_id}", headers=admin_headers
    )
    assert r.status_code == 204

    r = await client.post(
        "/v1/enroll",
        json={
            "enrollment_token": plaintext,
            "hostname": "x",
            "reported_ip": "10.0.0.1",
            "caps": CAPS,
        },
    )
    assert r.status_code == 401


async def test_expired_enrollment_token_is_rejected(client, admin_headers):
    past = int(time.time() * 1000) - 1000
    plaintext = await _mint_token(client, admin_headers, expires_at=past)
    r = await client.post(
        "/v1/enroll",
        json={
            "enrollment_token": plaintext,
            "hostname": "x",
            "reported_ip": "10.0.0.1",
            "caps": CAPS,
        },
    )
    assert r.status_code == 401


async def test_reject_pending_deletes_it(client, admin_headers):
    plaintext = await _mint_token(client, admin_headers)
    agent_uid = await _enroll(client, plaintext)
    r = await client.get("/v1/admin/enrollments/pending", headers=admin_headers)
    pending_id = r.json()[0]["id"]

    r = await client.post(
        f"/v1/admin/enrollments/{pending_id}/reject", headers=admin_headers
    )
    assert r.status_code == 204

    r = await client.get("/v1/admin/enrollments/pending", headers=admin_headers)
    assert r.json() == []

    # Agent's poll now errors: unknown agent_uid.
    r = await client.post(
        "/v1/enroll/poll",
        json={"enrollment_token": plaintext, "agent_uid": agent_uid},
    )
    assert r.status_code == 401


async def test_cannot_double_approve(client, admin_headers):
    plaintext = await _mint_token(client, admin_headers)
    await _enroll(client, plaintext)
    r = await client.get("/v1/admin/enrollments/pending", headers=admin_headers)
    pending_id = r.json()[0]["id"]

    r = await client.post(
        f"/v1/admin/enrollments/{pending_id}/approve",
        headers=admin_headers,
        json={},
    )
    assert r.status_code == 200

    r = await client.post(
        f"/v1/admin/enrollments/{pending_id}/approve",
        headers=admin_headers,
        json={},
    )
    assert r.status_code == 409


async def test_poll_with_wrong_enrollment_token(client, admin_headers):
    plaintext_a = await _mint_token(client, admin_headers, label="A")
    plaintext_b = await _mint_token(client, admin_headers, label="B")
    agent_uid = await _enroll(client, plaintext_a)

    # Poll with a different (still valid) enrollment token → mismatch.
    r = await client.post(
        "/v1/enroll/poll",
        json={"enrollment_token": plaintext_b, "agent_uid": agent_uid},
    )
    assert r.status_code == 401
