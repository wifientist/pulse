"""Ping sample rollups and retention.

Raw samples go into `ping_samples_raw` as agents poll. Two scheduled jobs condense them:
  - minute rollup: aggregate raw samples into per-minute buckets per (source, target)
  - hour rollup:   aggregate minute buckets into per-hour buckets (same schema)

A separate retention prune deletes raw samples older than `raw_retention_hours` once
their minute bucket has been rolled.

Jitter is computed as the mean absolute difference between consecutive RTTs in the
bucket (ordered by ts_ms). Hour p95 is the weighted max of minute p95s — a documented
approximation that avoids re-reading raw samples.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.config import Settings
from pulse_server.db.models import (
    PingAggregateHour,
    PingAggregateMinute,
    PingSampleRaw,
)
from pulse_server.repo import meta_repo

MINUTE_MS = 60_000
HOUR_MS = 3_600_000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    values_sorted = sorted(values)
    if len(values_sorted) == 1:
        return values_sorted[0]
    # Nearest-rank percentile: pick the index at ceil(pct * n) - 1 (clamped).
    import math

    rank = max(1, math.ceil(pct * len(values_sorted)))
    return values_sorted[rank - 1]


def _jitter(rtts_in_order: list[float]) -> float | None:
    if len(rtts_in_order) < 2:
        return None
    diffs = [abs(b - a) for a, b in zip(rtts_in_order, rtts_in_order[1:])]
    return sum(diffs) / len(diffs)


@dataclass(frozen=True)
class MinuteRollupSummary:
    buckets_rolled: int
    aggregates_written: int


async def rollup_minute(db: AsyncSession, now_ms: int | None = None) -> MinuteRollupSummary:
    """Roll complete minute buckets that have not been rolled yet.

    A bucket is complete when its end boundary is < now. We never roll the current
    minute — samples are still arriving.
    """
    now_ms = now_ms or _now_ms()
    last_rolled = await meta_repo.get_int(db, meta_repo.LAST_MINUTE_BUCKET_ROLLED, 0)
    latest_complete_end = (now_ms // MINUTE_MS) * MINUTE_MS  # exclusive upper bound

    # Always check the oldest raw sample. If a sample landed in a bucket older than our
    # cursor (e.g. a late-arriving agent poll), we must still roll it up — so pick the
    # smaller of `last_rolled + MINUTE_MS` and the oldest sample's bucket.
    oldest = (
        await db.execute(select(PingSampleRaw.ts_ms).order_by(PingSampleRaw.ts_ms).limit(1))
    ).scalar_one_or_none()

    if oldest is None:
        # No samples yet; don't advance the cursor so the first batch of samples won't
        # be skipped when they arrive after this call.
        return MinuteRollupSummary(0, 0)

    oldest_bucket = (oldest // MINUTE_MS) * MINUTE_MS
    if last_rolled == 0:
        start_bucket = oldest_bucket
    else:
        start_bucket = min(last_rolled + MINUTE_MS, oldest_bucket)

    if start_bucket >= latest_complete_end:
        return MinuteRollupSummary(0, 0)

    # Pull every sample in the window [start_bucket, latest_complete_end) in one query.
    rows = (
        await db.execute(
            select(
                PingSampleRaw.source_agent_id,
                PingSampleRaw.target_agent_id,
                PingSampleRaw.ts_ms,
                PingSampleRaw.rtt_ms,
                PingSampleRaw.lost,
            )
            .where(
                PingSampleRaw.ts_ms >= start_bucket,
                PingSampleRaw.ts_ms < latest_complete_end,
            )
            .order_by(PingSampleRaw.ts_ms)
        )
    ).all()

    buckets: dict[tuple[int, int, int], list[tuple[int, float | None, bool]]] = {}
    for source, target, ts, rtt, lost in rows:
        bucket = (ts // MINUTE_MS) * MINUTE_MS
        buckets.setdefault((source, target, bucket), []).append((ts, rtt, lost))

    payload = []
    for (src, tgt, bucket), samples in buckets.items():
        sent = len(samples)
        lost = sum(1 for _, _, l in samples if l)
        rtts_sorted_by_ts = [r for _, r, l in samples if r is not None]
        rtt_min = min(rtts_sorted_by_ts) if rtts_sorted_by_ts else None
        rtt_max = max(rtts_sorted_by_ts) if rtts_sorted_by_ts else None
        rtt_avg = (
            sum(rtts_sorted_by_ts) / len(rtts_sorted_by_ts) if rtts_sorted_by_ts else None
        )
        rtt_p95 = _percentile(rtts_sorted_by_ts, 0.95) if rtts_sorted_by_ts else None
        jitter = _jitter(rtts_sorted_by_ts)
        payload.append(
            {
                "source_agent_id": src,
                "target_agent_id": tgt,
                "bucket_ts_ms": bucket,
                "sent": sent,
                "lost": lost,
                "rtt_avg": rtt_avg,
                "rtt_min": rtt_min,
                "rtt_max": rtt_max,
                "rtt_p95": rtt_p95,
                "jitter_ms": jitter,
            }
        )

    if payload:
        stmt = sqlite_insert(PingAggregateMinute).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_agent_id", "target_agent_id", "bucket_ts_ms"],
            set_={
                "sent": stmt.excluded.sent,
                "lost": stmt.excluded.lost,
                "rtt_avg": stmt.excluded.rtt_avg,
                "rtt_min": stmt.excluded.rtt_min,
                "rtt_max": stmt.excluded.rtt_max,
                "rtt_p95": stmt.excluded.rtt_p95,
                "jitter_ms": stmt.excluded.jitter_ms,
            },
        )
        await db.execute(stmt)
        # Only advance the cursor when we actually rolled something. If samples hadn't
        # arrived yet, keeping the old cursor ensures a later call will still pick them up.
        await meta_repo.set_int(
            db, meta_repo.LAST_MINUTE_BUCKET_ROLLED, latest_complete_end - MINUTE_MS
        )
    await db.commit()
    return MinuteRollupSummary(
        buckets_rolled=len(buckets),
        aggregates_written=len(payload),
    )


@dataclass(frozen=True)
class HourRollupSummary:
    buckets_rolled: int
    aggregates_written: int


async def rollup_hour(db: AsyncSession, now_ms: int | None = None) -> HourRollupSummary:
    """Fold minute aggregates into hour aggregates.

    Hour p95 is the weighted max of minute p95s — an approximation documented in the
    plan. Computing exact p95 across 60 minutes would require re-reading raw samples
    which defeats the purpose of having aggregates.
    """
    now_ms = now_ms or _now_ms()
    last_rolled = await meta_repo.get_int(db, meta_repo.LAST_HOUR_BUCKET_ROLLED, 0)
    latest_complete_end = (now_ms // HOUR_MS) * HOUR_MS

    if last_rolled == 0:
        oldest = (
            await db.execute(
                select(PingAggregateMinute.bucket_ts_ms)
                .order_by(PingAggregateMinute.bucket_ts_ms)
                .limit(1)
            )
        ).scalar_one_or_none()
        if oldest is None:
            await meta_repo.set_int(db, meta_repo.LAST_HOUR_BUCKET_ROLLED, latest_complete_end)
            await db.commit()
            return HourRollupSummary(0, 0)
        start_bucket = (oldest // HOUR_MS) * HOUR_MS
    else:
        start_bucket = last_rolled + HOUR_MS

    if start_bucket >= latest_complete_end:
        return HourRollupSummary(0, 0)

    rows = (
        await db.execute(
            select(PingAggregateMinute).where(
                PingAggregateMinute.bucket_ts_ms >= start_bucket,
                PingAggregateMinute.bucket_ts_ms < latest_complete_end,
            )
        )
    ).scalars().all()

    buckets: dict[tuple[int, int, int], list[PingAggregateMinute]] = {}
    for r in rows:
        bucket = (r.bucket_ts_ms // HOUR_MS) * HOUR_MS
        buckets.setdefault((r.source_agent_id, r.target_agent_id, bucket), []).append(r)

    payload = []
    for (src, tgt, bucket), mins in buckets.items():
        sent = sum(m.sent for m in mins)
        lost = sum(m.lost for m in mins)
        rtts = [m.rtt_avg for m in mins if m.rtt_avg is not None]
        rtt_avg = sum(rtts) / len(rtts) if rtts else None
        rtt_min = min((m.rtt_min for m in mins if m.rtt_min is not None), default=None)
        rtt_max = max((m.rtt_max for m in mins if m.rtt_max is not None), default=None)
        rtt_p95 = max((m.rtt_p95 for m in mins if m.rtt_p95 is not None), default=None)
        jitters = [m.jitter_ms for m in mins if m.jitter_ms is not None]
        jitter = sum(jitters) / len(jitters) if jitters else None
        payload.append(
            {
                "source_agent_id": src,
                "target_agent_id": tgt,
                "bucket_ts_ms": bucket,
                "sent": sent,
                "lost": lost,
                "rtt_avg": rtt_avg,
                "rtt_min": rtt_min,
                "rtt_max": rtt_max,
                "rtt_p95": rtt_p95,
                "jitter_ms": jitter,
            }
        )

    if payload:
        stmt = sqlite_insert(PingAggregateHour).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_agent_id", "target_agent_id", "bucket_ts_ms"],
            set_={
                "sent": stmt.excluded.sent,
                "lost": stmt.excluded.lost,
                "rtt_avg": stmt.excluded.rtt_avg,
                "rtt_min": stmt.excluded.rtt_min,
                "rtt_max": stmt.excluded.rtt_max,
                "rtt_p95": stmt.excluded.rtt_p95,
                "jitter_ms": stmt.excluded.jitter_ms,
            },
        )
        await db.execute(stmt)

    await meta_repo.set_int(
        db, meta_repo.LAST_HOUR_BUCKET_ROLLED, latest_complete_end - HOUR_MS
    )
    await db.commit()
    return HourRollupSummary(
        buckets_rolled=len(buckets),
        aggregates_written=len(payload),
    )


@dataclass(frozen=True)
class PruneSummary:
    raw_deleted: int
    minute_deleted: int


async def prune(
    db: AsyncSession,
    settings: Settings,
    now_ms: int | None = None,
    chunk: int = 5000,
) -> PruneSummary:
    now_ms = now_ms or _now_ms()
    last_minute_rolled = await meta_repo.get_int(db, meta_repo.LAST_MINUTE_BUCKET_ROLLED, 0)

    raw_cutoff = now_ms - settings.raw_retention_hours * HOUR_MS
    # Never drop raw samples from a bucket that hasn't been rolled — we'd lose the data.
    raw_cutoff = min(raw_cutoff, last_minute_rolled)

    raw_deleted = 0
    while True:
        # SQLite supports DELETE ... LIMIT only when compiled with SQLITE_ENABLE_UPDATE_DELETE_LIMIT.
        # Emulate chunked delete via a subquery on the primary key.
        ids_to_delete = (
            await db.execute(
                select(PingSampleRaw.id)
                .where(PingSampleRaw.ts_ms < raw_cutoff)
                .limit(chunk)
            )
        ).scalars().all()
        if not ids_to_delete:
            break
        await db.execute(
            delete(PingSampleRaw).where(PingSampleRaw.id.in_(ids_to_delete))
        )
        raw_deleted += len(ids_to_delete)
        await db.commit()
        if len(ids_to_delete) < chunk:
            break

    minute_cutoff = now_ms - settings.minute_retention_days * 24 * HOUR_MS
    minute_deleted = (
        await db.execute(
            delete(PingAggregateMinute).where(PingAggregateMinute.bucket_ts_ms < minute_cutoff)
        )
    ).rowcount or 0
    await db.commit()

    return PruneSummary(raw_deleted=raw_deleted, minute_deleted=minute_deleted)
