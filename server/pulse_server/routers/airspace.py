"""Admin CRUD for the monitored-SSIDs allowlist + query for airspace scan
samples collected by monitor-role agents.

The allowlist is small (admin adds 1–3 SSIDs). Scan samples are much larger,
so the query endpoint is bucketed + capped the same way /trends is.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import (
    AccessPoint,
    AccessPointBssid,
    Agent,
    MonitoredSsid,
    WirelessScanSample,
)
from pulse_server.db.session import get_db
from pulse_server.security.deps import require_admin

router = APIRouter(
    prefix="/v1/admin",
    tags=["admin", "airspace"],
    dependencies=[Depends(require_admin)],
)


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---- Monitored SSIDs CRUD -----------------------------------------------


class MonitoredSsidView(BaseModel):
    id: int
    ssid: str
    created_at: int


class MonitoredSsidCreate(BaseModel):
    ssid: str = Field(min_length=1, max_length=64)


@router.get("/monitored-ssids", response_model=list[MonitoredSsidView])
async def list_monitored_ssids(
    db: AsyncSession = Depends(get_db),
) -> list[MonitoredSsidView]:
    rows = (
        await db.execute(select(MonitoredSsid).order_by(MonitoredSsid.ssid))
    ).scalars().all()
    return [
        MonitoredSsidView(id=r.id, ssid=r.ssid, created_at=r.created_at)
        for r in rows
    ]


@router.post("/monitored-ssids", response_model=MonitoredSsidView, status_code=201)
async def create_monitored_ssid(
    body: MonitoredSsidCreate,
    db: AsyncSession = Depends(get_db),
) -> MonitoredSsidView:
    ssid = body.ssid.strip()
    if not ssid:
        raise HTTPException(400, "ssid must be non-empty")
    existing = (
        await db.execute(select(MonitoredSsid).where(MonitoredSsid.ssid == ssid))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, f"already monitoring '{ssid}'")
    row = MonitoredSsid(ssid=ssid, created_at=_now_ms())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return MonitoredSsidView(id=row.id, ssid=row.ssid, created_at=row.created_at)


@router.delete("/monitored-ssids/{ssid_id}", status_code=204)
async def delete_monitored_ssid(
    ssid_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> None:
    row = await db.get(MonitoredSsid, ssid_id)
    if row is None:
        raise HTTPException(404, "not found")
    await db.delete(row)
    await db.commit()


# ---- Airspace query -----------------------------------------------------


class ScanBssidPoint(BaseModel):
    ts_ms: int
    signal_dbm: int | None


class ScanBssidSeries(BaseModel):
    bssid: str
    ssid: str | None
    ap_id: int | None
    ap_name: str | None
    frequency_mhz: int | None
    """Most-recent frequency for this BSSID in the window — lets the UI show
    a 2.4/5/6 GHz band badge so dual-band APs (same SSID, two BSSIDs) are
    visually distinguishable."""
    points: list[ScanBssidPoint]


class AirspaceResponse(BaseModel):
    since_ts: int
    until_ts: int
    agent_uid: str
    hostname: str | None
    series: list[ScanBssidSeries]


@router.get("/airspace", response_model=AirspaceResponse)
async def get_airspace(
    agent_uid: str = Query(..., description="agent_uid of the monitor agent"),
    since_ts: int = Query(..., description="window start epoch ms"),
    until_ts: int = Query(..., description="window end epoch ms"),
    db: AsyncSession = Depends(get_db),
) -> AirspaceResponse:
    """Return scan samples from the given monitor agent, grouped by BSSID.
    Each BSSID annotated with the mapped AP (if any) so the UI can render
    under an AP name instead of a raw MAC."""
    if until_ts <= since_ts:
        raise HTTPException(400, "until_ts must be > since_ts")
    agent = (
        await db.execute(select(Agent).where(Agent.agent_uid == agent_uid))
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(404, "agent not found")

    rows = (
        await db.execute(
            select(WirelessScanSample)
            .where(
                WirelessScanSample.agent_id == agent.id,
                WirelessScanSample.ts_ms >= since_ts,
                WirelessScanSample.ts_ms <= until_ts,
            )
            .order_by(WirelessScanSample.ts_ms)
        )
    ).scalars().all()

    # Resolve bssid → ap_id via the junction, then ap_id → name.
    bssid_set = {r.bssid for r in rows}
    ap_map: dict[str, tuple[int, str]] = {}
    if bssid_set:
        ap_rows = (
            await db.execute(
                select(AccessPointBssid.bssid, AccessPointBssid.access_point_id)
                .where(AccessPointBssid.bssid.in_(bssid_set))
            )
        ).all()
        ap_ids = {aid for _, aid in ap_rows}
        name_rows = (
            await db.execute(
                select(AccessPoint.id, AccessPoint.name).where(
                    AccessPoint.id.in_(ap_ids)
                )
            )
        ).all() if ap_ids else []
        id_to_name = {pk: name for pk, name in name_rows}
        for bssid, ap_id in ap_rows:
            ap_map[bssid] = (ap_id, id_to_name.get(ap_id, ""))

    by_bssid: dict[str, list[WirelessScanSample]] = {}
    for r in rows:
        by_bssid.setdefault(r.bssid, []).append(r)

    series: list[ScanBssidSeries] = []
    for bssid, samples in by_bssid.items():
        ap_id, ap_name = (None, None)
        if bssid in ap_map:
            ap_id, ap_name = ap_map[bssid]
        # Use the most-recent sample's SSID — enterprise APs can advertise
        # multiple SSIDs per BSSID but the last-seen is what the UI wants to
        # label the line.
        last_ssid = next(
            (s.ssid for s in reversed(samples) if s.ssid is not None), None,
        )
        last_freq = next(
            (s.frequency_mhz for s in reversed(samples) if s.frequency_mhz is not None),
            None,
        )
        series.append(
            ScanBssidSeries(
                bssid=bssid,
                ssid=last_ssid,
                ap_id=ap_id,
                ap_name=ap_name,
                frequency_mhz=last_freq,
                points=[
                    ScanBssidPoint(ts_ms=s.ts_ms, signal_dbm=s.signal_dbm)
                    for s in samples
                ],
            )
        )
    # Sort: mapped APs (alphabetical) first, then unmapped by SSID/BSSID.
    series.sort(
        key=lambda s: (
            0 if s.ap_name else 1,
            s.ap_name or s.ssid or s.bssid,
        )
    )

    return AirspaceResponse(
        since_ts=since_ts,
        until_ts=until_ts,
        agent_uid=agent.agent_uid,
        hostname=agent.hostname,
        series=series,
    )
