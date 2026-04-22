"""DNS resolution probe: resolve a name and report addresses + timing."""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any

from pulse_shared.contracts import DnsProbeSpec


async def run(payload: dict[str, Any]) -> tuple[bool, dict[str, Any] | None, str | None]:
    spec = DnsProbeSpec.model_validate(payload)
    if spec.resolver:
        # Custom resolver would require a DNS client library; keep v1 scope limited to
        # the stdlib resolver. Document the limitation and succeed transparently.
        error = "custom resolvers not supported in v1 — using system resolver"
    else:
        error = None

    loop = asyncio.get_running_loop()
    t0 = time.monotonic()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(spec.name, None, type=socket.SOCK_STREAM),
            timeout=spec.timeout_s,
        )
    except asyncio.TimeoutError:
        return (False, None, "timeout")
    except socket.gaierror as e:
        return (False, None, f"gaierror: {e}")
    except Exception as e:  # noqa: BLE001
        return (False, None, repr(e))

    elapsed_ms = (time.monotonic() - t0) * 1000
    addresses = sorted({info[4][0] for info in infos})
    return (
        True,
        {"addresses": addresses, "duration_ms": elapsed_ms, "error": error},
        error,
    )
