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

from pulse_server.db.models import AccessPoint, AccessPointBssid, WirelessSample
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
    """BSSIDs observed in wireless_samples (retention window) that aren't
    currently bound to any AP. Latest SSID + last-seen ms + reporting agents
    are included so the admin has context when deciding which AP to attach."""
    assigned = (
        await db.execute(select(AccessPointBssid.bssid))
    ).scalars().all()
    assigned_set = set(assigned)

    # Latest row per BSSID (group-by MAX(ts)).
    latest_rows = (
        await db.execute(
            select(
                WirelessSample.bssid,
                func.max(WirelessSample.ts_ms).label("last_seen_ms"),
            )
            .where(WirelessSample.bssid.is_not(None))
            .group_by(WirelessSample.bssid)
        )
    ).all()
    out: list[UnassignedBssidView] = []
    for row in latest_rows:
        bssid = row.bssid
        if not bssid or bssid in assigned_set:
            continue
        # Most-recent full sample for SSID + reporting agent list.
        last = (
            await db.execute(
                select(WirelessSample)
                .where(WirelessSample.bssid == bssid)
                .order_by(WirelessSample.ts_ms.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        agent_ids = (
            await db.execute(
                select(WirelessSample.agent_id)
                .where(WirelessSample.bssid == bssid)
                .group_by(WirelessSample.agent_id)
            )
        ).scalars().all()
        uids: list[str] = []
        if agent_ids:
            from pulse_server.db.models import Agent
            uid_rows = (
                await db.execute(
                    select(Agent.agent_uid).where(Agent.id.in_(list(agent_ids)))
                )
            ).scalars().all()
            uids = list(uid_rows)
        out.append(
            UnassignedBssidView(
                bssid=bssid,
                last_seen_ms=int(row.last_seen_ms),
                last_ssid=last.ssid if last else None,
                agent_uids=uids,
            )
        )
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
