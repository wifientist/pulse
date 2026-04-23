"""Link-state machine and alert emission.

Runs on the minute-rollup tick. For each (source, target) pair seen in recent minute
aggregates, derives a `desired` state from loss% and rtt_p95, then promotes candidate
states only after they've held for a configurable dwell. On promotion, writes an Alert
row and fans out to the webhook outbox.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.config import Settings
from pulse_server.db.models import (
    Agent,
    Alert,
    LinkStateRow,
    PassiveLinkStateRow,
    PassivePingAggregateMinute,
    PingAggregateMinute,
    Webhook,
    WebhookDelivery,
)
from pulse_shared.enums import LinkState, WebhookDeliveryState

MINUTE_MS = 60_000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _derive(
    sent: int,
    lost: int,
    rtt_p95: float | None,
    settings: Settings,
) -> LinkState:
    if sent == 0:
        return LinkState.UNKNOWN
    loss_pct = 100.0 * lost / sent
    if loss_pct >= settings.down_loss_pct:
        return LinkState.DOWN
    if loss_pct >= settings.degraded_loss_pct:
        return LinkState.DEGRADED
    if rtt_p95 is not None and rtt_p95 > settings.degraded_rtt_p95_ms:
        return LinkState.DEGRADED
    return LinkState.UP


@dataclass(frozen=True)
class EvaluateSummary:
    pairs_evaluated: int
    transitions: int


async def evaluate(
    db: AsyncSession, settings: Settings, now_ms: int | None = None
) -> EvaluateSummary:
    """Pull the most recent completed minute aggregate per pair and evaluate."""
    now = now_ms or _now_ms()
    current_bucket = (now // MINUTE_MS) * MINUTE_MS
    # Look at the bucket that just completed. If it doesn't exist yet (no rollup this
    # minute) we still evaluate what's there and let dwell/recovery handle jitter.
    target_bucket = current_bucket - MINUTE_MS

    aggs = (
        await db.execute(
            select(PingAggregateMinute).where(PingAggregateMinute.bucket_ts_ms == target_bucket)
        )
    ).scalars().all()

    transitions = 0
    for agg in aggs:
        desired = _derive(agg.sent, agg.lost, agg.rtt_p95, settings)
        loss_pct = 100.0 * agg.lost / agg.sent if agg.sent else None

        row = (
            await db.execute(
                select(LinkStateRow).where(
                    LinkStateRow.source_agent_id == agg.source_agent_id,
                    LinkStateRow.target_agent_id == agg.target_agent_id,
                )
            )
        ).scalar_one_or_none()

        if row is None:
            row = LinkStateRow(
                source_agent_id=agg.source_agent_id,
                target_agent_id=agg.target_agent_id,
                state=LinkState.UNKNOWN.value,
                since_ts=now,
                loss_pct_1m=loss_pct,
                rtt_p95_1m=agg.rtt_p95,
                candidate_state=None,
                candidate_since_ts=None,
            )
            db.add(row)

        row.loss_pct_1m = loss_pct
        row.rtt_p95_1m = agg.rtt_p95

        if desired.value == row.state:
            row.candidate_state = None
            row.candidate_since_ts = None
            continue

        # Dwell: recovery (going back to UP from a non-UP state) requires the longer
        # recovery window; every other transition uses MIN_DWELL_S.
        dwell_s = (
            settings.recovery_window_s
            if desired == LinkState.UP and row.state != LinkState.UNKNOWN.value
            else settings.min_dwell_s
        )

        if row.candidate_state != desired.value or row.candidate_since_ts is None:
            # Start (or restart) tracking this candidate.
            row.candidate_state = desired.value
            row.candidate_since_ts = now

        if now - row.candidate_since_ts >= dwell_s * 1000:
            # Promote.
            context = {
                "loss_pct_1m": loss_pct,
                "rtt_p95_1m": agg.rtt_p95,
                "sent": agg.sent,
                "lost": agg.lost,
                "dwell_s": dwell_s,
            }
            db.add(
                Alert(
                    source_agent_id=agg.source_agent_id,
                    target_agent_id=agg.target_agent_id,
                    from_state=row.state,
                    to_state=desired.value,
                    at_ts=now,
                    context=context,
                )
            )
            await db.flush()
            alert = (
                await db.execute(
                    select(Alert)
                    .where(
                        Alert.source_agent_id == agg.source_agent_id,
                        Alert.target_agent_id == agg.target_agent_id,
                        Alert.at_ts == now,
                    )
                    .order_by(Alert.id.desc())
                    .limit(1)
                )
            ).scalar_one()

            await _fanout_webhooks(db, alert)

            row.state = desired.value
            row.since_ts = now
            row.candidate_state = None
            row.candidate_since_ts = None
            transitions += 1

    # Passive targets — mirror the same state-machine but against the parallel
    # passive tables. No webhook fan-out for v1; we just record link state so
    # the UI can render per-(agent, target) color.
    passive_aggs = (
        await db.execute(
            select(PassivePingAggregateMinute).where(
                PassivePingAggregateMinute.bucket_ts_ms == target_bucket
            )
        )
    ).scalars().all()
    for agg in passive_aggs:
        desired = _derive(agg.sent, agg.lost, agg.rtt_p95, settings)
        loss_pct = 100.0 * agg.lost / agg.sent if agg.sent else None

        prow = (
            await db.execute(
                select(PassiveLinkStateRow).where(
                    PassiveLinkStateRow.source_agent_id == agg.source_agent_id,
                    PassiveLinkStateRow.passive_target_id == agg.passive_target_id,
                )
            )
        ).scalar_one_or_none()
        if prow is None:
            prow = PassiveLinkStateRow(
                source_agent_id=agg.source_agent_id,
                passive_target_id=agg.passive_target_id,
                state=LinkState.UNKNOWN.value,
                since_ts=now,
                loss_pct_1m=loss_pct,
                rtt_p95_1m=agg.rtt_p95,
                candidate_state=None,
                candidate_since_ts=None,
            )
            db.add(prow)

        prow.loss_pct_1m = loss_pct
        prow.rtt_p95_1m = agg.rtt_p95
        if desired.value == prow.state:
            prow.candidate_state = None
            prow.candidate_since_ts = None
            continue

        dwell_s = (
            settings.recovery_window_s
            if desired == LinkState.UP and prow.state != LinkState.UNKNOWN.value
            else settings.min_dwell_s
        )
        if prow.candidate_state != desired.value or prow.candidate_since_ts is None:
            prow.candidate_state = desired.value
            prow.candidate_since_ts = now
        if now - prow.candidate_since_ts >= dwell_s * 1000:
            prow.state = desired.value
            prow.since_ts = now
            prow.candidate_state = None
            prow.candidate_since_ts = None
            transitions += 1

    await db.commit()
    return EvaluateSummary(
        pairs_evaluated=len(aggs) + len(passive_aggs), transitions=transitions
    )


async def _fanout_webhooks(db: AsyncSession, alert: Alert) -> None:
    hooks = (
        await db.execute(select(Webhook).where(Webhook.enabled.is_(True)))
    ).scalars().all()
    if not hooks:
        return

    src = await db.get(Agent, alert.source_agent_id)
    tgt = await db.get(Agent, alert.target_agent_id)
    payload = {
        "event": "link_state_change",
        "alert_id": alert.id,
        "at_ts_ms": alert.at_ts,
        "source": {
            "agent_uid": src.agent_uid if src else None,
            "hostname": src.hostname if src else None,
            "ip": src.primary_ip if src else None,
        },
        "target": {
            "agent_uid": tgt.agent_uid if tgt else None,
            "hostname": tgt.hostname if tgt else None,
            "ip": tgt.primary_ip if tgt else None,
        },
        "from_state": alert.from_state,
        "to_state": alert.to_state,
        "stats_1m": alert.context,
    }
    now = _now_ms()
    for hook in hooks:
        db.add(
            WebhookDelivery(
                webhook_id=hook.id,
                alert_id=alert.id,
                payload=payload,
                attempts=0,
                next_attempt_at=now,
                last_error=None,
                delivered_at=None,
                state=WebhookDeliveryState.PENDING.value,
            )
        )
