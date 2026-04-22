"""Live subprocess pinger test (requires `ping` on PATH)."""

from __future__ import annotations

import shutil

import pytest

from pulse_agent.pinger.icmp_subprocess import SubprocessPinger


@pytest.mark.skipif(shutil.which("ping") is None, reason="ping not on PATH")
async def test_subprocess_pinger_hits_localhost() -> None:
    pinger = SubprocessPinger()
    result = await pinger.ping_once("127.0.0.1", timeout_s=2.0)
    assert result.lost is False
    assert result.rtt_ms is not None
    assert result.rtt_ms >= 0


@pytest.mark.skipif(shutil.which("ping") is None, reason="ping not on PATH")
async def test_subprocess_pinger_timeout_marks_lost() -> None:
    # Route/address that should never reply. 240.0.0.1 is reserved (class E).
    pinger = SubprocessPinger()
    result = await pinger.ping_once("240.0.0.1", timeout_s=1.0)
    assert result.lost is True
    assert result.rtt_ms is None
