"""Detect agent platform capabilities (OS, container, ICMP permission, iperf3 on PATH)."""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformCaps:
    os: str
    platform_tag: str
    raw_icmp: bool
    container: bool
    iperf3_available: bool


def _is_container() -> bool:
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup") as fh:
            data = fh.read()
        return any(token in data for token in ("docker", "containerd", "kubepods"))
    except OSError:
        return False


def _can_raw_icmp() -> bool:
    # On Linux, unprivileged ICMP datagram sockets require the kernel to have a wide
    # ping_group_range. If we're root, we can open raw sockets directly. Otherwise,
    # try to inspect the sysctl.
    if platform.system() != "Linux":
        return False
    if os.geteuid() == 0:
        return True
    try:
        with open("/proc/sys/net/ipv4/ping_group_range") as fh:
            lo_s, hi_s = fh.read().strip().split()
        lo, hi = int(lo_s), int(hi_s)
        gid = os.getgid()
        return lo <= gid <= hi
    except (OSError, ValueError):
        return False


def detect() -> PlatformCaps:
    system = platform.system().lower()
    return PlatformCaps(
        os=system if system in {"linux", "darwin", "windows"} else system,
        platform_tag=f"{system}-{platform.release()}",
        raw_icmp=_can_raw_icmp(),
        container=_is_container(),
        iperf3_available=shutil.which("iperf3") is not None,
    )
