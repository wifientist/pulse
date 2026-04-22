"""On-demand test orchestration end-to-end.

Uses a real agent PollLoop so the wire contract is exercised. The agent's dispatcher is
wired with the same probe handlers that ship in production.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass

import httpx
from sqlalchemy import select

from pulse_agent.dispatcher import Dispatcher
from pulse_agent.pinger import PingResult
from pulse_agent.pinger.scheduler import PingScheduler
from pulse_agent.poll import PollLoop
from pulse_agent.probes import dns as dns_probe
from pulse_agent.probes import tcp_port as tcp_probe
from pulse_agent.state import AgentRuntimeState
from pulse_server.db.models import Test
from pulse_shared.contracts import AgentCaps
from pulse_shared.enums import CommandType
from pulse_shared.version import PROTOCOL_VERSION

from .test_poll import _enroll_and_approve


@dataclass
class NullPinger:
    async def ping_once(self, ip: str, timeout_s: float = 1.0) -> PingResult:
        return PingResult(rtt_ms=None, lost=True)


def _caps() -> AgentCaps:
    return AgentCaps(
        os="linux",
        platform_tag="test",
        raw_icmp=False,
        container=False,
        iperf3_available=False,
        agent_version="0.1.0",
        protocol_version=PROTOCOL_VERSION,
    )


async def _build_agent_client(app, token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://pulse.test",
        headers={"authorization": f"Bearer {token}"},
    )


async def test_tcp_probe_test_flows_end_to_end(app, client, admin_headers) -> None:
    uid_a, tok_a = await _enroll_and_approve(client, admin_headers, "ha", "10.0.0.1")

    # A local TCP listener the probe will hit.
    async def _handle(reader, writer):
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()

    server = await asyncio.start_server(_handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    try:
        # Admin submits the test.
        r = await client.post(
            "/v1/admin/tests",
            headers=admin_headers,
            json={
                "type": "tcp_probe",
                "agent_uid": uid_a,
                "spec": {"host": "127.0.0.1", "port": port, "count": 1, "timeout_s": 1.0},
                "timeout_s": 10,
            },
        )
        assert r.status_code == 202, r.text
        test_id = r.json()["test_id"]

        # Test is queued.
        r = await client.get(f"/v1/admin/tests/{test_id}", headers=admin_headers)
        assert r.json()["state"] == "queued"

        # Run the agent end-to-end.
        agent_http = await _build_agent_client(app, tok_a)
        scheduler = PingScheduler(NullPinger())
        dispatcher = Dispatcher()
        dispatcher.register(CommandType.TCP_PROBE, tcp_probe.run)
        dispatcher.register(CommandType.DNS_PROBE, dns_probe.run)
        state = AgentRuntimeState()
        loop = PollLoop(
            http=agent_http,
            caps=_caps(),
            hostname="ha",
            primary_ip="10.0.0.1",
            agent_uid=uid_a,
            scheduler=scheduler,
            dispatcher=dispatcher,
            state=state,
            interval_s=0.05,
        )

        async with agent_http:
            # First tick: agent picks up the command. Test transitions to running.
            await loop._tick()
            await asyncio.sleep(0.1)  # let the dispatcher finish running the probe

            r = await client.get(f"/v1/admin/tests/{test_id}", headers=admin_headers)
            body = r.json()
            assert body["state"] == "running"

            # Second tick: agent pushes the result. Test transitions to succeeded.
            await loop._tick()

        await scheduler.shutdown()

        r = await client.get(f"/v1/admin/tests/{test_id}", headers=admin_headers)
        body = r.json()
        assert body["state"] == "succeeded", body
        assert body["result"]["successes"] == 1
        assert body["result"]["rtt_ms_avg"] is not None
    finally:
        server.close()
        await server.wait_closed()


async def test_dns_probe_failure_marks_test_failed(app, client, admin_headers) -> None:
    uid_a, tok_a = await _enroll_and_approve(client, admin_headers, "ha", "10.0.0.1")

    r = await client.post(
        "/v1/admin/tests",
        headers=admin_headers,
        json={
            "type": "dns_probe",
            "agent_uid": uid_a,
            "spec": {"name": "this-host-really-should-not-resolve.invalid", "timeout_s": 2.0},
            "timeout_s": 10,
        },
    )
    test_id = r.json()["test_id"]

    agent_http = await _build_agent_client(app, tok_a)
    dispatcher = Dispatcher()
    dispatcher.register(CommandType.DNS_PROBE, dns_probe.run)
    scheduler = PingScheduler(NullPinger())
    loop = PollLoop(
        http=agent_http,
        caps=_caps(),
        hostname="ha",
        primary_ip="10.0.0.1",
        agent_uid=uid_a,
        scheduler=scheduler,
        dispatcher=dispatcher,
        state=AgentRuntimeState(),
        interval_s=0.05,
    )

    async with agent_http:
        await loop._tick()
        await asyncio.sleep(2.5)  # DNS timeout can take a moment
        await loop._tick()

    await scheduler.shutdown()

    r = await client.get(f"/v1/admin/tests/{test_id}", headers=admin_headers)
    body = r.json()
    assert body["state"] == "failed", body
    assert body["error"] is not None


async def test_cancel_queued_test(client, admin_headers, app) -> None:
    uid_a, _tok = await _enroll_and_approve(client, admin_headers, "ha", "10.0.0.1")
    r = await client.post(
        "/v1/admin/tests",
        headers=admin_headers,
        json={
            "type": "tcp_probe",
            "agent_uid": uid_a,
            "spec": {"host": "127.0.0.1", "port": 1, "count": 1, "timeout_s": 0.1},
        },
    )
    test_id = r.json()["test_id"]

    r = await client.post(f"/v1/admin/tests/{test_id}/cancel", headers=admin_headers)
    assert r.status_code == 204
    r = await client.get(f"/v1/admin/tests/{test_id}", headers=admin_headers)
    assert r.json()["state"] == "cancelled"

    # Cancelling again is rejected.
    r = await client.post(f"/v1/admin/tests/{test_id}/cancel", headers=admin_headers)
    assert r.status_code == 409


async def test_listing_tests_honors_state_filter(app, client, admin_headers) -> None:
    uid_a, _tok = await _enroll_and_approve(client, admin_headers, "ha", "10.0.0.1")
    # Create 2 tests; cancel one.
    for port in (1, 2):
        await client.post(
            "/v1/admin/tests",
            headers=admin_headers,
            json={
                "type": "tcp_probe",
                "agent_uid": uid_a,
                "spec": {"host": "127.0.0.1", "port": port, "count": 1, "timeout_s": 0.1},
            },
        )
    r = await client.get("/v1/admin/tests", headers=admin_headers)
    rows = r.json()
    assert len(rows) == 2
    await client.post(f"/v1/admin/tests/{rows[0]['id']}/cancel", headers=admin_headers)

    r = await client.get(
        "/v1/admin/tests?state=queued", headers=admin_headers
    )
    assert len(r.json()) == 1
    r = await client.get(
        "/v1/admin/tests?state=cancelled", headers=admin_headers
    )
    assert len(r.json()) == 1
