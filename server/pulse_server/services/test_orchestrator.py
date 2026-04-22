"""On-demand test orchestration for single-command probes (TCP/DNS/HTTP).

iperf3 multi-step orchestration lives in iperf3_orchestrator.py. For the simple probes
this module is the entire story: one command, one result, one test row updated in place.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import Agent, Command, Test
from pulse_server.repo import command_repo
from pulse_shared.enums import CommandStatus, CommandType, TestState, TestType

_SIMPLE_PROBE_TO_CMD: dict[TestType, CommandType] = {
    TestType.TCP_PROBE: CommandType.TCP_PROBE,
    TestType.DNS_PROBE: CommandType.DNS_PROBE,
    TestType.HTTP_PROBE: CommandType.HTTP_PROBE,
}


class TestOrchestrationError(Exception):
    pass


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class CreatedTest:
    test_id: int


async def submit_simple_probe(
    db: AsyncSession,
    test_type: TestType,
    agent_uid: str,
    spec: dict[str, Any],
    timeout_s: float = 30.0,
    initiated_by: str | None = None,
) -> CreatedTest:
    if test_type not in _SIMPLE_PROBE_TO_CMD:
        raise TestOrchestrationError(f"unsupported simple probe: {test_type}")

    agent = (
        await db.execute(select(Agent).where(Agent.agent_uid == agent_uid))
    ).scalar_one_or_none()
    if agent is None:
        raise TestOrchestrationError(f"unknown agent: {agent_uid}")

    now = _now_ms()
    test = Test(
        initiated_by=initiated_by,
        type=test_type.value,
        spec=spec,
        state=TestState.QUEUED.value,
        created_at=now,
        started_at=None,
        finished_at=None,
        result=None,
        error=None,
    )
    db.add(test)
    await db.flush()

    cmd = await command_repo.enqueue(
        db,
        agent_id=agent.id,
        cmd_type=_SIMPLE_PROBE_TO_CMD[test_type],
        payload=spec,
        deadline_ms=now + int(timeout_s * 1000),
        test_run_id=test.id,
    )
    await db.commit()
    _ = cmd.id  # materialize for any caller
    return CreatedTest(test_id=test.id)


async def handle_command_lease(db: AsyncSession, command: Command) -> None:
    """Called when a command transitions PENDING → LEASED. Bumps linked test to running."""
    if command.test_run_id is None:
        return
    test = await db.get(Test, command.test_run_id)
    if test is None:
        return
    if test.state == TestState.QUEUED.value:
        test.state = TestState.RUNNING.value
        test.started_at = _now_ms()


async def handle_command_result(db: AsyncSession, command: Command) -> None:
    """Called when a command result is recorded. Finalizes the linked test."""
    if command.test_run_id is None:
        return
    test = await db.get(Test, command.test_run_id)
    if test is None:
        return
    if test.state in (
        TestState.SUCCEEDED.value,
        TestState.FAILED.value,
        TestState.TIMEOUT.value,
        TestState.CANCELLED.value,
    ):
        return
    # Look up the freshly-written CommandResult.
    from pulse_server.db.models import CommandResult

    result_row = (
        await db.execute(
            select(CommandResult).where(CommandResult.command_id == command.id)
        )
    ).scalar_one_or_none()
    test.finished_at = _now_ms()
    if command.status == CommandStatus.DONE.value:
        test.state = TestState.SUCCEEDED.value
    else:
        test.state = TestState.FAILED.value
    if result_row is not None:
        test.result = result_row.result
        test.error = result_row.error


async def cancel_test(db: AsyncSession, test_id: int) -> bool:
    test = await db.get(Test, test_id)
    if test is None:
        return False
    if test.state in (
        TestState.SUCCEEDED.value,
        TestState.FAILED.value,
        TestState.CANCELLED.value,
        TestState.TIMEOUT.value,
    ):
        return False
    # Expire any outstanding commands linked to this test so the agent ignores them.
    cmds = (
        await db.execute(select(Command).where(Command.test_run_id == test_id))
    ).scalars().all()
    for c in cmds:
        if c.status in (CommandStatus.PENDING.value, CommandStatus.LEASED.value):
            c.status = CommandStatus.EXPIRED.value
    test.state = TestState.CANCELLED.value
    test.finished_at = _now_ms()
    await db.commit()
    return True
