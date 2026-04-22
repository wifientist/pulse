"""Subprocess-based pinger fallback for platforms where raw ICMP isn't available.

Runs the system `ping` binary for a single packet and parses the RTT out of stdout.

Output examples handled by the shared regex:
  Linux:   "64 bytes from 10.0.0.1: icmp_seq=0 ttl=64 time=1.23 ms"
  Windows: "Reply from 10.0.0.1: bytes=32 time=15ms TTL=64"
           "Reply from 10.0.0.1: bytes=32 time<1ms TTL=64"
  macOS:   same as Linux
"""

from __future__ import annotations

import asyncio
import platform
import re

from pulse_agent.pinger.icmp_raw import PingResult

_TIME_RE = re.compile(r"time[=<]\s*([\d.]+)", re.IGNORECASE)


class SubprocessPinger:
    def __init__(self) -> None:
        self._system = platform.system().lower()

    def _args(self, ip: str, timeout_s: float) -> list[str]:
        if self._system == "windows":
            # -n count, -w timeout ms
            return ["ping", "-n", "1", "-w", str(int(timeout_s * 1000)), ip]
        # Linux / macOS
        # -c count; -W timeout in seconds (Linux) or ms-based on some BSDs — we use the
        # common Linux form which macOS ping also accepts (interprets -W similarly).
        return ["ping", "-c", "1", "-W", str(max(1, int(timeout_s))), ip]

    async def ping_once(self, ip: str, timeout_s: float = 1.0) -> PingResult:
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._args(ip, timeout_s),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return PingResult(rtt_ms=None, lost=True)

        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s + 1.0
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return PingResult(rtt_ms=None, lost=True)

        if proc.returncode != 0:
            return PingResult(rtt_ms=None, lost=True)

        out = stdout.decode(errors="replace")
        m = _TIME_RE.search(out)
        if m is None:
            return PingResult(rtt_ms=None, lost=True)
        try:
            return PingResult(rtt_ms=float(m.group(1)), lost=False)
        except ValueError:
            return PingResult(rtt_ms=None, lost=True)
