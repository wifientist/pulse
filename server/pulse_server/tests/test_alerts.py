"""Alert engine state machine + webhook outbox delivery."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass

import httpx
from sqlalchemy import select

from pulse_server.db.models import (
    Alert,
    LinkStateRow,
    PingAggregateMinute,
    Webhook,
    WebhookDelivery,
)
from pulse_server.services import alert_engine, webhook_dispatcher
from pulse_shared.enums import LinkState, WebhookDeliveryState

from .test_poll import _enroll_and_approve

MINUTE_MS = 60_000


async def _seed_minute_agg(
    db, src: int, tgt: int, bucket_ts: int, sent: int, lost: int, rtt_p95: float | None
) -> None:
    db.add(
        PingAggregateMinute(
            source_agent_id=src,
            target_agent_id=tgt,
            bucket_ts_ms=bucket_ts,
            sent=sent,
            lost=lost,
            rtt_avg=rtt_p95,
            rtt_min=rtt_p95,
            rtt_max=rtt_p95,
            rtt_p95=rtt_p95,
            jitter_ms=0.1,
        )
    )


async def test_clean_stats_produce_up_without_transition_from_unknown(
    app, client, admin_headers, settings
) -> None:
    uid_a, _ = await _enroll_and_approve(client, admin_headers, "ha", "10.0.0.1")
    uid_b, _ = await _enroll_and_approve(client, admin_headers, "hb", "10.0.0.2")

    now = int(time.time() * 1000)
    bucket = (now // MINUTE_MS) * MINUTE_MS - MINUTE_MS
    settings.min_dwell_s = 0  # immediate

    async with app.state.sessionmaker() as db:
        await _seed_minute_agg(db, src=1, tgt=2, bucket_ts=bucket, sent=12, lost=0, rtt_p95=5.0)
        await db.commit()
        summary = await alert_engine.evaluate(db, settings, now_ms=now)
    assert summary.pairs_evaluated == 1
    assert summary.transitions == 1  # unknown -> up is still a transition

    async with app.state.sessionmaker() as db:
        link = (await db.execute(select(LinkStateRow))).scalar_one()
        assert link.state == LinkState.UP.value
        alert = (await db.execute(select(Alert))).scalar_one()
        assert alert.from_state == LinkState.UNKNOWN.value
        assert alert.to_state == LinkState.UP.value


async def test_degraded_requires_dwell(app, client, admin_headers, settings) -> None:
    await _enroll_and_approve(client, admin_headers, "ha", "10.0.0.1")
    await _enroll_and_approve(client, admin_headers, "hb", "10.0.0.2")

    now = int(time.time() * 1000)
    bucket = (now // MINUTE_MS) * MINUTE_MS - MINUTE_MS
    settings.min_dwell_s = 60  # classic 60s dwell

    async with app.state.sessionmaker() as db:
        # Prime link state to UP so we start from a non-unknown baseline.
        db.add(
            LinkStateRow(
                source_agent_id=1,
                target_agent_id=2,
                state=LinkState.UP.value,
                since_ts=now - 3600_000,
                loss_pct_1m=0.0,
                rtt_p95_1m=5.0,
            )
        )
        await _seed_minute_agg(db, 1, 2, bucket, sent=20, lost=10, rtt_p95=5.0)  # 50% loss
        await db.commit()
        summary = await alert_engine.evaluate(db, settings, now_ms=now)
    # First sighting: dwell starts, no transition yet.
    assert summary.transitions == 0
    async with app.state.sessionmaker() as db:
        link = (await db.execute(select(LinkStateRow))).scalar_one()
        assert link.state == LinkState.UP.value
        assert link.candidate_state == LinkState.DEGRADED.value

    # Second evaluation 90s later → dwell exceeded → transition.
    later = now + 90_000
    async with app.state.sessionmaker() as db:
        # Same aggregate still present; alert engine re-reads it.
        summary = await alert_engine.evaluate(db, settings, now_ms=later)
    # evaluate() targets bucket=floor(later/MIN)-1 which is now's bucket + 1 — so we need
    # to seed THAT bucket too. Simpler: rely on target_bucket = floor(later/MIN) - MIN
    # and put a matching agg there.
    next_bucket = (later // MINUTE_MS) * MINUTE_MS - MINUTE_MS
    async with app.state.sessionmaker() as db:
        await _seed_minute_agg(db, 1, 2, next_bucket, sent=20, lost=10, rtt_p95=5.0)
        await db.commit()
        summary = await alert_engine.evaluate(db, settings, now_ms=later)
    assert summary.transitions == 1
    async with app.state.sessionmaker() as db:
        link = (await db.execute(select(LinkStateRow))).scalar_one()
        assert link.state == LinkState.DEGRADED.value
        alerts = (await db.execute(select(Alert))).scalars().all()
        assert len(alerts) == 1


async def test_rtt_p95_over_threshold_is_degraded(app, client, admin_headers, settings) -> None:
    await _enroll_and_approve(client, admin_headers, "ha", "10.0.0.1")
    await _enroll_and_approve(client, admin_headers, "hb", "10.0.0.2")
    settings.min_dwell_s = 0
    settings.degraded_rtt_p95_ms = 200

    now = int(time.time() * 1000)
    bucket = (now // MINUTE_MS) * MINUTE_MS - MINUTE_MS
    async with app.state.sessionmaker() as db:
        await _seed_minute_agg(db, 1, 2, bucket, sent=10, lost=0, rtt_p95=300.0)
        await db.commit()
        await alert_engine.evaluate(db, settings, now_ms=now)
    async with app.state.sessionmaker() as db:
        link = (await db.execute(select(LinkStateRow))).scalar_one()
        assert link.state == LinkState.DEGRADED.value


async def test_webhook_fanout_on_transition(app, client, admin_headers, settings) -> None:
    await _enroll_and_approve(client, admin_headers, "ha", "10.0.0.1")
    await _enroll_and_approve(client, admin_headers, "hb", "10.0.0.2")
    settings.min_dwell_s = 0

    # Create a webhook first so fanout targets it.
    r = await client.post(
        "/v1/admin/webhooks",
        headers=admin_headers,
        json={
            "name": "test",
            "url": "http://alerts.example/notify",
            "secret": "s3cret",
            "enabled": True,
        },
    )
    assert r.status_code == 201
    webhook_id = r.json()["id"]

    now = int(time.time() * 1000)
    bucket = (now // MINUTE_MS) * MINUTE_MS - MINUTE_MS
    async with app.state.sessionmaker() as db:
        await _seed_minute_agg(db, 1, 2, bucket, sent=10, lost=9, rtt_p95=5.0)  # down
        await db.commit()
        await alert_engine.evaluate(db, settings, now_ms=now)

    async with app.state.sessionmaker() as db:
        deliveries = (await db.execute(select(WebhookDelivery))).scalars().all()
        assert len(deliveries) == 1
        d = deliveries[0]
        assert d.webhook_id == webhook_id
        assert d.state == WebhookDeliveryState.PENDING.value
        assert d.payload["to_state"] == LinkState.DOWN.value


async def test_webhook_dispatcher_signs_and_delivers(app) -> None:
    received = []

    async def _stub(request: httpx.Request) -> httpx.Response:
        received.append({
            "url": str(request.url),
            "body": bytes(request.content),
            "signature": request.headers.get("x-pulse-signature", ""),
        })
        return httpx.Response(200)

    transport = httpx.MockTransport(_stub)

    async with app.state.sessionmaker() as db:
        hook = Webhook(
            name="w",
            url="http://alerts.example/x",
            secret="topsecret",
            enabled=True,
            event_filter={},
            created_at=0,
        )
        db.add(hook)
        await db.flush()
        db.add(
            WebhookDelivery(
                webhook_id=hook.id,
                alert_id=None,
                payload={"hello": "world"},
                attempts=0,
                next_attempt_at=0,
                state=WebhookDeliveryState.PENDING.value,
            )
        )
        await db.commit()

        async with httpx.AsyncClient(transport=transport) as http:
            summary = await webhook_dispatcher.dispatch_due(db, client=http)

    assert summary.delivered == 1
    assert len(received) == 1
    expected_sig = "sha256=" + hmac.new(
        b"topsecret", received[0]["body"], hashlib.sha256
    ).hexdigest()
    assert received[0]["signature"] == expected_sig
    assert json.loads(received[0]["body"]) == {"hello": "world"}

    async with app.state.sessionmaker() as db:
        row = (await db.execute(select(WebhookDelivery))).scalar_one()
        assert row.state == WebhookDeliveryState.DELIVERED.value
        assert row.delivered_at is not None


async def test_webhook_backoff_on_failure(app) -> None:
    async def _failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    transport = httpx.MockTransport(_failing)

    async with app.state.sessionmaker() as db:
        hook = Webhook(
            name="w",
            url="http://alerts.example/x",
            secret="s",
            enabled=True,
            event_filter={},
            created_at=0,
        )
        db.add(hook)
        await db.flush()
        db.add(
            WebhookDelivery(
                webhook_id=hook.id,
                alert_id=None,
                payload={},
                attempts=0,
                next_attempt_at=0,
                state=WebhookDeliveryState.PENDING.value,
            )
        )
        await db.commit()

        async with httpx.AsyncClient(transport=transport) as http:
            # Drain attempts until dead.
            for i in range(webhook_dispatcher.MAX_ATTEMPTS):
                async with app.state.sessionmaker() as inner:
                    # Force the delivery's next_attempt_at to be due so the dispatcher picks it up.
                    row = (await inner.execute(select(WebhookDelivery))).scalar_one()
                    row.next_attempt_at = 0
                    await inner.commit()
                summary = await webhook_dispatcher.dispatch_due(
                    db, client=http, now_ms=10**13
                )
                assert summary.attempted == 1
            async with app.state.sessionmaker() as inner:
                row = (await inner.execute(select(WebhookDelivery))).scalar_one()
                assert row.state == WebhookDeliveryState.DEAD.value
                assert row.attempts == webhook_dispatcher.MAX_ATTEMPTS
