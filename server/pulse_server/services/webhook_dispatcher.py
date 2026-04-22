"""Outbox-pattern webhook delivery with exponential backoff.

Run as a scheduled job every ~10s. Reads pending deliveries whose `next_attempt_at` is
due, POSTs the payload to the configured URL with an HMAC-SHA256 signature, and updates
the row. After BACKOFF_SCHEDULE is exhausted the delivery is marked `dead`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import Webhook, WebhookDelivery
from pulse_shared.enums import WebhookDeliveryState

# Delay added to `now` after the Nth failed attempt (index = attempts-1 before update).
BACKOFF_SCHEDULE_S = [30, 120, 600, 1800, 7200]
MAX_ATTEMPTS = len(BACKOFF_SCHEDULE_S) + 1


def _now_ms() -> int:
    return int(time.time() * 1000)


def _sign(body: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


@dataclass(frozen=True)
class DispatchSummary:
    attempted: int
    delivered: int
    failed: int
    marked_dead: int


async def dispatch_due(
    db: AsyncSession,
    client: httpx.AsyncClient | None = None,
    now_ms: int | None = None,
    batch: int = 50,
) -> DispatchSummary:
    now = now_ms or _now_ms()
    due = (
        await db.execute(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.state == WebhookDeliveryState.PENDING.value,
                WebhookDelivery.next_attempt_at <= now,
            )
            .limit(batch)
        )
    ).scalars().all()

    attempted = delivered = failed = marked_dead = 0
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=10.0, verify=True)
    try:
        for d in due:
            attempted += 1
            hook = await db.get(Webhook, d.webhook_id)
            if hook is None or not hook.enabled:
                d.state = WebhookDeliveryState.DEAD.value
                d.last_error = "webhook no longer enabled"
                marked_dead += 1
                continue

            body = json.dumps(d.payload, separators=(",", ":")).encode()
            headers = {
                "content-type": "application/json",
                "x-pulse-signature": _sign(body, hook.secret),
                "x-pulse-event": "link_state_change",
            }
            try:
                r = await client.post(hook.url, content=body, headers=headers)
                ok = 200 <= r.status_code < 300
                last_error = None if ok else f"status {r.status_code}: {r.text[:200]}"
            except httpx.HTTPError as e:
                ok = False
                last_error = f"{type(e).__name__}: {e}"

            d.attempts += 1
            if ok:
                d.state = WebhookDeliveryState.DELIVERED.value
                d.delivered_at = _now_ms()
                d.last_error = None
                delivered += 1
                continue

            d.last_error = last_error
            if d.attempts >= MAX_ATTEMPTS:
                d.state = WebhookDeliveryState.DEAD.value
                marked_dead += 1
            else:
                backoff = BACKOFF_SCHEDULE_S[d.attempts - 1]
                d.next_attempt_at = _now_ms() + backoff * 1000
                failed += 1
        await db.commit()
    finally:
        if own_client:
            await client.aclose()

    return DispatchSummary(
        attempted=attempted,
        delivered=delivered,
        failed=failed,
        marked_dead=marked_dead,
    )
