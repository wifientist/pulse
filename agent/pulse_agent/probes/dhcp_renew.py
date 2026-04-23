"""Force a DHCP release/renew on a given interface.

Tries a series of OS-specific methods in preference order and reports which one
worked. Root privileges required (agent runs as root under systemd, so this is fine
in the default install).

Methods (Linux):
  1. `networkctl renew <iface>` — systemd-networkd (Ubuntu server default 22.04+).
     Simple, fast, doesn't bounce the interface.
  2. `nmcli device reapply <iface>` + `nmcli device up <iface>` — NetworkManager
     (Ubuntu desktop, many distros). Bounces the interface briefly.
  3. `dhclient -r <iface>` followed by `dhclient <iface>` — classic ISC client.
     Last-resort fallback; some setups uninstall this by default.

On success, returns the method used + a brief excerpt of the command output.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Any


async def _run(args: list[str], timeout_s: float = 8.0) -> tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return (127, "", "not found")
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return (-1, "", "timeout")
    return (proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace"))


async def run(payload: dict[str, Any]) -> tuple[bool, dict[str, Any] | None, str | None]:
    iface = (payload.get("iface_name") or "").strip()
    if not iface:
        return (False, None, "iface_name required")

    attempted: list[dict[str, Any]] = []

    # 1. systemd-networkd
    if shutil.which("networkctl"):
        rc, out, err = await _run(["networkctl", "renew", iface])
        attempted.append({"method": "networkctl", "rc": rc, "stderr": err.strip()[:200]})
        if rc == 0:
            return (
                True,
                {"method": "networkctl", "output": out.strip()[:500], "attempted": attempted},
                None,
            )

    # 2. NetworkManager
    if shutil.which("nmcli"):
        rc1, out1, err1 = await _run(["nmcli", "device", "reapply", iface])
        attempted.append({"method": "nmcli reapply", "rc": rc1, "stderr": err1.strip()[:200]})
        rc2, out2, err2 = await _run(["nmcli", "device", "up", iface])
        attempted.append({"method": "nmcli up", "rc": rc2, "stderr": err2.strip()[:200]})
        if rc1 == 0 or rc2 == 0:
            return (
                True,
                {"method": "nmcli", "output": (out1 + out2).strip()[:500], "attempted": attempted},
                None,
            )

    # 3. dhclient
    if shutil.which("dhclient"):
        rc_r, _, err_r = await _run(["dhclient", "-r", iface])
        attempted.append({"method": "dhclient -r", "rc": rc_r, "stderr": err_r.strip()[:200]})
        rc_n, _, err_n = await _run(["dhclient", iface])
        attempted.append({"method": "dhclient", "rc": rc_n, "stderr": err_n.strip()[:200]})
        if rc_n == 0:
            return (
                True,
                {"method": "dhclient", "attempted": attempted},
                None,
            )

    return (
        False,
        {"attempted": attempted},
        "no supported DHCP client succeeded (tried networkctl, nmcli, dhclient)",
    )
