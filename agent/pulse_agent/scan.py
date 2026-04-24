"""Airspace scan for monitor-role agents.

Runs `iw dev <iface> scan` and parses the output into `ScanBssid` DTOs. Scan
is an active operation (the radio briefly leaves the current channel), so
this module must only be called on interfaces the server has classified as
role=monitor — never on a client's test interface.

Parsing is purposely lenient: `iw`'s output format varies slightly between
kernel/distro versions, so we extract the fields we need per-BSS block and
skip anything we can't make sense of.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)

SCAN_TIMEOUT_S = 15.0

_BSS_RE = re.compile(r"^BSS ([0-9a-fA-F:]{17})", re.MULTILINE)
_SIGNAL_RE = re.compile(r"signal:\s*(-?\d+(?:\.\d+)?)\s*dBm")
_FREQ_RE = re.compile(r"freq:\s*(\d+)")
_SSID_RE = re.compile(r"\tSSID:\s*(.*)")
# HT/VHT/HE width line, e.g. "HT operation:  * primary channel: 44  * secondary channel offset: above  * STA channel width: any"
_WIDTH_RE = re.compile(r"channel width:\s*(\d+)")


@dataclass(frozen=True)
class ScanResult:
    bssid: str
    ssid: str | None
    signal_dbm: int | None
    frequency_mhz: int | None
    channel_width_mhz: int | None


def is_scan_available() -> bool:
    """True iff `iw` is on PATH. Doesn't check capability — that surfaces later
    as a scan failure which the poll loop swallows."""
    return shutil.which("iw") is not None


def scan(iface_name: str) -> list[ScanResult]:
    """Run one active scan and return the list of visible BSSIDs. Returns empty
    list on any failure (missing binary, permission denied, interface down).
    Never raises — scanning is best-effort."""
    if not is_scan_available():
        return []
    try:
        proc = subprocess.run(
            ["iw", "dev", iface_name, "scan"],
            capture_output=True,
            text=True,
            timeout=SCAN_TIMEOUT_S,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        log.warning("agent.scan_failed iface=%s err=%s", iface_name, e)
        return []
    if proc.returncode != 0:
        log.warning(
            "agent.scan_nonzero iface=%s rc=%d stderr=%s",
            iface_name, proc.returncode, (proc.stderr or "").strip()[:200],
        )
        return []
    return _parse_iw_scan(proc.stdout or "")


def _parse_iw_scan(output: str) -> list[ScanResult]:
    """Chop on `BSS aa:bb:...` lines, pull SSID/signal/freq/width from each block.
    Blocks without a recognizable BSSID are dropped."""
    results: list[ScanResult] = []
    # Split by BSS headers; first element is the prologue before the first BSS.
    parts = re.split(r"^BSS ", output, flags=re.MULTILINE)
    for part in parts[1:]:
        # Re-prepend the stripped prefix so regexes that expect "BSS <mac>" work.
        block = "BSS " + part
        bssid_m = _BSS_RE.search(block)
        if not bssid_m:
            continue
        bssid = bssid_m.group(1).lower()
        signal_m = _SIGNAL_RE.search(block)
        signal = int(round(float(signal_m.group(1)))) if signal_m else None
        freq_m = _FREQ_RE.search(block)
        freq = int(freq_m.group(1)) if freq_m else None
        ssid_m = _SSID_RE.search(block)
        ssid = ssid_m.group(1).strip() if ssid_m else None
        if ssid == "":
            ssid = None  # hidden SSID — drop the empty string
        width_m = _WIDTH_RE.search(block)
        width = int(width_m.group(1)) if width_m else None
        results.append(
            ScanResult(
                bssid=bssid,
                ssid=ssid,
                signal_dbm=signal,
                frequency_mhz=freq,
                channel_width_mhz=width,
            )
        )
    return results
