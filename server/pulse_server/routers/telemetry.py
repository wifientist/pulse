from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import Agent
from pulse_server.db.session import get_db
from pulse_server.security.deps import require_agent
from pulse_server.services import poll_service
from pulse_shared.contracts import PollRequest, PollResponse

router = APIRouter(tags=["agent"])


@router.post("/v1/agent/poll", response_model=PollResponse)
async def poll(
    body: PollRequest,
    request: Request,
    agent: Agent = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
) -> PollResponse:
    if body.agent_uid != agent.agent_uid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="agent_uid does not match authenticated token",
        )
    return await poll_service.handle_poll(
        db=db,
        agent=agent,
        body=body,
        settings=request.app.state.settings,
        source_ip=request.client.host if request.client else None,
    )
