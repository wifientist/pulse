"""Enumerate host network interfaces (MAC + current IPv4 + name).

Used by the poll loop to report interface inventory to the server on every round-trip.
MAC is the stable identifier across DHCP renewals — the server keys on `(agent, mac)`
and just updates `current_ip` in place when it changes.

Filters out loopback and link-local-only interfaces.
"""

from __future__ import annotations

from dataclasses import dataclass

import psutil


@dataclass(frozen=True)
class InterfaceInfo:
    mac: str
    ip: str | None
    iface_name: str


def _is_skippable_iface(name: str) -> bool:
    """Skip loopback, docker, and common virtual/tunnel interfaces that aren't useful
    as ping targets. Conservative list; users running the agent inside container-heavy
    hosts can still see the info via psutil directly if they need it later."""
    lower = name.lower()
    if lower == "lo" or lower.startswith("lo:"):
        return True
    if lower.startswith(("docker", "br-", "veth", "virbr", "tailscale")):
        return True
    return False


def _is_mac_valid(mac: str) -> bool:
    # psutil returns "" on interfaces that have no L2 (e.g. tun). Skip those.
    parts = mac.split(":")
    if len(parts) != 6:
        return False
    if mac == "00:00:00:00:00:00":
        return False
    try:
        for p in parts:
            int(p, 16)
    except ValueError:
        return False
    return True


def enumerate_interfaces() -> list[InterfaceInfo]:
    """Return one InterfaceInfo per host interface with a usable MAC + IPv4.

    If an interface has a MAC but no IPv4 currently, we still report it with `ip=None`
    so the server can see it exists (useful for interfaces that are down, or mgmt-only
    that don't carry IPv4 themselves).
    """
    import socket

    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    out: list[InterfaceInfo] = []

    for name, snics in addrs.items():
        if _is_skippable_iface(name):
            continue
        if not stats.get(name) or not stats[name].isup:
            # Keep only interfaces that are administratively up AND have link state
            # visibility. Down interfaces aren't useful ping targets.
            continue

        mac = ""
        ipv4: str | None = None
        for snic in snics:
            if snic.family == psutil.AF_LINK:
                mac = (snic.address or "").strip()
            elif snic.family == socket.AF_INET:
                addr = (snic.address or "").strip()
                # Skip link-local — not routable.
                if addr and not addr.startswith("169.254."):
                    ipv4 = addr

        if not _is_mac_valid(mac):
            continue
        out.append(
            InterfaceInfo(mac=mac.lower(), ip=ipv4, iface_name=name),
        )

    return out
