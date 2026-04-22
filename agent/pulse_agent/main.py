"""Pulse agent entrypoint.

Startup sequence:
  1. Load config (env vars, optional .env).
  2. Detect platform capabilities (OS, container, raw ICMP permission, iperf3).
  3. Read existing token from disk; if missing, run the enrollment loop.
  4. Start ping scheduler (initially empty — peers arrive via the first poll).
  5. Run the poll loop until SIGTERM / 401 / CTRL+C.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from pulse_agent import token_store
from pulse_agent.config import AgentSettings, load_agent_settings
from pulse_agent.dispatcher import Dispatcher
from pulse_agent.enroll import run_enrollment
from pulse_agent.http_client import build as build_http
from pulse_agent.pinger import build_pinger
from pulse_agent.pinger.scheduler import PingScheduler
from pulse_agent.platform.detect import detect
from pulse_agent.poll import PollLoop
from pulse_agent.probes import dns as dns_probe
from pulse_agent.probes import http_check as http_probe
from pulse_agent.probes import iperf3_client as iperf3_client_probe
from pulse_agent.probes import iperf3_server as iperf3_server_probe
from pulse_agent.probes import tcp_port as tcp_probe
from pulse_shared.enums import CommandType
from pulse_agent.state import AgentRuntimeState, detect_hostname, detect_primary_ip
from pulse_shared.contracts import AgentCaps
from pulse_shared.version import PROTOCOL_VERSION


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def _main(settings: AgentSettings) -> int:
    _configure_logging(settings.log_level)
    log = logging.getLogger("pulse.agent")

    caps_platform = detect()
    hostname = detect_hostname(settings.hostname)
    primary_ip = settings.reported_ip or detect_primary_ip(settings.primary_iface)

    caps = AgentCaps(
        os=caps_platform.os,
        platform_tag=caps_platform.platform_tag,
        raw_icmp=caps_platform.raw_icmp,
        container=caps_platform.container,
        iperf3_available=caps_platform.iperf3_available,
        agent_version="0.1.0",
        protocol_version=PROTOCOL_VERSION,
    )

    token = token_store.load(settings.token_file)
    if token is None:
        log.info("agent.enrolling hostname=%s ip=%s", hostname, primary_ip)
        token = await run_enrollment(settings, hostname, primary_ip, caps)
        log.info("agent.enrolled agent_uid=%s", token.agent_uid)

    pinger = build_pinger(caps.raw_icmp)
    scheduler = PingScheduler(pinger)
    state = AgentRuntimeState()
    dispatcher = Dispatcher()
    dispatcher.register(CommandType.TCP_PROBE, tcp_probe.run)
    dispatcher.register(CommandType.DNS_PROBE, dns_probe.run)
    dispatcher.register(CommandType.HTTP_PROBE, http_probe.run)
    dispatcher.register(CommandType.IPERF3_SERVER_START, iperf3_server_probe.build_start(state))
    dispatcher.register(CommandType.IPERF3_SERVER_STOP, iperf3_server_probe.build_stop(state))
    dispatcher.register(CommandType.IPERF3_CLIENT, iperf3_client_probe.run)

    async with build_http(
        settings.server_url, bearer=token.agent_token, verify=settings.verify_tls
    ) as http:
        loop = PollLoop(
            http=http,
            caps=caps,
            hostname=hostname,
            primary_ip=primary_ip,
            agent_uid=token.agent_uid,
            scheduler=scheduler,
            dispatcher=dispatcher,
            state=state,
        )

        stop_event = asyncio.Event()

        def _on_signal():
            log.info("agent.shutdown_signal")
            stop_event.set()
            loop.stop()

        asyncio_loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                asyncio_loop.add_signal_handler(sig, _on_signal)
            except NotImplementedError:
                # Windows doesn't support signal.SIGTERM via asyncio; rely on KeyboardInterrupt.
                pass

        try:
            await loop.run()
        finally:
            await scheduler.shutdown()

    return 0


def run() -> None:
    settings = load_agent_settings()
    code = asyncio.run(_main(settings))
    raise SystemExit(code)


if __name__ == "__main__":
    run()
