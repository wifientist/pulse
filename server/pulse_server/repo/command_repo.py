"""Command-queue persistence.

Commands flow: admin or orchestrator creates a `pending` command for an agent → agent
picks it up on its next poll (leased) → agent returns result via next poll
(status=done/failed). Poll lease is the simple cursor: once returned, the server marks
the command as `leased` with an expiry so a lost/crashed agent's command can be reaped.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import Command, CommandResult
from pulse_shared.enums import CommandStatus, CommandType


def _now_ms() -> int:
    return int(time.time() * 1000)


async def enqueue(
    db: AsyncSession,
    agent_id: int,
    cmd_type: CommandType,
    payload: dict[str, Any],
    deadline_ms: int,
    test_run_id: int | None = None,
    idempotency_key: str | None = None,
) -> Command:
    cmd = Command(
        agent_id=agent_id,
        type=cmd_type.value,
        payload=payload,
        status=CommandStatus.PENDING.value,
        lease_expires_at=None,
        created_at=_now_ms(),
        dispatched_at=None,
        deadline_ms=deadline_ms,
        test_run_id=test_run_id,
        idempotency_key=idempotency_key,
    )
    db.add(cmd)
    await db.flush()
    return cmd


async def lease_pending_for_agent(
    db: AsyncSession,
    agent_id: int,
    now_ms: int | None = None,
) -> list[Command]:
    """Hand back all pending commands for the agent and mark them leased.

    A lease expires at the command's original `deadline_ms`. If the agent misses the
    deadline we treat the command as expired (caller handles re-enqueue if appropriate).
    """
    now_ms = now_ms or _now_ms()

    # Expire stale leases first so an agent that reconnects after a long gap doesn't
    # see commands that have already aged out.
    stale = (
        (
            await db.execute(
                select(Command).where(
                    Command.status == CommandStatus.LEASED.value,
                    Command.lease_expires_at.is_not(None),
                    Command.lease_expires_at <= now_ms,
                )
            )
        )
        .scalars()
        .all()
    )
    for c in stale:
        c.status = CommandStatus.EXPIRED.value

    # Only hand out PENDING commands — once a command is LEASED we've already
    # dispatched it and the agent's working on it. Re-delivering a LEASED command
    # on every poll while the agent hasn't ACK'd yet was causing racy duplicate
    # runs for slow commands like self_upgrade. If the agent dies mid-run, the
    # lease will expire (expiry handled above) and we'll re-send at that point.
    rows = (
        (
            await db.execute(
                select(Command)
                .where(
                    Command.agent_id == agent_id,
                    Command.status == CommandStatus.PENDING.value,
                    Command.deadline_ms > now_ms,
                )
                .order_by(Command.created_at)
            )
        )
        .scalars()
        .all()
    )
    for c in rows:
        c.status = CommandStatus.LEASED.value
        c.dispatched_at = now_ms
        c.lease_expires_at = c.deadline_ms
    return rows


async def record_result(
    db: AsyncSession,
    command_id: int,
    agent_id: int,
    success: bool,
    result: dict[str, Any] | None,
    error: str | None,
    now_ms: int | None = None,
) -> Command | None:
    """Accept a result for a command belonging to the given agent.

    Returns the Command row so the caller can dispatch follow-on logic (e.g. iperf3
    orchestrator state machine). Ignores commands that don't belong to this agent or
    are already terminal.
    """
    now_ms = now_ms or _now_ms()
    cmd = await db.get(Command, command_id)
    if cmd is None or cmd.agent_id != agent_id:
        return None
    if cmd.status in (CommandStatus.DONE.value, CommandStatus.FAILED.value):
        return cmd

    cmd.status = (CommandStatus.DONE if success else CommandStatus.FAILED).value

    # Upsert result row (idempotent on repeat).
    existing = (
        await db.execute(
            select(CommandResult).where(CommandResult.command_id == command_id)
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            CommandResult(
                command_id=command_id,
                success=success,
                result=result,
                error=error,
                received_at=now_ms,
            )
        )
    else:
        existing.success = success
        existing.result = result
        existing.error = error
        existing.received_at = now_ms
    return cmd
