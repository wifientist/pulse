"""iperf3 server-side probe.

Spawns `iperf3 -s -p PORT -1` as a subprocess and reports whether it's listening. The
PID is stashed in the per-agent runtime state so `iperf3_server_stop` can kill it on
timeout / cancellation. In the happy path iperf3 exits on its own after the client
disconnects, and the stop command is a no-op.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from pulse_agent.state import AgentRuntimeState
from pulse_shared.contracts import Iperf3ServerStartSpec


def build_start(state: AgentRuntimeState) -> Callable[[dict[str, Any]], Awaitable]:
    async def _start(payload: dict[str, Any]):
        spec = Iperf3ServerStartSpec.model_validate(payload)
        args = ["iperf3", "-s", "-p", str(spec.port), "-B", spec.bind]
        if spec.one_shot:
            args.append("-1")
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return (False, {"listening": False, "port": spec.port}, "iperf3 not found on PATH")

        # Give iperf3 a moment to bind. If it dies fast (port in use), surface the error.
        await asyncio.sleep(0.3)
        if proc.returncode is not None:
            stderr = (await proc.stderr.read()).decode(errors="replace") if proc.stderr else ""
            return (False, {"listening": False, "port": spec.port}, stderr.strip() or f"exit {proc.returncode}")

        state.iperf_server_pids[spec.session_id] = proc.pid
        return (True, {"listening": True, "port": spec.port}, None)

    return _start


def build_stop(state: AgentRuntimeState) -> Callable[[dict[str, Any]], Awaitable]:
    async def _stop(payload: dict[str, Any]):
        session_id = int(payload.get("session_id", 0))
        pid = state.iperf_server_pids.pop(session_id, None)
        if pid is None:
            return (True, {"stopped": False, "reason": "no pid for session"}, None)

        import os
        import signal

        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            # Already exited naturally — normal for the happy path.
            return (True, {"stopped": True, "graceful": True}, None)
        except PermissionError as e:
            return (False, {"stopped": False}, f"no permission to signal pid {pid}: {e}")

        # Wait up to 3s for graceful exit; fall back to SIGKILL.
        for _ in range(30):
            await asyncio.sleep(0.1)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return (True, {"stopped": True, "graceful": True}, None)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)
        return (True, {"stopped": True, "graceful": False}, None)

    return _stop
