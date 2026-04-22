"""One-time enrollment loop.

Called at startup when there's no agent token on disk. Exits when the server returns
the plaintext agent token, which is then persisted by the caller.
"""

from __future__ import annotations

import asyncio

import httpx

from pulse_agent import token_store
from pulse_agent.config import AgentSettings
from pulse_agent.http_client import build
from pulse_shared.contracts import AgentCaps


class EnrollmentFailed(RuntimeError):
    pass


async def run_enrollment(
    settings: AgentSettings,
    hostname: str,
    primary_ip: str,
    caps: AgentCaps,
    poll_interval_s: float = 5.0,
) -> token_store.StoredToken:
    if not settings.enrollment_token:
        raise EnrollmentFailed("PULSE_ENROLLMENT_TOKEN is required for first-time enrollment")

    async with build(settings.server_url, verify=settings.verify_tls) as http:
        agent_uid = token_store.load_pending_uid(settings.token_file)
        if agent_uid is None:
            r = await http.post(
                "/v1/enroll",
                json={
                    "enrollment_token": settings.enrollment_token,
                    "hostname": hostname,
                    "reported_ip": primary_ip,
                    "caps": caps.model_dump(),
                },
            )
            if r.status_code != 200:
                raise EnrollmentFailed(f"enroll failed: {r.status_code} {r.text}")
            agent_uid = r.json()["agent_uid"]
            token_store.save_uid_only(settings.token_file, agent_uid)

        while True:
            r = await http.post(
                "/v1/enroll/poll",
                json={
                    "enrollment_token": settings.enrollment_token,
                    "agent_uid": agent_uid,
                },
            )
            if r.status_code != 200:
                raise EnrollmentFailed(f"enroll/poll failed: {r.status_code} {r.text}")
            body = r.json()
            if body.get("approved"):
                agent_token = body["agent_token"]
                if not agent_token:
                    raise EnrollmentFailed("approved response missing agent_token")
                stored = token_store.StoredToken(agent_uid=agent_uid, agent_token=agent_token)
                token_store.save(settings.token_file, stored)
                token_store.clear_pending_uid(settings.token_file)
                return stored
            await asyncio.sleep(poll_interval_s)
