"""Enrollment endpoints.

Split into two logical groups that share this router prefix:

  agent-facing (no admin auth):
    POST /v1/enroll
    POST /v1/enroll/poll

  admin-facing (requires admin bearer):
    GET    /v1/admin/enrollment-tokens
    POST   /v1/admin/enrollment-tokens
    DELETE /v1/admin/enrollment-tokens/{id}
    GET    /v1/admin/enrollments/pending
    POST   /v1/admin/enrollments/{id}/approve
    POST   /v1/admin/enrollments/{id}/reject
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.session import get_db
from pulse_server.security.deps import require_admin
from pulse_server.services import enrollment_service as svc
from pulse_server.services import peer_service
from pulse_shared.contracts import (
    EnrollPollRequest,
    EnrollPollResponse,
    EnrollRequest,
    EnrollResponse,
)

router = APIRouter(tags=["enrollment"])


# ---------------------------------------------------------------------------
# Agent-facing
# ---------------------------------------------------------------------------


@router.post("/v1/enroll", response_model=EnrollResponse)
async def enroll(body: EnrollRequest, db: AsyncSession = Depends(get_db)) -> EnrollResponse:
    try:
        handle = await svc.submit_enrollment(
            db,
            enrollment_token=body.enrollment_token,
            hostname=body.hostname,
            reported_ip=body.reported_ip,
            caps=body.caps.model_dump(),
        )
    except svc.EnrollmentError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    return EnrollResponse(agent_uid=handle.agent_uid, pending=True)


@router.post("/v1/enroll/poll", response_model=EnrollPollResponse)
async def enroll_poll(
    body: EnrollPollRequest, db: AsyncSession = Depends(get_db)
) -> EnrollPollResponse:
    try:
        outcome = await svc.poll_enrollment(db, body.enrollment_token, body.agent_uid)
    except svc.EnrollmentError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    return EnrollPollResponse(approved=outcome.approved, agent_token=outcome.agent_token)


# ---------------------------------------------------------------------------
# Admin-facing — enrollment tokens
# ---------------------------------------------------------------------------


class NewEnrollmentTokenBody(BaseModel):
    label: str
    expires_at: int | None = None
    uses_remaining: int | None = None


class EnrollmentTokenSummary(BaseModel):
    id: int
    label: str
    created_at: int
    expires_at: int | None
    uses_remaining: int | None
    revoked: bool


class NewEnrollmentTokenResponse(EnrollmentTokenSummary):
    plaintext: str


@router.get(
    "/v1/admin/enrollment-tokens",
    response_model=list[EnrollmentTokenSummary],
    dependencies=[Depends(require_admin)],
)
async def list_enrollment_tokens(
    db: AsyncSession = Depends(get_db),
) -> list[EnrollmentTokenSummary]:
    tokens = await svc.list_enrollment_tokens(db)
    return [
        EnrollmentTokenSummary(
            id=t.id,
            label=t.label,
            created_at=t.created_at,
            expires_at=t.expires_at,
            uses_remaining=t.uses_remaining,
            revoked=t.revoked,
        )
        for t in tokens
    ]


@router.post(
    "/v1/admin/enrollment-tokens",
    response_model=NewEnrollmentTokenResponse,
    dependencies=[Depends(require_admin)],
)
async def create_enrollment_token(
    body: NewEnrollmentTokenBody, db: AsyncSession = Depends(get_db)
) -> NewEnrollmentTokenResponse:
    issued = await svc.issue_enrollment_token(
        db,
        label=body.label,
        expires_at=body.expires_at,
        uses_remaining=body.uses_remaining,
    )
    # Read back the row for the summary fields.
    tokens = await svc.list_enrollment_tokens(db)
    row = next(t for t in tokens if t.id == issued.id)
    return NewEnrollmentTokenResponse(
        id=row.id,
        label=row.label,
        created_at=row.created_at,
        expires_at=row.expires_at,
        uses_remaining=row.uses_remaining,
        revoked=row.revoked,
        plaintext=issued.plaintext,
    )


@router.delete(
    "/v1/admin/enrollment-tokens/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def revoke_enrollment_token(
    token_id: int, db: AsyncSession = Depends(get_db)
) -> None:
    ok = await svc.revoke_enrollment_token(db, token_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


# ---------------------------------------------------------------------------
# Admin-facing — pending enrollments
# ---------------------------------------------------------------------------


class PendingEnrollmentView(BaseModel):
    id: int
    agent_uid: str
    reported_hostname: str
    reported_ip: str
    caps: dict
    created_at: int
    approved: bool


class ApproveBody(BaseModel):
    poll_interval_s: int | None = None
    ping_interval_s: int | None = None


class ApproveResponse(BaseModel):
    agent_id: int
    agent_uid: str


@router.get(
    "/v1/admin/enrollments/pending",
    response_model=list[PendingEnrollmentView],
    dependencies=[Depends(require_admin)],
)
async def list_pending(db: AsyncSession = Depends(get_db)) -> list[PendingEnrollmentView]:
    rows = await svc.list_pending(db)
    return [
        PendingEnrollmentView(
            id=r.id,
            agent_uid=r.agent_uid_candidate,
            reported_hostname=r.reported_hostname,
            reported_ip=r.reported_ip,
            caps=r.caps if isinstance(r.caps, dict) else {},
            created_at=r.created_at,
            approved=r.approved,
        )
        for r in rows
    ]


@router.post(
    "/v1/admin/enrollments/{pending_id}/approve",
    response_model=ApproveResponse,
    dependencies=[Depends(require_admin)],
)
async def approve_pending(
    pending_id: int,
    body: ApproveBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ApproveResponse:
    settings = request.app.state.settings
    try:
        issued = await svc.approve_pending(
            db,
            pending_id,
            poll_interval_s=body.poll_interval_s or settings.default_poll_interval_s,
            ping_interval_s=body.ping_interval_s or settings.default_ping_interval_s,
        )
    except svc.EnrollmentError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    # Refresh the full-mesh so every active agent discovers the new peer on next poll.
    await peer_service.recompute_full_mesh(db)
    return ApproveResponse(agent_id=issued.agent_id, agent_uid=issued.agent_uid)


@router.post(
    "/v1/admin/enrollments/{pending_id}/reject",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def reject_pending(pending_id: int, db: AsyncSession = Depends(get_db)) -> None:
    try:
        ok = await svc.reject_pending(db, pending_id)
    except svc.EnrollmentError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
