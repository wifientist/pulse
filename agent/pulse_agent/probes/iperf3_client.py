"""iperf3 client-side probe.

Spawns `iperf3 -c HOST -p PORT -J -t DUR [extra]`, blocks until completion, parses the
JSON output for the headline throughput, retransmits, and duration.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pulse_shared.contracts import Iperf3ClientSpec


async def run(payload: dict[str, Any]) -> tuple[bool, dict[str, Any] | None, str | None]:
    spec = Iperf3ClientSpec.model_validate(payload)
    args = [
        "iperf3",
        "-c",
        spec.host,
        "-p",
        str(spec.port),
        "-J",
        "-t",
        str(spec.duration_s),
    ]
    if spec.protocol.lower() == "udp":
        args.append("-u")
        if spec.bitrate:
            args.extend(["-b", spec.bitrate])

    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError:
        return (False, None, "iperf3 not found on PATH")

    # Generous overall timeout: duration + 15s buffer for setup/teardown.
    timeout = spec.duration_s + 15
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return (False, None, f"iperf3 client exceeded {timeout}s")

    out = stdout.decode(errors="replace").strip()
    err = stderr.decode(errors="replace").strip()

    if not out:
        return (False, None, err or f"iperf3 exited {proc.returncode} with no output")
    try:
        raw = json.loads(out)
    except json.JSONDecodeError:
        return (False, None, f"iperf3 produced non-JSON output: {out[:200]}")

    if "error" in raw:
        return (False, {"raw_json": raw}, raw["error"])

    end = raw.get("end", {})
    if spec.protocol.lower() == "udp":
        summary = end.get("sum", {})
    else:
        summary = end.get("sum_received") or end.get("sum_sent") or {}
    throughput_bps = summary.get("bits_per_second")
    duration_s = summary.get("seconds")
    retransmits = summary.get("retransmits")

    return (
        True,
        {
            "throughput_bps": throughput_bps,
            "retransmits": retransmits,
            "duration_s": duration_s,
            "raw_json": raw,
            "error": None,
        },
        None,
    )
