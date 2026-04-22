"""Rollup and retention math."""

from __future__ import annotations

from sqlalchemy import select

from pulse_server.db.models import (
    PingAggregateHour,
    PingAggregateMinute,
    PingSampleRaw,
)
from pulse_server.repo import meta_repo
from pulse_server.services import rollup_service
from pulse_server.services.rollup_service import HOUR_MS, MINUTE_MS


async def _seed_sample(
    db, source_agent_id: int, target_agent_id: int, ts_ms: int, rtt_ms: float | None, lost: bool
) -> None:
    db.add(
        PingSampleRaw(
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            ts_ms=ts_ms,
            rtt_ms=rtt_ms,
            lost=lost,
            seq=None,
        )
    )


async def test_minute_rollup_basic(app) -> None:
    # Bucket at T=60_000_000 (an arbitrary round minute)
    bucket = 60_000_000
    now = bucket + 2 * MINUTE_MS  # two full minutes past the bucket end
    async with app.state.sessionmaker() as db:
        for ts in (bucket + 1_000, bucket + 20_000, bucket + 40_000):
            await _seed_sample(db, 1, 2, ts, rtt_ms=2.0, lost=False)
        await _seed_sample(db, 1, 2, bucket + 55_000, rtt_ms=None, lost=True)
        await db.commit()

        summary = await rollup_service.rollup_minute(db, now_ms=now)

    assert summary.buckets_rolled == 1
    assert summary.aggregates_written == 1

    async with app.state.sessionmaker() as db:
        rows = (await db.execute(select(PingAggregateMinute))).scalars().all()
        assert len(rows) == 1
        agg = rows[0]
        assert agg.bucket_ts_ms == bucket
        assert agg.sent == 4
        assert agg.lost == 1
        assert agg.rtt_min == 2.0
        assert agg.rtt_max == 2.0
        assert agg.rtt_avg == 2.0


async def test_minute_rollup_current_minute_is_not_rolled(app) -> None:
    async with app.state.sessionmaker() as db:
        # 30s into the current minute; rollup must not touch it.
        now_bucket = (1_234_567_000 // MINUTE_MS) * MINUTE_MS
        now = now_bucket + 30_000
        await _seed_sample(db, 1, 2, now_bucket + 5_000, 1.0, False)
        await db.commit()
        summary = await rollup_service.rollup_minute(db, now_ms=now)

    assert summary.aggregates_written == 0
    async with app.state.sessionmaker() as db:
        rows = (await db.execute(select(PingAggregateMinute))).scalars().all()
        assert rows == []


async def test_minute_rollup_advances_meta(app) -> None:
    bucket = 60_000_000
    now = bucket + 5 * MINUTE_MS

    async with app.state.sessionmaker() as db:
        await _seed_sample(db, 1, 2, bucket + 100, 1.0, False)
        await db.commit()
        await rollup_service.rollup_minute(db, now_ms=now)
        last = await meta_repo.get_int(db, meta_repo.LAST_MINUTE_BUCKET_ROLLED)
    # The last completely-rolled bucket is (now // MINUTE_MS - 1) * MINUTE_MS.
    assert last == (now // MINUTE_MS) * MINUTE_MS - MINUTE_MS


async def test_rollup_idempotent_on_repeat_run(app) -> None:
    """Repeat runs with the same raw samples produce the same final aggregate row.

    The upsert path means a second call may re-write the row (the old row and the new
    row have identical values), so we don't check aggregates_written — we check that the
    final state is correct and that the row count hasn't blown up.
    """
    bucket = 60_000_000
    now = bucket + 3 * MINUTE_MS
    async with app.state.sessionmaker() as db:
        for ts in (bucket + 1000, bucket + 30_000):
            await _seed_sample(db, 1, 2, ts, 3.0, False)
        await db.commit()
        await rollup_service.rollup_minute(db, now_ms=now)
        await rollup_service.rollup_minute(db, now_ms=now)

    async with app.state.sessionmaker() as db:
        rows = (await db.execute(select(PingAggregateMinute))).scalars().all()
        assert len(rows) == 1
        assert rows[0].sent == 2


async def test_jitter_is_mean_abs_diff(app) -> None:
    bucket = 60_000_000
    now = bucket + 2 * MINUTE_MS
    async with app.state.sessionmaker() as db:
        rtts = [1.0, 3.0, 2.0, 6.0]  # diffs: 2.0, 1.0, 4.0 → jitter = 7/3 ≈ 2.333
        for i, r in enumerate(rtts):
            await _seed_sample(db, 1, 2, bucket + i * 1000, r, False)
        await db.commit()
        await rollup_service.rollup_minute(db, now_ms=now)

    async with app.state.sessionmaker() as db:
        row = (await db.execute(select(PingAggregateMinute))).scalar_one()
        assert row.jitter_ms is not None
        assert abs(row.jitter_ms - (7 / 3)) < 1e-6


async def test_hour_rollup_folds_minute_aggregates(app) -> None:
    hour_start = 100_000_000 - (100_000_000 % HOUR_MS)
    async with app.state.sessionmaker() as db:
        # Seed 3 minute aggregates inside one hour.
        for offset_min in range(3):
            db.add(
                PingAggregateMinute(
                    source_agent_id=1,
                    target_agent_id=2,
                    bucket_ts_ms=hour_start + offset_min * MINUTE_MS,
                    sent=10,
                    lost=1,
                    rtt_avg=2.0,
                    rtt_min=1.5,
                    rtt_max=3.0,
                    rtt_p95=2.8,
                    jitter_ms=0.5,
                )
            )
        await db.commit()
        now = hour_start + 2 * HOUR_MS
        summary = await rollup_service.rollup_hour(db, now_ms=now)

    assert summary.aggregates_written == 1
    async with app.state.sessionmaker() as db:
        row = (await db.execute(select(PingAggregateHour))).scalar_one()
        assert row.bucket_ts_ms == hour_start
        assert row.sent == 30
        assert row.lost == 3
        assert row.rtt_max == 3.0
        assert row.rtt_min == 1.5
        assert row.rtt_p95 == 2.8  # weighted max-of-minute-p95


async def test_prune_deletes_old_raw_but_keeps_unrolled(app, settings) -> None:
    now = 500_000_000
    async with app.state.sessionmaker() as db:
        # Roll a minute so last_minute_rolled advances.
        old_bucket = now - 10 * HOUR_MS  # comfortably older than the retention cutoff
        await _seed_sample(db, 1, 2, old_bucket + 1000, 1.0, False)
        await rollup_service.rollup_minute(db, now_ms=old_bucket + 2 * MINUTE_MS)
        # Also insert a sample that has NOT been rolled yet (belongs to now's minute).
        current_bucket = (now // MINUTE_MS) * MINUTE_MS
        await _seed_sample(db, 1, 2, current_bucket + 1000, 1.0, False)
        await db.commit()

    # Retention is `settings.raw_retention_hours` (default 48); we force it low for test.
    settings.raw_retention_hours = 1
    async with app.state.sessionmaker() as db:
        summary = await rollup_service.prune(db, settings, now_ms=now)

    assert summary.raw_deleted == 1
    async with app.state.sessionmaker() as db:
        remaining = (await db.execute(select(PingSampleRaw))).scalars().all()
        assert len(remaining) == 1
        assert remaining[0].ts_ms >= current_bucket
