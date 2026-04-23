"""ICMP pinger using the icmplib library.

Uses privileged=False mode by default — requires net.ipv4.ping_group_range to include
the agent's GID, or CAP_NET_RAW. Set privileged=True explicitly when running as root.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from icmplib import async_ping


@dataclass(frozen=True)
class PingResult:
    rtt_ms: float | None
    lost: bool


class IcmpRawPinger:
    def __init__(self, privileged: bool = False) -> None:
        self._privileged = privileged

    async def ping_once(
        self, ip: str, timeout_s: float = 1.0, source: str | None = None
    ) -> PingResult:
        # `source` pins the ICMP socket to a specific local IP so the kernel's
        # routing table picks the interface with that IP. Forces traffic out the
        # test-plane interface even when the default route points at mgmt.
        try:
            host = await async_ping(
                address=ip,
                count=1,
                timeout=timeout_s,
                privileged=self._privileged,
                source=source,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return PingResult(rtt_ms=None, lost=True)

        if host.packets_received == 0:
            return PingResult(rtt_ms=None, lost=True)
        return PingResult(rtt_ms=float(host.avg_rtt), lost=False)
