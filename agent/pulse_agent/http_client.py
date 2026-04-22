"""httpx client factory for agent → server communication."""

from __future__ import annotations

import httpx


def build(
    server_url: str,
    bearer: str | None = None,
    verify: bool = True,
    timeout_s: float = 10.0,
) -> httpx.AsyncClient:
    headers = {"user-agent": "pulse-agent/0.1.0"}
    if bearer:
        headers["authorization"] = f"Bearer {bearer}"
    return httpx.AsyncClient(
        base_url=server_url.rstrip("/"),
        headers=headers,
        verify=verify,
        timeout=timeout_s,
        follow_redirects=False,
    )
