from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import Webhook, WebhookDelivery
from pulse_server.db.session import get_db
from pulse_server.security.deps import require_admin
from pulse_shared.enums import WebhookDeliveryState

router = APIRouter(
    prefix="/v1/admin/webhooks",
    tags=["admin", "webhooks"],
    dependencies=[Depends(require_admin)],
)


class WebhookView(BaseModel):
    id: int
    name: str
    url: str
    enabled: bool
    event_filter: dict
    created_at: int


class CreateWebhookBody(BaseModel):
    name: str
    url: str
    secret: str
    enabled: bool = True
    event_filter: dict = {}


class PatchWebhookBody(BaseModel):
    name: str | None = None
    url: str | None = None
    secret: str | None = None
    enabled: bool | None = None
    event_filter: dict | None = None


@router.get("", response_model=list[WebhookView])
async def list_webhooks(db: AsyncSession = Depends(get_db)) -> list[WebhookView]:
    rows = (await db.execute(select(Webhook))).scalars().all()
    return [
        WebhookView(
            id=r.id,
            name=r.name,
            url=r.url,
            enabled=r.enabled,
            event_filter=r.event_filter if isinstance(r.event_filter, dict) else {},
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("", response_model=WebhookView, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    body: CreateWebhookBody, db: AsyncSession = Depends(get_db)
) -> WebhookView:
    row = Webhook(
        name=body.name,
        url=body.url,
        secret=body.secret,
        enabled=body.enabled,
        event_filter=body.event_filter,
        created_at=int(time.time() * 1000),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return WebhookView(
        id=row.id,
        name=row.name,
        url=row.url,
        enabled=row.enabled,
        event_filter=row.event_filter if isinstance(row.event_filter, dict) else {},
        created_at=row.created_at,
    )


@router.patch("/{webhook_id}", response_model=WebhookView)
async def patch_webhook(
    webhook_id: int, body: PatchWebhookBody, db: AsyncSession = Depends(get_db)
) -> WebhookView:
    row = await db.get(Webhook, webhook_id)
    if row is None:
        raise HTTPException(404, "webhook not found")
    if body.name is not None:
        row.name = body.name
    if body.url is not None:
        row.url = body.url
    if body.secret is not None:
        row.secret = body.secret
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.event_filter is not None:
        row.event_filter = body.event_filter
    await db.commit()
    return WebhookView(
        id=row.id,
        name=row.name,
        url=row.url,
        enabled=row.enabled,
        event_filter=row.event_filter if isinstance(row.event_filter, dict) else {},
        created_at=row.created_at,
    )


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(webhook_id: int, db: AsyncSession = Depends(get_db)) -> None:
    row = await db.get(Webhook, webhook_id)
    if row is None:
        raise HTTPException(404, "webhook not found")
    # Also kill pending deliveries so they don't fire after the hook is gone.
    pending = (
        await db.execute(
            select(WebhookDelivery).where(
                WebhookDelivery.webhook_id == webhook_id,
                WebhookDelivery.state == WebhookDeliveryState.PENDING.value,
            )
        )
    ).scalars().all()
    for d in pending:
        d.state = WebhookDeliveryState.DEAD.value
        d.last_error = "webhook deleted"
    await db.delete(row)
    await db.commit()
