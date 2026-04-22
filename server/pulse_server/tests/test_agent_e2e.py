"""End-to-end: the real PollLoop talking to the real FastAPI server in-process,
with a FakePinger generating deterministic samples.

Verifies that samples and command results flow through the actual wire contracts.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import select

from pulse_agent.dispatcher import Dispatcher
from pulse_agent.pinger import PingResult
from pulse_agent.pinger.scheduler import PeerSpec, PingScheduler
from pulse_agent.poll import PollLoop
from pulse_agent.state import AgentRuntimeState
from pulse_server.db.models import Agent, Command, CommandResult, PingSampleRaw
from pulse_server.repo import command_repo
from pulse_shared.contracts import AgentCaps
from pulse_shared.enums import CommandType
from pulse_shared.version import PROTOCOL_VERSION

from .test_enrollment import CAPS
from .test_poll import _enroll_and_approve


@dataclass
class FakePinger:
    rtt_ms: float = 1.1

    async def ping_once(self, ip: str, timeout_s: float = 1.0) -> PingResult:
        return PingResult(rtt_ms=self.rtt_ms, lost=False)


async def test_agent_poll_loop_roundtrip(app, client, admin_headers) -> None:
    # Enroll two agents; agent A is our "local" agent driven by the PollLoop.
    uid_a, tok_a = await _enroll_and_approve(client, admin_headers, "ha", "10.0.0.1")
    uid_b, _tok_b = await _enroll_and_approve(client, admin_headers, "hb", "10.0.0.2")

    caps = AgentCaps(
        os="linux",
        platform_tag="test",
        raw_icmp=False,
        container=False,
        iperf3_available=False,
        agent_version="0.1.0",
        protocol_version=PROTOCOL_VERSION,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://pulse.test",
        headers={"authorization": f"Bearer {tok_a}"},
    ) as http:
        scheduler = PingScheduler(FakePinger(rtt_ms=0.5))
        dispatcher = Dispatcher()
        state = AgentRuntimeState()
        loop = PollLoop(
            http=http,
            caps=caps,
            hostname="ha",
            primary_ip="10.0.0.1",
            agent_uid=uid_a,
            scheduler=scheduler,
            dispatcher=dispatcher,
            state=state,
            interval_s=0.05,
        )

        # First tick: no peers yet → scheduler idle → no samples. Picks up assignments.
        await loop._tick()
        # With two agents, recompute ran on every approval. Agent A's version is now 2.
        assert state.peers_version_seen >= 1

        # Swap in a faster interval for the test so we don't wait seconds for samples.
        scheduler.apply([PeerSpec(uid_b, "10.0.0.2", interval_s=0.05)])

        # Wait for the scheduler to generate a couple of pings toward B.
        await asyncio.sleep(0.3)

        # Enqueue a test command to prove the command path round-trips.
        async with app.state.sessionmaker() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.agent_uid == uid_a))
            ).scalar_one()
            import time
            await command_repo.enqueue(
                db,
                agent_id=agent.id,
                cmd_type=CommandType.TCP_PROBE,
                payload={},
                deadline_ms=int(time.time() * 1000) + 60_000,
            )
            await db.commit()

        # Next tick pushes samples and pulls the command.
        await loop._tick()
        # Give any dispatched command handler (will be the "no handler" stub) time to record.
        await asyncio.sleep(0.1)
        # Next tick acks the command result (no_handler → success=False).
        await loop._tick()

        await scheduler.shutdown()

    # Verify samples landed.
    async with app.state.sessionmaker() as db:
        sample_rows = (await db.execute(select(PingSampleRaw))).scalars().all()
        assert len(sample_rows) >= 2
        agent_b = (
            await db.execute(select(Agent).where(Agent.agent_uid == uid_b))
        ).scalar_one()
        assert all(s.target_agent_id == agent_b.id for s in sample_rows)

        cmds = (await db.execute(select(Command))).scalars().all()
        assert len(cmds) == 1
        # The agent's dispatcher had no handler registered; acknowledged as failed.
        assert cmds[0].status == "failed"
        results = (await db.execute(select(CommandResult))).scalars().all()
        assert len(results) == 1
        assert results[0].success is False
        assert "no handler" in (results[0].error or "")
