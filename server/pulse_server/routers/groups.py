from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/v1/admin/groups", tags=["admin", "groups"])
