"""Tools API — currently just the Attenuator.

Exposed under /v1/admin/tools/. Each tool owns its own sub-prefix so the
router stays tidy as we add more. Attenuator paths:

    GET    /attenuator/ruckus-aps           — live list from Ruckus One
    PATCH  /attenuator/aps/{id}              — set Pulse AP's ruckus_serial
    GET    /attenuator/presets               — list saved presets
    POST   /attenuator/presets               — create
    PATCH  /attenuator/presets/{id}          — update
    DELETE /attenuator/presets/{id}          — delete
    GET    /attenuator/runs                  — run history (latest first)
    GET    /attenuator/runs/{id}             — single run incl. steps
    POST   /attenuator/runs                  — start a run
    POST   /attenuator/runs/{id}/cancel      — cancel + restore
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.config import Settings
from pulse_server.db.models import (
    AccessPoint,
    AttenuatorPreset,
    ToolRun,
    ToolRunStep,
)
from pulse_server.db.session import get_db
from pulse_server.security.deps import require_admin
from pulse_server.services import attenuator_service
from pulse_server.services.ruckus_client import (
    TX_POWER_VALUES,
    build_client,
    is_valid_tx_power,
)

router = APIRouter(
    prefix="/v1/admin/tools",
    tags=["admin", "tools"],
    dependencies=[Depends(require_admin)],
)


def _settings(request: Request) -> Settings:
    return request.app.state.settings


# --- Models ---------------------------------------------------------------

class RuckusApView(BaseModel):
    serial: str
    name: str | None
    model: str | None
    status: str | None
    venue_id: str | None
    mapped_ap_id: int | None  # which Pulse AccessPoint currently claims this serial


class SetRuckusSerialBody(BaseModel):
    ruckus_serial: str | None = Field(default=None, max_length=32)


class Participant(BaseModel):
    ap_id: int
    direction: str  # "drop" | "raise"
    target_value: str

    @field_validator("direction")
    @classmethod
    def _dir(cls, v: str) -> str:
        if v not in ("drop", "raise"):
            raise ValueError("direction must be drop or raise")
        return v

    @field_validator("target_value")
    @classmethod
    def _tv(cls, v: str) -> str:
        if v not in TX_POWER_VALUES and v != "Auto":
            raise ValueError(f"target_value must be one of {TX_POWER_VALUES} or 'Auto'")
        return v


class AttenuatorPresetBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    radio: str = "5g"
    step_size_db: int = Field(default=3, ge=1, le=23)
    step_interval_s: int = Field(default=10, ge=1, le=120)
    participants: list[Participant]
    boost_participants: bool = True
    instant: bool = False
    """If true, skip the ramp and jump each participant straight to its
    target txPower; do NOT restore on successful completion."""

    @field_validator("radio")
    @classmethod
    def _radio(cls, v: str) -> str:
        if v not in ("5g", "24g", "6g"):
            raise ValueError("radio must be 5g, 24g, or 6g")
        return v


class AttenuatorPresetCreate(AttenuatorPresetBase):
    pass


class AttenuatorPresetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    radio: str | None = None
    step_size_db: int | None = Field(default=None, ge=1, le=23)
    step_interval_s: int | None = Field(default=None, ge=1, le=120)
    participants: list[Participant] | None = None
    boost_participants: bool | None = None
    instant: bool | None = None


class AttenuatorPresetView(BaseModel):
    id: int
    name: str
    radio: str
    step_size_db: int
    step_interval_s: int
    participants: list[Participant]
    boost_participants: bool
    instant: bool
    created_at: int
    updated_at: int


class StartRunBody(BaseModel):
    preset_id: int | None = None
    # Optional inline config — if preset_id is supplied, these override.
    name: str | None = None
    radio: str | None = None
    step_size_db: int | None = None
    step_interval_s: int | None = None
    participants: list[Participant] | None = None
    boost_participants: bool | None = None
    instant: bool | None = None


class ToolRunStepView(BaseModel):
    ts_ms: int
    ap_serial: str | None
    action: dict
    success: bool
    ruckus_request_id: str | None
    error: str | None


class ToolRunView(BaseModel):
    id: int
    tool_type: str
    preset_id: int | None
    state: str
    config: dict
    started_at: int
    ends_at: int
    finalized_at: int | None
    error: str | None


class ToolRunDetailView(ToolRunView):
    revert_state: dict | None
    steps: list[ToolRunStepView]


def _preset_view(r: AttenuatorPreset) -> AttenuatorPresetView:
    return AttenuatorPresetView(
        id=r.id,
        name=r.name,
        radio=r.radio,
        step_size_db=r.step_size_db,
        step_interval_s=r.step_interval_s,
        participants=[Participant(**p) for p in (r.participants or [])],
        boost_participants=r.boost_participants,
        instant=bool(r.instant),
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def _run_view(r: ToolRun) -> ToolRunView:
    return ToolRunView(
        id=r.id,
        tool_type=r.tool_type,
        preset_id=r.preset_id,
        state=r.state,
        config=r.config,
        started_at=r.started_at,
        ends_at=r.ends_at,
        finalized_at=r.finalized_at,
        error=r.error,
    )


# --- AP sync + mapping --------------------------------------------------

@router.get("/attenuator/ruckus-aps", response_model=list[RuckusApView])
async def list_ruckus_aps(
    request: Request, db: AsyncSession = Depends(get_db),
) -> list[RuckusApView]:
    settings = _settings(request)
    if not settings.ruckus_configured:
        raise HTTPException(
            status_code=412,
            detail="Ruckus One credentials not configured in .env",
        )
    client = build_client(settings)
    try:
        aps = await client.list_aps()
    finally:
        await client.aclose()

    pulse_aps = (await db.execute(select(AccessPoint))).scalars().all()
    owner = {
        a.ruckus_serial: a.id for a in pulse_aps if a.ruckus_serial
    }
    out: list[RuckusApView] = []
    for a in aps:
        serial = a.get("serialNumber") or a.get("serial") or ""
        if not serial:
            continue
        out.append(
            RuckusApView(
                serial=serial,
                name=a.get("name") or a.get("apName"),
                model=a.get("model") or a.get("apModel"),
                status=a.get("status") or a.get("state"),
                venue_id=a.get("venueId"),
                mapped_ap_id=owner.get(serial),
            )
        )
    # Show unmapped first, then mapped, then by name for stable ordering.
    out.sort(key=lambda x: (x.mapped_ap_id is not None, x.name or x.serial))
    return out


@router.patch("/attenuator/aps/{ap_id}", status_code=204)
async def set_ruckus_serial(
    body: SetRuckusSerialBody,
    ap_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> None:
    ap = await db.get(AccessPoint, ap_id)
    if ap is None:
        raise HTTPException(status_code=404, detail="access point not found")
    # Enforce uniqueness — unmap any other AP currently claiming this serial.
    if body.ruckus_serial:
        clash = (
            await db.execute(
                select(AccessPoint).where(
                    AccessPoint.ruckus_serial == body.ruckus_serial,
                    AccessPoint.id != ap_id,
                )
            )
        ).scalars().all()
        for other in clash:
            other.ruckus_serial = None
    ap.ruckus_serial = body.ruckus_serial
    ap.updated_at = int(time.time() * 1000)
    await db.commit()


# --- Presets ------------------------------------------------------------

@router.get("/attenuator/presets", response_model=list[AttenuatorPresetView])
async def list_presets(
    db: AsyncSession = Depends(get_db),
) -> list[AttenuatorPresetView]:
    rows = (
        await db.execute(
            select(AttenuatorPreset).order_by(AttenuatorPreset.name)
        )
    ).scalars().all()
    return [_preset_view(r) for r in rows]


@router.post(
    "/attenuator/presets", response_model=AttenuatorPresetView, status_code=201,
)
async def create_preset(
    body: AttenuatorPresetCreate, db: AsyncSession = Depends(get_db),
) -> AttenuatorPresetView:
    now = int(time.time() * 1000)
    row = AttenuatorPreset(
        name=body.name.strip(),
        radio=body.radio,
        step_size_db=body.step_size_db,
        step_interval_s=body.step_interval_s,
        participants=[p.model_dump() for p in body.participants],
        boost_participants=body.boost_participants,
        instant=body.instant,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _preset_view(row)


@router.patch("/attenuator/presets/{pid}", response_model=AttenuatorPresetView)
async def update_preset(
    body: AttenuatorPresetUpdate,
    pid: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> AttenuatorPresetView:
    row = await db.get(AttenuatorPreset, pid)
    if row is None:
        raise HTTPException(status_code=404, detail="preset not found")
    if body.name is not None:
        row.name = body.name.strip()
    if body.radio is not None:
        if body.radio not in ("5g", "24g", "6g"):
            raise HTTPException(status_code=400, detail="invalid radio")
        row.radio = body.radio
    if body.step_size_db is not None:
        row.step_size_db = body.step_size_db
    if body.step_interval_s is not None:
        row.step_interval_s = body.step_interval_s
    if body.participants is not None:
        row.participants = [p.model_dump() for p in body.participants]
    if body.boost_participants is not None:
        row.boost_participants = body.boost_participants
    if body.instant is not None:
        row.instant = body.instant
    row.updated_at = int(time.time() * 1000)
    await db.commit()
    await db.refresh(row)
    return _preset_view(row)


@router.delete("/attenuator/presets/{pid}", status_code=204)
async def delete_preset(
    pid: int = Path(..., ge=1), db: AsyncSession = Depends(get_db),
) -> None:
    row = await db.get(AttenuatorPreset, pid)
    if row is None:
        raise HTTPException(status_code=404, detail="preset not found")
    await db.delete(row)
    await db.commit()


# --- Runs --------------------------------------------------------------

@router.get("/attenuator/runs", response_model=list[ToolRunView])
async def list_runs(
    db: AsyncSession = Depends(get_db),
) -> list[ToolRunView]:
    rows = (
        await db.execute(
            select(ToolRun)
            .where(ToolRun.tool_type == attenuator_service.TOOL_TYPE)
            .order_by(desc(ToolRun.started_at))
            .limit(50)
        )
    ).scalars().all()
    return [_run_view(r) for r in rows]


@router.get("/attenuator/runs/{rid}", response_model=ToolRunDetailView)
async def get_run(
    rid: int = Path(..., ge=1), db: AsyncSession = Depends(get_db),
) -> ToolRunDetailView:
    r = await db.get(ToolRun, rid)
    if r is None or r.tool_type != attenuator_service.TOOL_TYPE:
        raise HTTPException(status_code=404, detail="run not found")
    steps = (
        await db.execute(
            select(ToolRunStep)
            .where(ToolRunStep.run_id == rid)
            .order_by(ToolRunStep.ts_ms)
        )
    ).scalars().all()
    return ToolRunDetailView(
        id=r.id,
        tool_type=r.tool_type,
        preset_id=r.preset_id,
        state=r.state,
        config=r.config,
        started_at=r.started_at,
        ends_at=r.ends_at,
        finalized_at=r.finalized_at,
        error=r.error,
        revert_state=r.revert_state,
        steps=[
            ToolRunStepView(
                ts_ms=s.ts_ms,
                ap_serial=s.ap_serial,
                action=s.action,
                success=s.success,
                ruckus_request_id=s.ruckus_request_id,
                error=s.error,
            )
            for s in steps
        ],
    )


@router.post("/attenuator/runs", response_model=ToolRunView, status_code=201)
async def start_run(
    body: StartRunBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ToolRunView:
    settings = _settings(request)
    if not settings.ruckus_configured:
        raise HTTPException(
            status_code=412,
            detail="Ruckus One credentials not configured in .env",
        )
    sessionmaker = request.app.state.sessionmaker

    # Merge body with preset (body fields override preset fields).
    preset: AttenuatorPreset | None = None
    if body.preset_id is not None:
        preset = await db.get(AttenuatorPreset, body.preset_id)
        if preset is None:
            raise HTTPException(status_code=404, detail="preset not found")

    def pick(field: str):
        val = getattr(body, field, None)
        if val is not None:
            return val
        if preset is not None:
            return getattr(preset, field)
        return None

    participants_raw = body.participants
    if participants_raw is None and preset is not None:
        participants_raw = [Participant(**p) for p in (preset.participants or [])]
    if not participants_raw:
        raise HTTPException(status_code=400, detail="participants required")

    try:
        run = await attenuator_service.start_run(
            db,
            settings,
            sessionmaker,
            preset_id=body.preset_id,
            name=(body.name or (preset.name if preset else "attenuator run")),
            radio=pick("radio") or "5g",
            step_size_db=pick("step_size_db") or 3,
            step_interval_s=pick("step_interval_s") or 10,
            participants=[p.model_dump() for p in participants_raw],
            boost_participants=(
                body.boost_participants
                if body.boost_participants is not None
                else (preset.boost_participants if preset else True)
            ),
            instant=(
                body.instant
                if body.instant is not None
                else bool(preset.instant) if preset else False
            ),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _run_view(run)


@router.post("/attenuator/runs/{rid}/cancel", response_model=ToolRunView)
async def cancel_run(
    rid: int = Path(..., ge=1),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
) -> ToolRunView:
    settings = _settings(request)
    sessionmaker = request.app.state.sessionmaker
    row = await db.get(ToolRun, rid)
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    if row.state != "running":
        return _run_view(row)
    await attenuator_service.cancel_run(sessionmaker, settings, rid)
    await db.refresh(row)
    return _run_view(row)
