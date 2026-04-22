"""Tests for the PingScheduler using a synthetic Pinger."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from pulse_agent.pinger import PingResult
from pulse_agent.pinger.scheduler import PeerSpec, PingScheduler


@dataclass
class FakePinger:
    rtt_ms: float = 2.5

    async def ping_once(self, ip: str, timeout_s: float = 1.0) -> PingResult:
        return PingResult(rtt_ms=self.rtt_ms, lost=False)


async def test_scheduler_collects_samples() -> None:
    sched = PingScheduler(FakePinger(rtt_ms=1.0))
    sched.apply([PeerSpec("uid-b", "10.0.0.2", interval_s=0.05)])
    await asyncio.sleep(0.2)
    samples = sched.drain_samples()
    await sched.shutdown()
    assert len(samples) >= 2
    assert all(s.target_agent_uid == "uid-b" for s in samples)
    assert all(s.rtt_ms == 1.0 for s in samples)


async def test_scheduler_apply_replaces_peer_set() -> None:
    sched = PingScheduler(FakePinger())
    sched.apply([PeerSpec("uid-b", "10.0.0.2", interval_s=0.05)])
    await asyncio.sleep(0.1)

    sched.apply([PeerSpec("uid-c", "10.0.0.3", interval_s=0.05)])
    await asyncio.sleep(0.2)

    # Old peer's task should be cancelled; no new samples for it.
    samples = sched.drain_samples()
    await sched.shutdown()
    assert any(s.target_agent_uid == "uid-c" for s in samples)


async def test_dropped_counter_increments_when_queue_full() -> None:
    sched = PingScheduler(FakePinger())
    # Shrink the queue to stress drop-oldest behavior.
    sched.state.queue = asyncio.Queue(maxsize=2)
    sched.apply([PeerSpec("uid-b", "10.0.0.2", interval_s=0.02)])
    await asyncio.sleep(0.25)
    dropped = sched.pop_dropped_count()
    await sched.shutdown()
    assert dropped >= 1
