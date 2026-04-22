"""iperf3 pair orchestration (server-coordinated).

State machine per `iperf_sessions` row:

    REQUESTED → SERVER_STARTING → CLIENT_RUNNING → COLLECTING → DONE
                               ↘ FAILED / TIMEOUT / CANCELLED

The orchestrator holds the cross-agent coordination while individual agents remain
stateless between commands. `handle_command_result` is dispatched by the poll endpoint
whenever a command linked to one of our sessions completes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.config import Settings
from pulse_server.db.models import Agent, Command, CommandResult, IperfSession, Test
from pulse_server.repo import command_repo
from pulse_shared.contracts import Iperf3ClientSpec
from pulse_shared.enums import (
    CommandStatus,
    CommandType,
    IperfSessionState,
    TestState,
    TestType,
)

SERVER_START_WATCHDOG_MS = 15_000
POST_CLIENT_WATCHDOG_BUFFER_MS = 20_000


class IperfOrchestrationError(Exception):
    pass


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class CreatedIperfTest:
    test_id: int
    session_id: int
    server_port: int


async def _allocate_port(db: AsyncSession, settings: Settings) -> int:
    terminal = {
        IperfSessionState.DONE.value,
        IperfSessionState.FAILED.value,
        IperfSessionState.TIMEOUT.value,
        IperfSessionState.CANCELLED.value,
    }
    in_use = {
        row.server_port
        for row in (await db.execute(select(IperfSession))).scalars().all()
        if row.state not in terminal
    }
    for port in range(settings.iperf_port_min, settings.iperf_port_max + 1):
        if port not in in_use:
            return port
    raise IperfOrchestrationError("no iperf3 ports available")


async def submit_iperf3_pair(
    db: AsyncSession,
    settings: Settings,
    client_agent_uid: str,
    server_agent_uid: str,
    duration_s: int = 10,
    protocol: str = "tcp",
    bitrate: str | None = None,
    initiated_by: str | None = None,
) -> CreatedIperfTest:
    if client_agent_uid == server_agent_uid:
        raise IperfOrchestrationError("client and server must be different agents")
    agents = {
        a.agent_uid: a
        for a in (
            await db.execute(
                select(Agent).where(Agent.agent_uid.in_([client_agent_uid, server_agent_uid]))
            )
        ).scalars()
    }
    if client_agent_uid not in agents:
        raise IperfOrchestrationError(f"client agent not found: {client_agent_uid}")
    if server_agent_uid not in agents:
        raise IperfOrchestrationError(f"server agent not found: {server_agent_uid}")

    client_agent = agents[client_agent_uid]
    server_agent = agents[server_agent_uid]
    port = await _allocate_port(db, settings)

    now = _now_ms()
    spec = {
        "client_agent_uid": client_agent_uid,
        "server_agent_uid": server_agent_uid,
        "duration_s": duration_s,
        "protocol": protocol,
        "bitrate": bitrate,
    }
    test = Test(
        initiated_by=initiated_by,
        type=TestType.IPERF3_PAIR.value,
        spec=spec,
        state=TestState.QUEUED.value,
        created_at=now,
    )
    db.add(test)
    await db.flush()

    session = IperfSession(
        test_id=test.id,
        server_agent_id=server_agent.id,
        client_agent_id=client_agent.id,
        server_port=port,
        state=IperfSessionState.REQUESTED.value,
        watchdog_deadline=now + SERVER_START_WATCHDOG_MS,
    )
    db.add(session)
    await db.flush()

    await command_repo.enqueue(
        db,
        agent_id=server_agent.id,
        cmd_type=CommandType.IPERF3_SERVER_START,
        payload={
            "session_id": session.id,
            "port": port,
            "bind": "0.0.0.0",
            "one_shot": True,
        },
        deadline_ms=now + SERVER_START_WATCHDOG_MS - 2_000,
        test_run_id=test.id,
    )
    session.state = IperfSessionState.SERVER_STARTING.value
    await db.commit()
    return CreatedIperfTest(test_id=test.id, session_id=session.id, server_port=port)


async def _fail_session(
    db: AsyncSession, session: IperfSession, error: str
) -> None:
    session.state = IperfSessionState.FAILED.value
    session.finished_at = _now_ms()
    session.error = error
    test = await db.get(Test, session.test_id)
    if test and test.state not in (
        TestState.SUCCEEDED.value,
        TestState.FAILED.value,
        TestState.TIMEOUT.value,
        TestState.CANCELLED.value,
    ):
        test.state = TestState.FAILED.value
        test.finished_at = _now_ms()
        test.error = error


async def _dispatch_client_command(
    db: AsyncSession, session: IperfSession, now_ms: int
) -> None:
    test = await db.get(Test, session.test_id)
    if test is None:
        return
    spec = test.spec if isinstance(test.spec, dict) else {}
    duration_s = int(spec.get("duration_s", 10))
    server_agent = await db.get(Agent, session.server_agent_id)
    client_spec = Iperf3ClientSpec(
        session_id=session.id,
        host=server_agent.primary_ip or "0.0.0.0",
        port=session.server_port,
        duration_s=duration_s,
        protocol=spec.get("protocol", "tcp"),
        bitrate=spec.get("bitrate"),
    )
    session.client_started_at = now_ms
    session.watchdog_deadline = now_ms + duration_s * 1000 + POST_CLIENT_WATCHDOG_BUFFER_MS

    await command_repo.enqueue(
        db,
        agent_id=session.client_agent_id,
        cmd_type=CommandType.IPERF3_CLIENT,
        payload=client_spec.model_dump(),
        deadline_ms=session.watchdog_deadline - 2_000,
        test_run_id=session.test_id,
    )
    session.state = IperfSessionState.CLIENT_RUNNING.value


async def _mark_test_succeeded(
    db: AsyncSession, session: IperfSession, result: dict | None
) -> None:
    test = await db.get(Test, session.test_id)
    if test is None:
        return
    test.state = TestState.SUCCEEDED.value
    test.result = result
    test.finished_at = _now_ms()


async def handle_command_result(db: AsyncSession, command: Command) -> bool:
    """If the command belongs to an iperf session, advance the state machine.

    Returns True if the command was handled by this orchestrator (caller should then
    skip the default single-command test orchestrator hook), False otherwise.
    """
    if command.test_run_id is None:
        return False
    session = (
        await db.execute(
            select(IperfSession).where(IperfSession.test_id == command.test_run_id)
        )
    ).scalar_one_or_none()
    if session is None:
        return False

    now = _now_ms()
    if session.state in (
        IperfSessionState.DONE.value,
        IperfSessionState.FAILED.value,
        IperfSessionState.TIMEOUT.value,
        IperfSessionState.CANCELLED.value,
    ):
        return True

    result_row = (
        await db.execute(
            select(CommandResult).where(CommandResult.command_id == command.id)
        )
    ).scalar_one_or_none()
    success = command.status == CommandStatus.DONE.value
    error = result_row.error if result_row else None

    if command.type == CommandType.IPERF3_SERVER_START.value:
        if not success:
            await _fail_session(db, session, error or "server failed to start")
            return True
        if session.state == IperfSessionState.SERVER_STARTING.value:
            session.server_started_at = now
            await _dispatch_client_command(db, session, now)
    elif command.type == CommandType.IPERF3_CLIENT.value:
        if not success:
            await _fail_session(db, session, error or "client run failed")
            # Best-effort stop the server so the port frees up promptly.
            await _enqueue_stop(db, session, now)
            return True
        session.state = IperfSessionState.COLLECTING.value
        session.finished_at = now
        session.result = result_row.result if result_row else None
        session.state = IperfSessionState.DONE.value
        await _mark_test_succeeded(db, session, session.result)
        await _enqueue_stop(db, session, now)
    elif command.type == CommandType.IPERF3_SERVER_STOP.value:
        # Advisory; no state transition. The server may already be gone.
        pass

    return True


async def _enqueue_stop(db: AsyncSession, session: IperfSession, now_ms: int) -> None:
    await command_repo.enqueue(
        db,
        agent_id=session.server_agent_id,
        cmd_type=CommandType.IPERF3_SERVER_STOP,
        payload={"session_id": session.id},
        deadline_ms=now_ms + 10_000,
        test_run_id=session.test_id,
    )


async def cancel(db: AsyncSession, test_id: int) -> bool:
    session = (
        await db.execute(
            select(IperfSession).where(IperfSession.test_id == test_id)
        )
    ).scalar_one_or_none()
    if session is None:
        return False
    if session.state in (
        IperfSessionState.DONE.value,
        IperfSessionState.FAILED.value,
        IperfSessionState.TIMEOUT.value,
        IperfSessionState.CANCELLED.value,
    ):
        return False
    now = _now_ms()
    session.state = IperfSessionState.CANCELLED.value
    session.finished_at = now
    await _enqueue_stop(db, session, now)
    test = await db.get(Test, test_id)
    if test:
        test.state = TestState.CANCELLED.value
        test.finished_at = now
    # Expire pending client/server commands tied to this test.
    pending_cmds = (
        await db.execute(
            select(Command).where(
                Command.test_run_id == test_id,
                Command.status.in_(
                    [CommandStatus.PENDING.value, CommandStatus.LEASED.value]
                ),
                Command.type != CommandType.IPERF3_SERVER_STOP.value,
            )
        )
    ).scalars().all()
    for c in pending_cmds:
        c.status = CommandStatus.EXPIRED.value
    await db.commit()
    return True


@dataclass(frozen=True)
class WatchdogSummary:
    timed_out: int


async def run_watchdog(db: AsyncSession, now_ms: int | None = None) -> WatchdogSummary:
    now = now_ms or _now_ms()
    stuck = (
        await db.execute(
            select(IperfSession).where(
                IperfSession.state.in_(
                    [
                        IperfSessionState.REQUESTED.value,
                        IperfSessionState.SERVER_STARTING.value,
                        IperfSessionState.CLIENT_RUNNING.value,
                        IperfSessionState.COLLECTING.value,
                    ]
                ),
                IperfSession.watchdog_deadline < now,
            )
        )
    ).scalars().all()

    for session in stuck:
        session.state = IperfSessionState.TIMEOUT.value
        session.finished_at = now
        session.error = f"watchdog timeout in state {session.state}"
        test = await db.get(Test, session.test_id)
        if test and test.state not in (
            TestState.SUCCEEDED.value,
            TestState.FAILED.value,
            TestState.CANCELLED.value,
            TestState.TIMEOUT.value,
        ):
            test.state = TestState.TIMEOUT.value
            test.finished_at = now
            test.error = "iperf3 watchdog timeout"
        await _enqueue_stop(db, session, now)
    if stuck:
        await db.commit()
    return WatchdogSummary(timed_out=len(stuck))
