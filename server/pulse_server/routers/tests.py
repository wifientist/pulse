from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import Test
from pulse_server.db.session import get_db
from pulse_server.security.deps import require_admin
from pulse_server.services import iperf3_orchestrator, test_orchestrator
from pulse_shared.enums import TestType

router = APIRouter(
    prefix="/v1/admin/tests",
    tags=["admin", "tests"],
    dependencies=[Depends(require_admin)],
)


class CreateTestBody(BaseModel):
    type: TestType
    agent_uid: str | None = None
    """Required for single-agent probes (tcp/dns/http). Ignored for iperf3 which uses
    client_agent_uid / server_agent_uid."""
    client_agent_uid: str | None = None
    server_agent_uid: str | None = None
    spec: dict[str, Any]
    timeout_s: float = 30.0


class TestView(BaseModel):
    id: int
    type: str
    state: str
    spec: dict[str, Any]
    created_at: int
    started_at: int | None
    finished_at: int | None
    result: dict[str, Any] | None
    error: str | None


class CreateTestResponse(BaseModel):
    test_id: int


@router.post("", response_model=CreateTestResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_test(
    body: CreateTestBody, request: Request, db: AsyncSession = Depends(get_db)
) -> CreateTestResponse:
    if body.type in (TestType.TCP_PROBE, TestType.DNS_PROBE, TestType.HTTP_PROBE):
        if not body.agent_uid:
            raise HTTPException(400, "agent_uid required for single-agent probes")
        try:
            created = await test_orchestrator.submit_simple_probe(
                db,
                test_type=body.type,
                agent_uid=body.agent_uid,
                spec=body.spec,
                timeout_s=body.timeout_s,
            )
        except test_orchestrator.TestOrchestrationError as e:
            raise HTTPException(400, str(e))
        return CreateTestResponse(test_id=created.test_id)

    if body.type == TestType.IPERF3_PAIR:
        if not body.client_agent_uid or not body.server_agent_uid:
            raise HTTPException(
                400, "client_agent_uid and server_agent_uid are required for iperf3_pair"
            )
        settings = request.app.state.settings
        try:
            created = await iperf3_orchestrator.submit_iperf3_pair(
                db,
                settings=settings,
                client_agent_uid=body.client_agent_uid,
                server_agent_uid=body.server_agent_uid,
                duration_s=int(body.spec.get("duration_s", 10)),
                protocol=str(body.spec.get("protocol", "tcp")),
                bitrate=body.spec.get("bitrate"),
            )
        except iperf3_orchestrator.IperfOrchestrationError as e:
            raise HTTPException(400, str(e))
        return CreateTestResponse(test_id=created.test_id)

    raise HTTPException(400, f"unknown test type: {body.type}")


@router.get("", response_model=list[TestView])
async def list_tests(
    db: AsyncSession = Depends(get_db),
    state: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[TestView]:
    stmt = select(Test).order_by(desc(Test.created_at)).limit(limit)
    if state is not None:
        stmt = stmt.where(Test.state == state)
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_view(r) for r in rows]


@router.get("/{test_id}", response_model=TestView)
async def get_test(test_id: int, db: AsyncSession = Depends(get_db)) -> TestView:
    row = await db.get(Test, test_id)
    if row is None:
        raise HTTPException(404, "test not found")
    return _to_view(row)


@router.post("/{test_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_test(test_id: int, db: AsyncSession = Depends(get_db)) -> None:
    # Try the iperf3 orchestrator first (owns its own cancel path because of port/state
    # cleanup); fall back to the single-command cancel if not an iperf3 test.
    if await iperf3_orchestrator.cancel(db, test_id):
        return
    ok = await test_orchestrator.cancel_test(db, test_id)
    if not ok:
        raise HTTPException(409, "test is not in a cancellable state")


def _to_view(row: Test) -> TestView:
    return TestView(
        id=row.id,
        type=row.type,
        state=row.state,
        spec=row.spec if isinstance(row.spec, dict) else {},
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        result=row.result,
        error=row.error,
    )
