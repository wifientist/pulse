"""Pinger factory.

Picks the best backend for the current platform:
  - `IcmpRawPinger` (icmplib) when raw/unprivileged ICMP is available
  - `SubprocessPinger` otherwise (Windows, unprivileged containers)
"""

from __future__ import annotations

import os
import platform
from typing import Protocol

from pulse_agent.pinger.icmp_raw import IcmpRawPinger, PingResult
from pulse_agent.pinger.icmp_subprocess import SubprocessPinger


class Pinger(Protocol):
    async def ping_once(
        self, ip: str, timeout_s: float = 1.0, source: str | None = None
    ) -> PingResult: ...


def build_pinger(raw_icmp: bool) -> Pinger:
    if platform.system().lower() == "windows":
        # icmplib can work on Windows but requires admin. The subprocess fallback is
        # more reliable for a typical install.
        return SubprocessPinger()
    if not raw_icmp:
        return SubprocessPinger()
    privileged = os.geteuid() == 0
    return IcmpRawPinger(privileged=privileged)


__all__ = ["Pinger", "PingResult", "build_pinger", "IcmpRawPinger", "SubprocessPinger"]
