"""Admin-gated debug endpoints.

Intended for operators and the smoke test. Protected by the admin bearer so they aren't
a security concern, but they let callers skip the normal scheduled cadence of rollup
and alert evaluation — useful in tests where waiting 60+ seconds for a minute boundary
is prohibitive.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.session import get_db
from pulse_server.security.deps import require_admin
from pulse_server.services import alert_engine, rollup_service

router = APIRouter(
    prefix="/v1/admin/debug",
    tags=["admin", "debug"],
    dependencies=[Depends(require_admin)],
)


class RollupResponse(BaseModel):
    buckets_rolled: int
    aggregates_written: int
    transitions: int


@router.post("/rollup-now", response_model=RollupResponse)
async def rollup_now(
    request: Request,
    db: AsyncSession = Depends(get_db),
    now_ms: int | None = Query(default=None, description="Simulated wall clock; defaults to now+60s"),
) -> RollupResponse:
    forced = now_ms or int(time.time() * 1000) + 60_000
    rsum = await rollup_service.rollup_minute(db, now_ms=forced)
    asum = await alert_engine.evaluate(db, request.app.state.settings, now_ms=forced)
    return RollupResponse(
        buckets_rolled=rsum.buckets_rolled,
        aggregates_written=rsum.aggregates_written,
        transitions=asum.transitions,
    )
