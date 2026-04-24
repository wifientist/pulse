"""Admin CRUD for the BSSID → AP-name reference list.

Each AccessPoint owns 1..N BSSIDs. The frontend resolves a reported BSSID to
an AP name by exact match against any of these junction rows — no prefix/
wildcard logic. Admin assigns BSSIDs explicitly, typically by picking from the
"unassigned" list surfaced via GET /unassigned-bssids.
"""

from __future__ import annotations

import re
import time

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import (
    AccessPoint,
    AccessPointBssid,
    WirelessSample,
    WirelessScanSample,
)
from pulse_server.db.session import get_db
from pulse_server.security.deps import require_admin

router = APIRouter(
    prefix="/v1/admin/access-points",
    tags=["admin", "access-points"],
    dependencies=[Depends(require_admin)],
)

_BSSID_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def _normalize_bssid(value: str) -> str:
    v = value.strip().lower().replace("-", ":")
    if not _BSSID_RE.match(v):
        raise ValueError("bssid must be aa:bb:cc:dd:ee:ff")
    return v


class AccessPointView(BaseModel):
    id: int
    name: str
    bssids: list[str]
    location: str | None
    notes: str | None
    ruckus_serial: str | None
    created_at: int
    updated_at: int


class AccessPointCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    bssids: list[str] = Field(default_factory=list)
    location: str | None = Field(default=None, max_length=128)
    notes: str | None = Field(default=None, max_length=512)

    @field_validator("bssids")
    @classmethod
    def _norm_bssids(cls, v: list[str]) -> list[str]:
        return [_normalize_bssid(b) for b in v]


class AccessPointUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    location: str | None = Field(default=None, max_length=128)
    notes: str | None = Field(default=None, max_length=512)
    # Empty string treated as "clear the mapping"; omit to leave unchanged.
    ruckus_serial: str | None = Field(default=None, max_length=32)


class BssidAddBody(BaseModel):
    bssid: str

    @field_validator("bssid")
    @classmethod
    def _norm(cls, v: str) -> str:
        return _normalize_bssid(v)


class UnassignedBssidView(BaseModel):
    bssid: str
    last_seen_ms: int
    last_ssid: str | None
    agent_uids: list[str]
    frequency_mhz: int | None = None
    """Populated when the BSSID was observed by a monitor-role agent scan.
    Client-connected BSSIDs have no frequency in wireless_samples so this is
    null for them; UI renders "2.4 GHz" / "5 GHz" / "6 GHz" badges from this."""


async def _bssids_for(db: AsyncSession, ap_id: int) -> list[str]:
    rows = (
        await db.execute(
            select(AccessPointBssid.bssid)
            .where(AccessPointBssid.access_point_id == ap_id)
            .order_by(AccessPointBssid.bssid)
        )
    ).scalars().all()
    return list(rows)


async def _view(db: AsyncSession, r: AccessPoint) -> AccessPointView:
    bssids = await _bssids_for(db, r.id)
    return AccessPointView(
        id=r.id,
        name=r.name,
        bssids=bssids,
        location=r.location,
        notes=r.notes,
        ruckus_serial=r.ruckus_serial,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


@router.get("", response_model=list[AccessPointView])
async def list_access_points(
    db: AsyncSession = Depends(get_db),
) -> list[AccessPointView]:
    rows = (
        await db.execute(select(AccessPoint).order_by(AccessPoint.name))
    ).scalars().all()
    return [await _view(db, r) for r in rows]


@router.get("/unassigned-bssids", response_model=list[UnassignedBssidView])
async def list_unassigned_bssids(
    db: AsyncSession = Depends(get_db),
) -> list[UnassignedBssidView]:
    """BSSIDs seen in the retention window (either by a client connecting
    — `wireless_samples` — or by a monitor-agent airspace scan —
    `wireless_scan_samples`) that aren't currently bound to any AP. Latest
    SSID + last-seen ms + observing agents are included so the admin has
    context when deciding which AP to attach."""
    assigned = (
        await db.execute(select(AccessPointBssid.bssid))
    ).scalars().all()
    assigned_set = set(assigned)

    # Merge both "seen" sources: client connections and airspace scans. Each
    # contributes (bssid, max_ts, observing_agent_ids, latest_ssid); we pick
    # the newer timestamp + union the agent sets per BSSID.
    from pulse_server.db.models import Agent

    merged: dict[
        str, dict[str, object]
    ] = {}

    async def _ingest_source(
        bssid_col, ts_col, ssid_col, agent_col, freq_col=None,
    ) -> None:
        # MAX(ts) per bssid for last_seen, plus a sweep of distinct agent_ids
        # and the most-recent ssid. `freq_col` is only set for the scan table
        # (client-connection samples don't record frequency).
        maxes = (
            await db.execute(
                select(bssid_col, func.max(ts_col))
                .where(bssid_col.is_not(None))
                .group_by(bssid_col)
            )
        ).all()
        for bssid, last_ts in maxes:
            if not bssid or bssid in assigned_set:
                continue
            entry = merged.setdefault(
                bssid,
                {
                    "last_seen_ms": 0,
                    "last_ssid": None,
                    "agent_ids": set(),
                    "frequency_mhz": None,
                },
            )
            if int(last_ts) > int(entry["last_seen_ms"]):  # type: ignore[arg-type]
                entry["last_seen_ms"] = int(last_ts)
                # Pick up SSID (+ frequency, when available) from the latest
                # row for this bssid.
                cols = [ssid_col] + ([freq_col] if freq_col is not None else [])
                latest = (
                    await db.execute(
                        select(*cols)
                        .where(bssid_col == bssid, ts_col == last_ts)
                        .limit(1)
                    )
                ).first()
                if latest:
                    if latest[0]:
                        entry["last_ssid"] = latest[0]
                    if freq_col is not None and len(latest) > 1 and latest[1]:
                        entry["frequency_mhz"] = int(latest[1])
            ag_ids = (
                await db.execute(
                    select(agent_col)
                    .where(bssid_col == bssid)
                    .group_by(agent_col)
                )
            ).scalars().all()
            entry["agent_ids"].update(int(a) for a in ag_ids)  # type: ignore[union-attr]

    await _ingest_source(
        WirelessSample.bssid,
        WirelessSample.ts_ms,
        WirelessSample.ssid,
        WirelessSample.agent_id,
    )
    await _ingest_source(
        WirelessScanSample.bssid,
        WirelessScanSample.ts_ms,
        WirelessScanSample.ssid,
        WirelessScanSample.agent_id,
        WirelessScanSample.frequency_mhz,
    )

    # Resolve agent_ids → uids in one sweep across every bssid.
    all_ids: set[int] = set()
    for entry in merged.values():
        all_ids |= entry["agent_ids"]  # type: ignore[operator]
    id_to_uid: dict[int, str] = {}
    if all_ids:
        uid_rows = (
            await db.execute(
                select(Agent.id, Agent.agent_uid).where(Agent.id.in_(list(all_ids)))
            )
        ).all()
        id_to_uid = {int(pk): uid for pk, uid in uid_rows}

    out = [
        UnassignedBssidView(
            bssid=bssid,
            last_seen_ms=int(entry["last_seen_ms"]),  # type: ignore[arg-type]
            last_ssid=entry["last_ssid"],  # type: ignore[arg-type]
            agent_uids=sorted(
                id_to_uid[aid]
                for aid in entry["agent_ids"]  # type: ignore[union-attr]
                if aid in id_to_uid
            ),
            frequency_mhz=entry["frequency_mhz"],  # type: ignore[arg-type]
        )
        for bssid, entry in merged.items()
    ]
    # Newest-first so the admin sees what's currently on the air at the top.
    out.sort(key=lambda x: x.last_seen_ms, reverse=True)
    return out


@router.post("", response_model=AccessPointView, status_code=201)
async def create_access_point(
    body: AccessPointCreate,
    db: AsyncSession = Depends(get_db),
) -> AccessPointView:
    # Reject any BSSID already bound elsewhere to preserve the junction
    # UNIQUE constraint with a clean 409 instead of a SQL error.
    if body.bssids:
        clash = (
            await db.execute(
                select(AccessPointBssid.bssid).where(
                    AccessPointBssid.bssid.in_(body.bssids)
                )
            )
        ).scalars().all()
        if clash:
            raise HTTPException(
                status_code=409,
                detail=f"bssid(s) already mapped: {sorted(set(clash))}",
            )
    now = int(time.time() * 1000)
    row = AccessPoint(
        name=body.name.strip(),
        location=(body.location or None),
        notes=(body.notes or None),
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.flush()
    for b in body.bssids:
        db.add(
            AccessPointBssid(access_point_id=row.id, bssid=b, created_at=now)
        )
    await db.commit()
    await db.refresh(row)
    return await _view(db, row)


@router.patch("/{ap_id}", response_model=AccessPointView)
async def update_access_point(
    body: AccessPointUpdate,
    ap_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> AccessPointView:
    row = await db.get(AccessPoint, ap_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    if body.name is not None:
        row.name = body.name.strip()
    if body.location is not None:
        row.location = body.location or None
    if body.notes is not None:
        row.notes = body.notes or None
    if body.ruckus_serial is not None:
        new_serial = body.ruckus_serial.strip() or None
        # Uniqueness: steal the serial from any other AP currently claiming it,
        # same semantics as the attenuator's mapping endpoint.
        if new_serial and new_serial != row.ruckus_serial:
            clash = (
                await db.execute(
                    select(AccessPoint).where(
                        AccessPoint.ruckus_serial == new_serial,
                        AccessPoint.id != ap_id,
                    )
                )
            ).scalars().all()
            for other in clash:
                other.ruckus_serial = None
        row.ruckus_serial = new_serial
    row.updated_at = int(time.time() * 1000)
    await db.commit()
    await db.refresh(row)
    return await _view(db, row)


@router.post("/{ap_id}/bssids", response_model=AccessPointView)
async def add_bssid(
    body: BssidAddBody,
    ap_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> AccessPointView:
    row = await db.get(AccessPoint, ap_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    clash = (
        await db.execute(
            select(AccessPointBssid).where(AccessPointBssid.bssid == body.bssid)
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(
            status_code=409,
            detail=f"bssid {body.bssid} already mapped to AP #{clash.access_point_id}",
        )
    db.add(
        AccessPointBssid(
            access_point_id=ap_id,
            bssid=body.bssid,
            created_at=int(time.time() * 1000),
        )
    )
    row.updated_at = int(time.time() * 1000)
    await db.commit()
    await db.refresh(row)
    return await _view(db, row)


@router.delete("/{ap_id}/bssids/{bssid}", response_model=AccessPointView)
async def remove_bssid(
    ap_id: int = Path(..., ge=1),
    bssid: str = Path(...),
    db: AsyncSession = Depends(get_db),
) -> AccessPointView:
    row = await db.get(AccessPoint, ap_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    normalized = _normalize_bssid(bssid)
    link = (
        await db.execute(
            select(AccessPointBssid).where(
                AccessPointBssid.access_point_id == ap_id,
                AccessPointBssid.bssid == normalized,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=404, detail="bssid not on this AP")
    await db.delete(link)
    row.updated_at = int(time.time() * 1000)
    await db.commit()
    await db.refresh(row)
    return await _view(db, row)


@router.delete("/{ap_id}", status_code=204)
async def delete_access_point(
    ap_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> None:
    row = await db.get(AccessPoint, ap_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    await db.delete(row)
    await db.commit()
