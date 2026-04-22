"""Enrollment flow.

Pipeline:
    1. Admin POSTs /v1/admin/enrollment-tokens → server mints a pre-shared token (plaintext
       returned once, hashed at rest).
    2. Agent POSTs /v1/enroll with that token + hostname/ip/caps → server validates, creates
       a PendingEnrollment row with a freshly-minted agent_uid, returns `{agent_uid}`.
    3. Admin approves via POST /v1/admin/enrollments/{id}/approve → server creates an Agent
       row, mints an agent-specific bearer token, stashes its hash on the pending row.
    4. Agent polls POST /v1/enroll/poll → once the pending row has `handoff_token`
       set, server returns the plaintext token once and clears it.

Token verification against the enrollment_tokens table is an O(N) argon2.verify scan. At
<25 active enrollment tokens this is trivial.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import Agent, EnrollmentToken, PendingEnrollment
from pulse_server.security.tokens import hash_token, new_token, verify_token
from pulse_shared.enums import AgentState


class EnrollmentError(Exception):
    """Raised for any enrollment-flow business-rule violation."""


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class IssuedEnrollmentToken:
    id: int
    plaintext: str
    label: str


@dataclass(frozen=True)
class PendingHandle:
    agent_uid: str


@dataclass(frozen=True)
class PollOutcome:
    approved: bool
    agent_token: str | None


@dataclass(frozen=True)
class IssuedAgentToken:
    agent_id: int
    agent_uid: str
    plaintext: str


async def issue_enrollment_token(
    db: AsyncSession,
    label: str,
    expires_at: int | None = None,
    uses_remaining: int | None = None,
) -> IssuedEnrollmentToken:
    plaintext = new_token()
    row = EnrollmentToken(
        token_hash=hash_token(plaintext),
        label=label,
        created_at=now_ms(),
        expires_at=expires_at,
        uses_remaining=uses_remaining,
        revoked=False,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return IssuedEnrollmentToken(id=row.id, plaintext=plaintext, label=row.label)


async def revoke_enrollment_token(db: AsyncSession, token_id: int) -> bool:
    row = await db.get(EnrollmentToken, token_id)
    if row is None:
        return False
    row.revoked = True
    await db.commit()
    return True


async def _find_enrollment_token(db: AsyncSession, plaintext: str) -> EnrollmentToken | None:
    candidates = (await db.execute(select(EnrollmentToken))).scalars().all()
    now = now_ms()
    for row in candidates:
        if row.revoked:
            continue
        if row.expires_at is not None and row.expires_at <= now:
            continue
        if row.uses_remaining is not None and row.uses_remaining <= 0:
            continue
        if verify_token(plaintext, row.token_hash):
            return row
    return None


async def submit_enrollment(
    db: AsyncSession,
    enrollment_token: str,
    hostname: str,
    reported_ip: str,
    caps: dict[str, Any],
) -> PendingHandle:
    token = await _find_enrollment_token(db, enrollment_token)
    if token is None:
        raise EnrollmentError("invalid or expired enrollment token")

    agent_uid = str(uuid.uuid4())
    pending = PendingEnrollment(
        agent_uid_candidate=agent_uid,
        enrollment_token_id=token.id,
        reported_hostname=hostname,
        reported_ip=reported_ip,
        caps=caps,
        created_at=now_ms(),
        approved=False,
        handoff_token=None,
    )
    db.add(pending)

    if token.uses_remaining is not None:
        token.uses_remaining -= 1

    await db.commit()
    return PendingHandle(agent_uid=agent_uid)


async def list_enrollment_tokens(db: AsyncSession) -> list[EnrollmentToken]:
    return list((await db.execute(select(EnrollmentToken))).scalars().all())


async def list_pending(db: AsyncSession, include_approved: bool = False) -> list[PendingEnrollment]:
    stmt = select(PendingEnrollment)
    if not include_approved:
        stmt = stmt.where(PendingEnrollment.approved.is_(False))
    return list((await db.execute(stmt)).scalars().all())


async def approve_pending(
    db: AsyncSession,
    pending_id: int,
    poll_interval_s: int,
    ping_interval_s: int,
) -> IssuedAgentToken:
    pending = await db.get(PendingEnrollment, pending_id)
    if pending is None:
        raise EnrollmentError("pending enrollment not found")
    if pending.approved:
        raise EnrollmentError("pending enrollment already approved")

    plaintext = new_token()
    token_hash = hash_token(plaintext)

    agent = Agent(
        agent_uid=pending.agent_uid_candidate,
        hostname=pending.reported_hostname,
        os=pending.caps.get("os", "unknown") if isinstance(pending.caps, dict) else "unknown",
        platform_caps=pending.caps if isinstance(pending.caps, dict) else {},
        primary_ip=pending.reported_ip,
        cidr=None,
        token_hash=token_hash,
        state=AgentState.ACTIVE.value,
        poll_interval_s=poll_interval_s,
        ping_interval_s=ping_interval_s,
        created_at=pending.created_at,
        approved_at=now_ms(),
        last_poll_at=None,
        agent_version=pending.caps.get("agent_version")
        if isinstance(pending.caps, dict)
        else None,
    )
    db.add(agent)

    pending.approved = True
    pending.handoff_token = plaintext  # stored briefly for single handoff

    await db.commit()
    await db.refresh(agent)
    return IssuedAgentToken(agent_id=agent.id, agent_uid=agent.agent_uid, plaintext=plaintext)


async def reject_pending(db: AsyncSession, pending_id: int) -> bool:
    pending = await db.get(PendingEnrollment, pending_id)
    if pending is None:
        return False
    if pending.approved:
        raise EnrollmentError("cannot reject an already-approved pending enrollment")
    await db.delete(pending)
    await db.commit()
    return True


async def poll_enrollment(
    db: AsyncSession, enrollment_token: str, agent_uid: str
) -> PollOutcome:
    """Single-read-single-write poll: returns approved=False until admin approves,
    then returns the plaintext agent token exactly once and clears it from the row."""
    pending = (
        await db.execute(
            select(PendingEnrollment).where(PendingEnrollment.agent_uid_candidate == agent_uid)
        )
    ).scalar_one_or_none()
    if pending is None:
        raise EnrollmentError("unknown agent_uid")

    token = await db.get(EnrollmentToken, pending.enrollment_token_id)
    if token is None or not verify_token(enrollment_token, token.token_hash):
        raise EnrollmentError("enrollment token mismatch")

    if not pending.approved:
        return PollOutcome(approved=False, agent_token=None)

    if pending.handoff_token is None:
        raise EnrollmentError("token already retrieved")

    plaintext = pending.handoff_token
    pending.handoff_token = None
    await db.commit()
    return PollOutcome(approved=True, agent_token=plaintext)
