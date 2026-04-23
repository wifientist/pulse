"""Poll loop: one round-trip combining telemetry push and command pull."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from pulse_agent.dispatcher import Dispatcher
from pulse_agent.interfaces import enumerate_interfaces
from pulse_agent.pinger.scheduler import PeerSpec, PingScheduler
from pulse_agent.state import AgentRuntimeState
from pulse_shared.contracts import (
    AgentCaps,
    AgentInterface,
    CommandResult,
    PingSample,
    PollRequest,
    PollResponse,
)
from pulse_shared.enums import CommandType

log = logging.getLogger(__name__)


class PollLoop:
    def __init__(
        self,
        http: httpx.AsyncClient,
        caps: AgentCaps,
        hostname: str,
        primary_ip: str,
        agent_uid: str,
        scheduler: PingScheduler,
        dispatcher: Dispatcher,
        state: AgentRuntimeState,
        interval_s: float = 5.0,
    ) -> None:
        self._http = http
        self._caps = caps
        self._hostname = hostname
        self._primary_ip = primary_ip
        self._agent_uid = agent_uid
        self._scheduler = scheduler
        self._dispatcher = dispatcher
        self._state = state
        self._interval_s = interval_s
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except httpx.HTTPStatusError as e:
                if e.response is not None and e.response.status_code == 401:
                    log.error("agent.token_rejected_halting")
                    return
                log.warning("agent.poll_http_error", extra={"status": e.response.status_code if e.response else "?"})
            except httpx.RequestError as e:
                # Transient network failures (server restart, network flap, DNS blip).
                # These are expected during normal operation — log a one-liner so the
                # journal stays readable. When the server comes back we resume polling.
                log.warning(
                    "agent.poll_unreachable error=%s: %s",
                    type(e).__name__, str(e) or "(no message)",
                )
            except Exception:  # noqa: BLE001
                # Anything else is genuinely unexpected — keep the full traceback.
                log.exception("agent.poll_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        samples = self._scheduler.drain_samples()
        dropped = self._scheduler.pop_dropped_count()
        results = list(self._state.pending_results)
        self._state.pending_results.clear()

        # Enumerate interfaces every tick. psutil's calls are cheap (~microseconds)
        # and running it every poll means DHCP IP changes propagate to the server
        # within one poll interval.
        try:
            ifaces = [
                AgentInterface(
                    mac=i.mac,
                    ip=i.ip,
                    iface_name=i.iface_name,
                    ssid=i.ssid,
                    bssid=i.bssid,
                    signal_dbm=i.signal_dbm,
                )
                for i in enumerate_interfaces()
            ]
        except Exception:  # noqa: BLE001
            log.exception("agent.interface_enumeration_failed")
            ifaces = []

        req = PollRequest(
            agent_uid=self._agent_uid,
            now_ms=int(time.time() * 1000),
            caps=self._caps,
            primary_ip=self._primary_ip,
            ping_samples=[
                PingSample(
                    target_agent_uid=s.target_agent_uid,
                    ts_ms=s.ts_ms,
                    rtt_ms=s.rtt_ms,
                    lost=s.lost,
                    seq=s.seq,
                )
                for s in samples
            ],
            command_results=results,
            peers_version_seen=self._state.peers_version_seen,
            dropped_samples_since_last=dropped,
            interfaces=ifaces,
        )

        r = await self._http.post("/v1/agent/poll", json=req.model_dump())
        r.raise_for_status()
        resp = PollResponse.model_validate(r.json())

        # Apply new peer assignments if version changed.
        if resp.peer_assignments is not None:
            specs = [
                PeerSpec(
                    target_agent_uid=p.target_agent_uid,
                    target_ip=p.target_ip,
                    interval_s=float(p.interval_s),
                    source_bind_ip=p.source_bind_ip,
                )
                for p in resp.peer_assignments
                if p.enabled
            ]
            self._scheduler.apply(specs)
            self._state.peers_version_seen = resp.peer_assignments_version

        # Dispatch commands concurrently. Handlers push their results to pending_results.
        for cmd in resp.commands:
            asyncio.create_task(self._handle_command(cmd.id, cmd.type, cmd.payload))

        # Update poll interval if server changed it.
        if resp.config.poll_interval_s and resp.config.poll_interval_s != int(self._interval_s):
            self._interval_s = float(resp.config.poll_interval_s)

    async def _handle_command(self, command_id: int, cmd_type: CommandType, payload: dict) -> None:
        result: CommandResult = await self._dispatcher.handle(command_id, cmd_type, payload)
        self._state.pending_results.append(result)
