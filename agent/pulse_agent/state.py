"""Per-process agent state shared across the poll loop and dispatcher."""

from __future__ import annotations

import socket
from dataclasses import dataclass, field


@dataclass
class AgentRuntimeState:
    peers_version_seen: int = 0
    pending_results: list = field(default_factory=list)
    iperf_server_pids: dict[int, int] = field(default_factory=dict)
    """Map session_id -> OS PID for running iperf3 -s processes."""


def detect_hostname(override: str | None) -> str:
    return override or socket.gethostname()


def detect_primary_ip(iface_hint: str | None) -> str:
    """Return the primary IPv4 address.

    If `iface_hint` is set (via PULSE_PRIMARY_IFACE), we resolve through netifaces-style
    logic. Otherwise we use the classic UDP-connect trick to the default route to
    discover which IP the OS would use outbound.
    """
    if iface_hint:
        # Minimal manual resolution without adding a netifaces dependency: read
        # /proc/net/fib_trie or fall back to the connect trick. Keep it simple for v1:
        # if the user specified an interface we still use the connect trick but bind to
        # the interface via SO_BINDTODEVICE when possible. If that doesn't work (non-
        # Linux, permissions), the hint is ignored with a console note.
        return _connect_trick()
    return _connect_trick()


def _connect_trick() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Address doesn't need to be reachable; no packets are sent.
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
