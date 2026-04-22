"""iperf3 pair state machine.

Unit-tests the orchestrator with simulated agent responses (no real iperf3 binary
involved). End-to-end testing of the real iperf3 subprocess lives in the Docker smoke
test.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import select

from pulse_server.db.models import Agent, Command, CommandResult, IperfSession, Test
from pulse_server.repo import command_repo
from pulse_server.services import iperf3_orchestrator
from pulse_shared.enums import (
    CommandStatus,
    CommandType,
    IperfSessionState,
    TestState,
)

from .test_poll import _enroll_and_approve


async def _ack(db, command_id: int, agent_id: int, success: bool, result: dict | None, error: str | None = None):
    cmd = await command_repo.record_result(
        db,
        command_id=command_id,
        agent_id=agent_id,
        success=success,
        result=result,
        error=error,
    )
    await db.flush()
    handled = await iperf3_orchestrator.handle_command_result(db, cmd)
    await db.commit()
    return handled


async def _agent_id(db, agent_uid: str) -> int:
    row = (
        await db.execute(select(Agent).where(Agent.agent_uid == agent_uid))
    ).scalar_one()
    return row.id


async def _latest_command(db, agent_id: int, cmd_type: str) -> Command:
    cmd = (
        await db.execute(
            select(Command)
            .where(Command.agent_id == agent_id, Command.type == cmd_type)
            .order_by(Command.id.desc())
            .limit(1)
        )
    ).scalar_one()
    return cmd


async def test_happy_path_iperf3_pair(app, client, admin_headers) -> None:
    uid_c, _ = await _enroll_and_approve(client, admin_headers, "hc", "10.0.0.1")
    uid_s, _ = await _enroll_and_approve(client, admin_headers, "hs", "10.0.0.2")

    r = await client.post(
        "/v1/admin/tests",
        headers=admin_headers,
        json={
            "type": "iperf3_pair",
            "client_agent_uid": uid_c,
            "server_agent_uid": uid_s,
            "spec": {"duration_s": 3, "protocol": "tcp"},
        },
    )
    assert r.status_code == 202, r.text
    test_id = r.json()["test_id"]

    async with app.state.sessionmaker() as db:
        session = (
            await db.execute(select(IperfSession).where(IperfSession.test_id == test_id))
        ).scalar_one()
        assert session.state == IperfSessionState.SERVER_STARTING.value
        server_port = session.server_port
        assert 42000 <= server_port <= 42099

        server_id = await _agent_id(db, uid_s)
        client_id = await _agent_id(db, uid_c)

        start_cmd = await _latest_command(db, server_id, CommandType.IPERF3_SERVER_START.value)
        assert start_cmd.payload["port"] == server_port
        assert start_cmd.payload["session_id"] == session.id

    # Simulate the server agent successfully starting iperf3.
    async with app.state.sessionmaker() as db:
        server_id = await _agent_id(db, uid_s)
        start_cmd = await _latest_command(db, server_id, CommandType.IPERF3_SERVER_START.value)
        handled = await _ack(
            db,
            command_id=start_cmd.id,
            agent_id=server_id,
            success=True,
            result={"listening": True, "port": server_port},
        )
        assert handled is True

    # The orchestrator should have advanced to CLIENT_RUNNING and enqueued the client command.
    async with app.state.sessionmaker() as db:
        session = (
            await db.execute(select(IperfSession).where(IperfSession.test_id == test_id))
        ).scalar_one()
        assert session.state == IperfSessionState.CLIENT_RUNNING.value

        client_id = await _agent_id(db, uid_c)
        client_cmd = await _latest_command(db, client_id, CommandType.IPERF3_CLIENT.value)
        assert client_cmd.payload["host"] == "10.0.0.2"
        assert client_cmd.payload["port"] == server_port

    # Simulate the client finishing with throughput.
    async with app.state.sessionmaker() as db:
        client_id = await _agent_id(db, uid_c)
        client_cmd = await _latest_command(db, client_id, CommandType.IPERF3_CLIENT.value)
        await _ack(
            db,
            command_id=client_cmd.id,
            agent_id=client_id,
            success=True,
            result={
                "throughput_bps": 950_000_000,
                "retransmits": 0,
                "duration_s": 3.0,
                "raw_json": {"end": {"sum_received": {"bits_per_second": 950_000_000}}},
            },
        )

    async with app.state.sessionmaker() as db:
        session = (
            await db.execute(select(IperfSession).where(IperfSession.test_id == test_id))
        ).scalar_one()
        assert session.state == IperfSessionState.DONE.value
        test = await db.get(Test, test_id)
        assert test.state == TestState.SUCCEEDED.value
        assert test.result["throughput_bps"] == 950_000_000

        # A best-effort server_stop was enqueued.
        server_id = await _agent_id(db, uid_s)
        stop_cmd = (
            await db.execute(
                select(Command).where(
                    Command.agent_id == server_id,
                    Command.type == CommandType.IPERF3_SERVER_STOP.value,
                )
            )
        ).scalars().all()
        assert len(stop_cmd) == 1


async def test_server_start_failure_fails_test(app, client, admin_headers) -> None:
    uid_c, _ = await _enroll_and_approve(client, admin_headers, "hc", "10.0.0.1")
    uid_s, _ = await _enroll_and_approve(client, admin_headers, "hs", "10.0.0.2")
    r = await client.post(
        "/v1/admin/tests",
        headers=admin_headers,
        json={
            "type": "iperf3_pair",
            "client_agent_uid": uid_c,
            "server_agent_uid": uid_s,
            "spec": {"duration_s": 3},
        },
    )
    test_id = r.json()["test_id"]

    async with app.state.sessionmaker() as db:
        server_id = await _agent_id(db, uid_s)
        start_cmd = await _latest_command(db, server_id, CommandType.IPERF3_SERVER_START.value)
        await _ack(
            db,
            command_id=start_cmd.id,
            agent_id=server_id,
            success=False,
            result={"listening": False},
            error="port in use",
        )
    async with app.state.sessionmaker() as db:
        session = (
            await db.execute(select(IperfSession).where(IperfSession.test_id == test_id))
        ).scalar_one()
        assert session.state == IperfSessionState.FAILED.value
        test = await db.get(Test, test_id)
        assert test.state == TestState.FAILED.value
        assert "port in use" in (test.error or "")


async def test_port_reused_after_terminal(app, client, admin_headers) -> None:
    uid_c, _ = await _enroll_and_approve(client, admin_headers, "hc", "10.0.0.1")
    uid_s, _ = await _enroll_and_approve(client, admin_headers, "hs", "10.0.0.2")

    # Fail the first test immediately so its port is free.
    r = await client.post(
        "/v1/admin/tests",
        headers=admin_headers,
        json={
            "type": "iperf3_pair",
            "client_agent_uid": uid_c,
            "server_agent_uid": uid_s,
            "spec": {},
        },
    )
    test_one = r.json()["test_id"]

    async with app.state.sessionmaker() as db:
        server_id = await _agent_id(db, uid_s)
        cmd = await _latest_command(db, server_id, CommandType.IPERF3_SERVER_START.value)
        first_port = cmd.payload["port"]
        await _ack(db, cmd.id, server_id, success=False, result=None, error="bind failed")

    # Second test should allocate a port — could be the same number now that the first is terminal.
    r = await client.post(
        "/v1/admin/tests",
        headers=admin_headers,
        json={
            "type": "iperf3_pair",
            "client_agent_uid": uid_c,
            "server_agent_uid": uid_s,
            "spec": {},
        },
    )
    test_two = r.json()["test_id"]
    async with app.state.sessionmaker() as db:
        session_two = (
            await db.execute(select(IperfSession).where(IperfSession.test_id == test_two))
        ).scalar_one()
        # First port is free again, so port allocator returns the lowest available → same port.
        assert session_two.server_port == first_port


async def test_cancel_running_test(app, client, admin_headers) -> None:
    uid_c, _ = await _enroll_and_approve(client, admin_headers, "hc", "10.0.0.1")
    uid_s, _ = await _enroll_and_approve(client, admin_headers, "hs", "10.0.0.2")
    r = await client.post(
        "/v1/admin/tests",
        headers=admin_headers,
        json={
            "type": "iperf3_pair",
            "client_agent_uid": uid_c,
            "server_agent_uid": uid_s,
            "spec": {},
        },
    )
    test_id = r.json()["test_id"]

    r = await client.post(f"/v1/admin/tests/{test_id}/cancel", headers=admin_headers)
    assert r.status_code == 204

    async with app.state.sessionmaker() as db:
        session = (
            await db.execute(select(IperfSession).where(IperfSession.test_id == test_id))
        ).scalar_one()
        assert session.state == IperfSessionState.CANCELLED.value
        test = await db.get(Test, test_id)
        assert test.state == TestState.CANCELLED.value
        # The in-flight SERVER_START command should have been expired.
        server_id = await _agent_id(db, uid_s)
        start_cmd = await _latest_command(db, server_id, CommandType.IPERF3_SERVER_START.value)
        assert start_cmd.status == CommandStatus.EXPIRED.value


async def test_watchdog_times_out_stuck_session(app, client, admin_headers) -> None:
    uid_c, _ = await _enroll_and_approve(client, admin_headers, "hc", "10.0.0.1")
    uid_s, _ = await _enroll_and_approve(client, admin_headers, "hs", "10.0.0.2")
    r = await client.post(
        "/v1/admin/tests",
        headers=admin_headers,
        json={
            "type": "iperf3_pair",
            "client_agent_uid": uid_c,
            "server_agent_uid": uid_s,
            "spec": {},
        },
    )
    test_id = r.json()["test_id"]

    async with app.state.sessionmaker() as db:
        session = (
            await db.execute(select(IperfSession).where(IperfSession.test_id == test_id))
        ).scalar_one()
        # Pretend it's way past the deadline.
        now = int(time.time() * 1000) + 999_999_999
        summary = await iperf3_orchestrator.run_watchdog(db, now_ms=now)
        assert summary.timed_out == 1

    async with app.state.sessionmaker() as db:
        session = (
            await db.execute(select(IperfSession).where(IperfSession.test_id == test_id))
        ).scalar_one()
        assert session.state == IperfSessionState.TIMEOUT.value
        test = await db.get(Test, test_id)
        assert test.state == TestState.TIMEOUT.value


async def test_client_cannot_be_same_as_server(app, client, admin_headers) -> None:
    uid_a, _ = await _enroll_and_approve(client, admin_headers, "ha", "10.0.0.1")
    r = await client.post(
        "/v1/admin/tests",
        headers=admin_headers,
        json={
            "type": "iperf3_pair",
            "client_agent_uid": uid_a,
            "server_agent_uid": uid_a,
            "spec": {},
        },
    )
    assert r.status_code == 400
