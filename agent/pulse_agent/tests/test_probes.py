"""Agent probe handlers."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from pulse_agent.probes import dns as dns_probe
from pulse_agent.probes import tcp_port as tcp_probe


async def test_tcp_probe_success_against_local_listener() -> None:
    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()

    server = await asyncio.start_server(_handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        success, result, error = await tcp_probe.run(
            {"host": "127.0.0.1", "port": port, "count": 2, "timeout_s": 1.0}
        )
        assert success is True
        assert result["attempts"] == 2
        assert result["successes"] == 2
        assert result["rtt_ms_avg"] is not None
        assert error is None
    finally:
        server.close()
        await server.wait_closed()


async def test_tcp_probe_failure_on_closed_port() -> None:
    # Pick a port that's almost certainly unused (high ephemeral).
    success, result, error = await tcp_probe.run(
        {"host": "127.0.0.1", "port": 1, "count": 1, "timeout_s": 0.5}
    )
    assert success is False
    assert result["successes"] == 0
    assert error is not None


async def test_dns_probe_localhost() -> None:
    success, result, error = await dns_probe.run({"name": "localhost", "timeout_s": 2.0})
    assert success is True
    assert result is not None
    assert any(addr in ("127.0.0.1", "::1") for addr in result["addresses"])
    assert result["duration_ms"] >= 0
    assert error is None


async def test_dns_probe_failure() -> None:
    success, result, error = await dns_probe.run(
        {"name": "this-host-really-should-not-resolve.invalid", "timeout_s": 2.0}
    )
    assert success is False
    assert error is not None
