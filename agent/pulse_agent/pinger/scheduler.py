"""Per-peer ping scheduler.

One asyncio task per assigned peer. Each fires at `interval_s` with a small random jitter
to desynchronize bursts. Results are placed in a bounded async queue; when the queue is
full we drop oldest and increment `dropped_samples` so the next poll can report the drop.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field

from pulse_agent.pinger import Pinger


@dataclass
class Sample:
    target_agent_uid: str
    ts_ms: int
    rtt_ms: float | None
    lost: bool
    seq: int


@dataclass
class PeerSpec:
    target_agent_uid: str
    target_ip: str
    interval_s: float


@dataclass
class SchedulerState:
    queue: asyncio.Queue[Sample] = field(default_factory=lambda: asyncio.Queue(maxsize=5000))
    dropped_samples: int = 0
    seq: int = 0


class PingScheduler:
    """Spawns one task per peer; `apply()` replaces the peer list atomically."""

    def __init__(self, pinger: Pinger, jitter_fraction: float = 0.1) -> None:
        self._pinger = pinger
        self._jitter = jitter_fraction
        self._tasks: dict[str, asyncio.Task] = {}
        self.state = SchedulerState()

    def apply(self, peers: list[PeerSpec]) -> None:
        # apply() is called only when the server bumps peer_assignments_version, which is
        # rare. Cancel every existing task and start fresh — this guarantees any change
        # to interval or target_ip takes effect immediately without tracking diffs.
        for task in self._tasks.values():
            task.cancel()
        self._tasks = {
            p.target_agent_uid: asyncio.create_task(self._run_peer(p))
            for p in peers
        }

    def drain_samples(self, limit: int = 1000) -> list[Sample]:
        out: list[Sample] = []
        while len(out) < limit:
            try:
                out.append(self.state.queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return out

    def pop_dropped_count(self) -> int:
        n = self.state.dropped_samples
        self.state.dropped_samples = 0
        return n

    async def shutdown(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()

    async def _run_peer(self, spec: PeerSpec) -> None:
        # Stagger the initial tick so all peers don't fire simultaneously.
        await asyncio.sleep(random.uniform(0, min(spec.interval_s, 1.0)))
        while True:
            result = await self._pinger.ping_once(spec.target_ip, timeout_s=min(1.0, spec.interval_s / 2))
            self.state.seq += 1
            sample = Sample(
                target_agent_uid=spec.target_agent_uid,
                ts_ms=int(time.time() * 1000),
                rtt_ms=result.rtt_ms,
                lost=result.lost,
                seq=self.state.seq,
            )
            try:
                self.state.queue.put_nowait(sample)
            except asyncio.QueueFull:
                # Drop oldest and retry.
                try:
                    self.state.queue.get_nowait()
                    self.state.dropped_samples += 1
                    self.state.queue.put_nowait(sample)
                except asyncio.QueueEmpty:
                    self.state.dropped_samples += 1

            jitter = spec.interval_s * random.uniform(-self._jitter, self._jitter)
            await asyncio.sleep(max(0.05, spec.interval_s + jitter))
