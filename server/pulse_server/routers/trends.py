"""Historical trends for a given source→target pair.

Returns a tidy time-series ready for charting. Picks the tier automatically based
on the requested range:
  - range ≤ 2h    → raw tier (per-second buckets or raw samples, fine enough to
                    see 1 Hz boost data immediately)
  - 2h < range ≤ 24h → minute aggregates
  - range > 24h   → hour aggregates

Wireless samples from both endpoints are included when either side has wireless
activity during the window, so the UI can render a signal chart + roam markers.
"""

from __future__ import annotations

import math
import statistics
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import (
    Agent,
    AgentInterface,
    PingAggregateHour,
    PingAggregateMinute,
    PingSampleRaw,
    WirelessSample,
    WirelessScanSample,
)
from pulse_server.db.session import get_db
from pulse_server.security.deps import require_admin

router = APIRouter(
    prefix="/v1/admin/trends",
    tags=["admin", "trends"],
    dependencies=[Depends(require_admin)],
)


class TrendPoint(BaseModel):
    ts_ms: int
    sent: int
    lost: int
    loss_pct: float | None
    rtt_avg_ms: float | None
    rtt_min_ms: float | None
    rtt_max_ms: float | None
    rtt_p50_ms: float | None
    rtt_p95_ms: float | None
    rtt_p99_ms: float | None
    jitter_ms: float | None


class TrendSummary(BaseModel):
    sent_total: int
    lost_total: int
    loss_pct: float | None
    rtt_avg_ms: float | None
    rtt_p95_ms: float | None
    point_count: int


class WirelessTrendPoint(BaseModel):
    ts_ms: int
    ssid: str | None
    bssid: str | None
    signal_dbm: int | None


class WirelessTrendSeries(BaseModel):
    agent_uid: str
    hostname: str | None
    iface_name: str | None
    points: list[WirelessTrendPoint]
    # Roam events: each entry marks the first point of a new BSSID.
    roams: list[dict]  # [{ts_ms, from_bssid, to_bssid}]
    bssid_frequencies: dict[str, int] = {}
    """Most-recent `frequency_mhz` per BSSID, sourced from wireless_scan_samples
    if a monitor agent has ever seen it. Empty for BSSIDs never scanned — the
    client-connect path doesn't capture frequency. Lets the UI show a band
    badge ("2.4/5/6 GHz") without extending the raw wireless sample schema."""


class TrendResponse(BaseModel):
    source_agent_uid: str
    target_agent_uid: str
    since_ts: int
    until_ts: int
    granularity: str  # "raw" | "minute" | "hour"
    bucket_s: int | None  # present when granularity == "raw"
    points: list[TrendPoint]
    summary: TrendSummary
    wireless: list[WirelessTrendSeries] = []


MINUTE_MS = 60_000
HOUR_MS = 3_600_000
RAW_CUTOFF_MS = 2 * HOUR_MS
MINUTE_CUTOFF_MS = 24 * HOUR_MS


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _agent_row(db: AsyncSession, uid: str) -> Agent | None:
    return (
        await db.execute(select(Agent).where(Agent.agent_uid == uid))
    ).scalar_one_or_none()


def _pick_bucket_s(window_s: int) -> int:
    """Choose a raw bucket size aimed at ~120 points max in the chart — enough
    detail to see fluctuations without drowning the renderer."""
    if window_s <= 60:
        return 1
    if window_s <= 300:
        return 5
    if window_s <= 600:
        return 10
    if window_s <= 1800:
        return 30
    return 60


def _pct(sorted_values: list[float], p: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = k - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def _bucket_raw(
    samples: list[PingSampleRaw],
    since_ts: int,
    bucket_ms: int,
) -> list[TrendPoint]:
    if not samples:
        return []
    by_bucket: dict[int, list[PingSampleRaw]] = {}
    for s in samples:
        key = ((s.ts_ms - since_ts) // bucket_ms) * bucket_ms + since_ts
        by_bucket.setdefault(key, []).append(s)
    out: list[TrendPoint] = []
    for bucket_ts in sorted(by_bucket.keys()):
        rows = by_bucket[bucket_ts]
        sent = len(rows)
        lost = sum(1 for r in rows if r.lost)
        rtts = sorted(r.rtt_ms for r in rows if not r.lost and r.rtt_ms is not None)
        # Ordered by arrival for jitter
        ordered = [r.rtt_ms for r in sorted(rows, key=lambda x: x.ts_ms)
                   if not r.lost and r.rtt_ms is not None]
        jitter = None
        if len(ordered) > 1:
            diffs = [abs(ordered[i] - ordered[i - 1]) for i in range(1, len(ordered))]
            jitter = statistics.mean(diffs)
        out.append(
            TrendPoint(
                ts_ms=bucket_ts,
                sent=sent,
                lost=lost,
                loss_pct=(round(100.0 * lost / sent, 3) if sent else None),
                rtt_avg_ms=(round(statistics.mean(rtts), 4) if rtts else None),
                rtt_min_ms=(round(min(rtts), 4) if rtts else None),
                rtt_max_ms=(round(max(rtts), 4) if rtts else None),
                rtt_p50_ms=(round(_pct(rtts, 0.50) or 0, 4) if rtts else None),
                rtt_p95_ms=(round(_pct(rtts, 0.95) or 0, 4) if rtts else None),
                rtt_p99_ms=(round(_pct(rtts, 0.99) or 0, 4) if rtts else None),
                jitter_ms=(round(jitter, 4) if jitter is not None else None),
            )
        )
    return out


@router.get("", response_model=TrendResponse)
async def get_trends(
    source_uid: str = Query(..., alias="source_uid"),
    target_uid: str = Query(..., alias="target_uid"),
    since_ts: int = Query(..., description="Unix ms, inclusive lower bound"),
    until_ts: int | None = Query(
        default=None, description="Unix ms, inclusive upper bound; defaults to now"
    ),
    granularity: str = Query(
        default="auto", pattern="^(auto|raw|minute|hour)$"
    ),
    db: AsyncSession = Depends(get_db),
) -> TrendResponse:
    until_ts = until_ts if until_ts is not None else _now_ms()
    if until_ts <= since_ts:
        raise HTTPException(status_code=400, detail="until_ts must be after since_ts")

    src = await _agent_row(db, source_uid)
    tgt = await _agent_row(db, target_uid)
    if src is None or tgt is None:
        raise HTTPException(status_code=404, detail="unknown agent uid(s)")

    window_ms = until_ts - since_ts
    if granularity == "auto":
        if window_ms <= RAW_CUTOFF_MS:
            granularity = "raw"
        elif window_ms <= MINUTE_CUTOFF_MS:
            granularity = "minute"
        else:
            granularity = "hour"

    bucket_s: int | None = None
    points: list[TrendPoint] = []

    if granularity == "raw":
        bucket_s = _pick_bucket_s(math.ceil(window_ms / 1000))
        raw = (
            await db.execute(
                select(PingSampleRaw).where(
                    PingSampleRaw.source_agent_id == src.id,
                    PingSampleRaw.target_agent_id == tgt.id,
                    PingSampleRaw.ts_ms >= since_ts,
                    PingSampleRaw.ts_ms <= until_ts,
                )
            )
        ).scalars().all()
        points = _bucket_raw(raw, since_ts, bucket_s * 1000)
    elif granularity == "minute":
        rows = (
            await db.execute(
                select(PingAggregateMinute)
                .where(
                    PingAggregateMinute.source_agent_id == src.id,
                    PingAggregateMinute.target_agent_id == tgt.id,
                    PingAggregateMinute.bucket_ts_ms >= since_ts,
                    PingAggregateMinute.bucket_ts_ms <= until_ts,
                )
                .order_by(PingAggregateMinute.bucket_ts_ms)
            )
        ).scalars().all()
        points = [
            TrendPoint(
                ts_ms=r.bucket_ts_ms,
                sent=r.sent,
                lost=r.lost,
                loss_pct=(100.0 * r.lost / r.sent) if r.sent else None,
                rtt_avg_ms=r.rtt_avg,
                rtt_min_ms=r.rtt_min,
                rtt_max_ms=r.rtt_max,
                rtt_p50_ms=r.rtt_p50,
                rtt_p95_ms=r.rtt_p95,
                rtt_p99_ms=r.rtt_p99,
                jitter_ms=r.jitter_ms,
            )
            for r in rows
        ]
    else:  # hour
        rows = (
            await db.execute(
                select(PingAggregateHour)
                .where(
                    PingAggregateHour.source_agent_id == src.id,
                    PingAggregateHour.target_agent_id == tgt.id,
                    PingAggregateHour.bucket_ts_ms >= since_ts,
                    PingAggregateHour.bucket_ts_ms <= until_ts,
                )
                .order_by(PingAggregateHour.bucket_ts_ms)
            )
        ).scalars().all()
        points = [
            TrendPoint(
                ts_ms=r.bucket_ts_ms,
                sent=r.sent,
                lost=r.lost,
                loss_pct=(100.0 * r.lost / r.sent) if r.sent else None,
                rtt_avg_ms=r.rtt_avg,
                rtt_min_ms=r.rtt_min,
                rtt_max_ms=r.rtt_max,
                rtt_p50_ms=r.rtt_p50,
                rtt_p95_ms=r.rtt_p95,
                rtt_p99_ms=r.rtt_p99,
                jitter_ms=r.jitter_ms,
            )
            for r in rows
        ]

    # Summary tiles.
    sent_total = sum(p.sent for p in points)
    lost_total = sum(p.lost for p in points)
    rtt_avg_weighted = sum(
        (p.rtt_avg_ms or 0) * p.sent for p in points if p.rtt_avg_ms is not None
    )
    rtt_avg_denom = sum(p.sent for p in points if p.rtt_avg_ms is not None)
    p95_values = [p.rtt_p95_ms for p in points if p.rtt_p95_ms is not None]
    summary = TrendSummary(
        sent_total=sent_total,
        lost_total=lost_total,
        loss_pct=(round(100.0 * lost_total / sent_total, 3) if sent_total else None),
        rtt_avg_ms=(
            round(rtt_avg_weighted / rtt_avg_denom, 4) if rtt_avg_denom else None
        ),
        rtt_p95_ms=(round(max(p95_values), 4) if p95_values else None),
        point_count=len(points),
    )

    wireless = await _wireless_series(db, [src, tgt], since_ts, until_ts)

    return TrendResponse(
        source_agent_uid=source_uid,
        target_agent_uid=target_uid,
        since_ts=since_ts,
        until_ts=until_ts,
        granularity=granularity,
        bucket_s=bucket_s,
        points=points,
        summary=summary,
        wireless=wireless,
    )


async def _wireless_series(
    db: AsyncSession, agents: list[Agent], since_ts: int, until_ts: int
) -> list[WirelessTrendSeries]:
    """For each participating agent, if the window contains wireless samples, emit
    a series. Skipped entirely for wired-only pairs so the UI can decide not to
    render the wireless chart at all."""
    if not agents:
        return []
    agent_ids = [a.id for a in agents]
    samples = (
        await db.execute(
            select(WirelessSample)
            .where(
                WirelessSample.agent_id.in_(agent_ids),
                WirelessSample.ts_ms >= since_ts,
                WirelessSample.ts_ms <= until_ts,
            )
            .order_by(WirelessSample.ts_ms)
        )
    ).scalars().all()
    if not samples:
        return []

    iface_ids = {int(s.agent_interface_id) for s in samples}
    iface_rows = (
        await db.execute(
            select(AgentInterface).where(AgentInterface.id.in_(iface_ids))
        )
    ).scalars().all()
    iface_name_by_id = {i.id: i.iface_name for i in iface_rows}
    hostname_by_id = {a.id: a.hostname for a in agents}
    uid_by_id = {a.id: a.agent_uid for a in agents}

    by_key: dict[tuple[int, int], list[WirelessSample]] = {}
    for s in samples:
        by_key.setdefault(
            (int(s.agent_id), int(s.agent_interface_id)), []
        ).append(s)

    # BSSID → most-recent frequency, pulled from the airspace scan table if a
    # monitor agent has ever observed any of these BSSIDs. Empty otherwise.
    bssids = {s.bssid for s in samples if s.bssid}
    bssid_freq_map: dict[str, int] = {}
    if bssids:
        # func.max picks the most recent freq per bssid in one query.
        from sqlalchemy import func as sa_func

        freq_rows = (
            await db.execute(
                select(
                    WirelessScanSample.bssid,
                    sa_func.max(WirelessScanSample.frequency_mhz),
                )
                .where(
                    WirelessScanSample.bssid.in_(bssids),
                    WirelessScanSample.frequency_mhz.is_not(None),
                )
                .group_by(WirelessScanSample.bssid)
            )
        ).all()
        bssid_freq_map = {b: int(f) for b, f in freq_rows if f is not None}

    out: list[WirelessTrendSeries] = []
    for (aid, iid), rows in by_key.items():
        rows.sort(key=lambda r: r.ts_ms)
        pts = [
            WirelessTrendPoint(
                ts_ms=r.ts_ms, ssid=r.ssid, bssid=r.bssid, signal_dbm=r.signal_dbm
            )
            for r in rows
        ]
        roams: list[dict] = []
        prev_bssid: str | None = None
        for r in rows:
            if r.bssid and prev_bssid and r.bssid != prev_bssid:
                roams.append(
                    {
                        "ts_ms": r.ts_ms,
                        "from_bssid": prev_bssid,
                        "to_bssid": r.bssid,
                    }
                )
            if r.bssid:
                prev_bssid = r.bssid
        # Filter the freq map to BSSIDs this series actually saw, so the
        # payload stays tidy.
        seen_bssids = {r.bssid for r in rows if r.bssid}
        series_freqs = {
            b: f for b, f in bssid_freq_map.items() if b in seen_bssids
        }
        out.append(
            WirelessTrendSeries(
                agent_uid=uid_by_id.get(aid, ""),
                hostname=hostname_by_id.get(aid),
                iface_name=iface_name_by_id.get(iid),
                points=pts,
                roams=roams,
                bssid_frequencies=series_freqs,
            )
        )
    return out
