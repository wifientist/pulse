"""FastAPI dependencies for authentication.

`require_admin` — expects the configured admin bearer token.
`require_agent` — resolves the requesting agent from its bearer token.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import Agent
from pulse_server.db.session import get_db
from pulse_server.security.tokens import verify_token

_bearer = HTTPBearer(auto_error=True)


async def require_admin(
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> None:
    expected = request.app.state.settings.admin_token
    if not expected or creds.credentials != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin token"
        )


async def require_agent(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> Agent:
    # Full-table scan on hashes is the price of never storing plaintext. At <25 agents
    # this is trivial. If the fleet grows, we can add a short-lived cache keyed by a
    # non-reversible token fingerprint (e.g. first N bytes of sha256) -> agent_id.
    agents = (await db.execute(select(Agent).where(Agent.state != "revoked"))).scalars().all()
    for agent in agents:
        if verify_token(creds.credentials, agent.token_hash):
            if agent.state == "revoked":
                break
            return agent
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid agent token")
