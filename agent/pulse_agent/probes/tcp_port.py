"""TCP port probe: open a connection and time it.

Catches firewall/service issues that ICMP misses — many home networks drop ICMP but
allow TCP to specific ports.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from pulse_shared.contracts import TcpProbeSpec


async def run(payload: dict[str, Any]) -> tuple[bool, dict[str, Any] | None, str | None]:
    spec = TcpProbeSpec.model_validate(payload)
    successes = 0
    rtts: list[float] = []
    last_error: str | None = None

    for _ in range(max(1, spec.count)):
        t0 = time.monotonic()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(spec.host, spec.port), timeout=spec.timeout_s
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            successes += 1
            rtts.append((time.monotonic() - t0) * 1000)
        except asyncio.TimeoutError:
            last_error = "timeout"
        except OSError as e:
            last_error = f"{type(e).__name__}: {e}"
        except Exception as e:  # noqa: BLE001
            last_error = repr(e)

    rtt_avg = sum(rtts) / len(rtts) if rtts else None
    success = successes > 0
    return (
        success,
        {
            "attempts": spec.count,
            "successes": successes,
            "rtt_ms_avg": rtt_avg,
            "error": None if success else last_error,
        },
        None if success else (last_error or "unreachable"),
    )
