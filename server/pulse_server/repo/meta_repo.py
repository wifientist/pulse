"""Tiny key-value bag for singleton runtime state that doesn't deserve its own table."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_server.db.models import Meta

PEER_ASSIGNMENTS_VERSION = "peer_assignments_version"
LAST_MINUTE_BUCKET_ROLLED = "last_minute_bucket_rolled"
LAST_HOUR_BUCKET_ROLLED = "last_hour_bucket_rolled"


async def get_int(db: AsyncSession, key: str, default: int = 0) -> int:
    row = (await db.execute(select(Meta).where(Meta.key == key))).scalar_one_or_none()
    if row is None:
        return default
    try:
        return int(row.value)
    except ValueError:
        return default


async def set_int(db: AsyncSession, key: str, value: int) -> None:
    row = (await db.execute(select(Meta).where(Meta.key == key))).scalar_one_or_none()
    if row is None:
        db.add(Meta(key=key, value=str(value)))
    else:
        row.value = str(value)


async def bump(db: AsyncSession, key: str) -> int:
    current = await get_int(db, key, 0)
    nxt = current + 1
    await set_int(db, key, nxt)
    return nxt
