"""Admin CRUD for passive targets (ping-only endpoints with no agent)."""

from __future__ import annotations

import ipaddress
import time

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import PassiveTarget
from pulse_server.db.session import get_db
from pulse_server.repo import meta_repo
from pulse_server.security.deps import require_admin

router = APIRouter(
    prefix="/v1/admin/passive-targets",
    tags=["admin", "passive-targets"],
    dependencies=[Depends(require_admin)],
)


def _validate_ip(value: str) -> str:
    v = value.strip()
    try:
        ipaddress.ip_address(v)
    except ValueError as e:
        raise ValueError(f"invalid ip: {v}") from e
    return v


class PassiveTargetView(BaseModel):
    id: int
    name: str
    ip: str
    notes: str | None
    enabled: bool
    created_at: int
    updated_at: int


class PassiveTargetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    ip: str
    notes: str | None = Field(default=None, max_length=512)
    enabled: bool = True

    @field_validator("ip")
    @classmethod
    def _ip(cls, v: str) -> str:
        return _validate_ip(v)


class PassiveTargetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    ip: str | None = None
    notes: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None

    @field_validator("ip")
    @classmethod
    def _ip(cls, v: str | None) -> str | None:
        return None if v is None else _validate_ip(v)


def _view(r: PassiveTarget) -> PassiveTargetView:
    return PassiveTargetView(
        id=r.id,
        name=r.name,
        ip=r.ip,
        notes=r.notes,
        enabled=r.enabled,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


@router.get("", response_model=list[PassiveTargetView])
async def list_passive_targets(
    db: AsyncSession = Depends(get_db),
) -> list[PassiveTargetView]:
    rows = (
        await db.execute(
            select(PassiveTarget).order_by(desc(PassiveTarget.enabled), PassiveTarget.name)
        )
    ).scalars().all()
    return [_view(r) for r in rows]


@router.post("", response_model=PassiveTargetView, status_code=201)
async def create_passive_target(
    body: PassiveTargetCreate,
    db: AsyncSession = Depends(get_db),
) -> PassiveTargetView:
    existing = (
        await db.execute(select(PassiveTarget).where(PassiveTarget.ip == body.ip))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="ip already registered")
    now = int(time.time() * 1000)
    row = PassiveTarget(
        name=body.name.strip(),
        ip=body.ip,
        notes=(body.notes or None),
        enabled=body.enabled,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.flush()
    if row.enabled:
        await meta_repo.bump(db, meta_repo.PEER_ASSIGNMENTS_VERSION)
    await db.commit()
    await db.refresh(row)
    return _view(row)


@router.patch("/{tid}", response_model=PassiveTargetView)
async def update_passive_target(
    body: PassiveTargetUpdate,
    tid: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> PassiveTargetView:
    row = await db.get(PassiveTarget, tid)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    bump = False
    if body.ip is not None and body.ip != row.ip:
        clash = (
            await db.execute(
                select(PassiveTarget).where(
                    PassiveTarget.ip == body.ip, PassiveTarget.id != tid,
                )
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise HTTPException(status_code=409, detail="ip already registered")
        row.ip = body.ip
        bump = True
    if body.name is not None:
        row.name = body.name.strip()
    if body.notes is not None:
        row.notes = body.notes or None
    if body.enabled is not None and body.enabled != row.enabled:
        row.enabled = body.enabled
        bump = True
    row.updated_at = int(time.time() * 1000)
    if bump:
        await meta_repo.bump(db, meta_repo.PEER_ASSIGNMENTS_VERSION)
    await db.commit()
    await db.refresh(row)
    return _view(row)


@router.delete("/{tid}", status_code=204)
async def delete_passive_target(
    tid: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> None:
    row = await db.get(PassiveTarget, tid)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    was_enabled = row.enabled
    await db.delete(row)
    if was_enabled:
        await meta_repo.bump(db, meta_repo.PEER_ASSIGNMENTS_VERSION)
    await db.commit()
