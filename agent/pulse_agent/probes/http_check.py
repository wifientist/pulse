"""HTTP(S) health check: make a request and report status + timing."""

from __future__ import annotations

import time
from typing import Any

import httpx

from pulse_shared.contracts import HttpProbeSpec


async def run(payload: dict[str, Any]) -> tuple[bool, dict[str, Any] | None, str | None]:
    spec = HttpProbeSpec.model_validate(payload)
    t0 = time.monotonic()
    ttfb_ms: float | None = None
    try:
        async with httpx.AsyncClient(timeout=spec.timeout_s, verify=True) as client:
            response = await client.request(spec.method, spec.url)
            ttfb_ms = (time.monotonic() - t0) * 1000
            # Drain body into total time measurement.
            _ = response.content
            total_ms = (time.monotonic() - t0) * 1000
    except httpx.TimeoutException:
        return (False, None, "timeout")
    except httpx.HTTPError as e:
        return (False, None, f"{type(e).__name__}: {e}")

    expected = spec.expect_status
    ok = True if expected is None else response.status_code == expected
    return (
        ok,
        {
            "status": response.status_code,
            "ttfb_ms": ttfb_ms,
            "total_ms": total_ms,
            "error": None if ok else f"expected {expected} got {response.status_code}",
        },
        None if ok else f"status {response.status_code}",
    )
