"""Enumerate host network interfaces (MAC + current IPv4 + name).

Used by the poll loop to report interface inventory to the server on every round-trip.
MAC is the stable identifier across DHCP renewals — the server keys on `(agent, mac)`
and just updates `current_ip` in place when it changes.

Filters out loopback and link-local-only interfaces. Wireless interfaces additionally
carry SSID/BSSID/signal pulled from `iw dev <iface> link` (no sudo needed on standard
Ubuntu/Debian).
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

import psutil


@dataclass(frozen=True)
class InterfaceInfo:
    mac: str
    ip: str | None
    iface_name: str
    ssid: str | None = None
    bssid: str | None = None
    signal_dbm: int | None = None


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


def _is_wireless(iface_name: str) -> bool:
    """An interface is wifi iff `/sys/class/net/<iface>/wireless` exists (kernel exposes
    this directory for every wlan/cfg80211-registered netdev). No sudo required."""
    return os.path.isdir(f"/sys/class/net/{iface_name}/wireless")


# `iw dev <iface> link` output when associated:
#   Connected to aa:bb:cc:dd:ee:ff (on wlan0)
#           SSID: MyHomeAP
#           freq: 5180
#           signal: -55 dBm
#           ...
# When not associated: `Not connected.` (single line). We tolerate both.
_IW_BSSID_RE = re.compile(r"Connected to\s+([0-9a-fA-F:]{17})")
_IW_SSID_RE = re.compile(r"^\s*SSID:\s*(.+?)\s*$", re.MULTILINE)
_IW_SIGNAL_RE = re.compile(r"signal:\s*(-?\d+)\s*dBm")


def _read_wireless(iface_name: str) -> tuple[str | None, str | None, int | None]:
    """Return (ssid, bssid, signal_dbm) for the given wifi iface, or (None, None, None)
    if unassociated / `iw` missing / parse failed. Never raises."""
    try:
        proc = subprocess.run(
            ["iw", "dev", iface_name, "link"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None, None, None

    out = proc.stdout or ""
    if "Not connected" in out:
        return None, None, None

    bssid_m = _IW_BSSID_RE.search(out)
    ssid_m = _IW_SSID_RE.search(out)
    signal_m = _IW_SIGNAL_RE.search(out)

    bssid = bssid_m.group(1).lower() if bssid_m else None
    ssid = ssid_m.group(1) if ssid_m else None
    signal = int(signal_m.group(1)) if signal_m else None
    return ssid, bssid, signal


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

        ssid = bssid = None
        signal: int | None = None
        if _is_wireless(name):
            ssid, bssid, signal = _read_wireless(name)

        out.append(
            InterfaceInfo(
                mac=mac.lower(),
                ip=ipv4,
                iface_name=name,
                ssid=ssid,
                bssid=bssid,
                signal_dbm=signal,
            ),
        )

    return out
