"""backend/routers/track.py — User behavior tracking (埋点)

Receives batched behavior events from the frontend and persists them to
the user_events table via the serial write queue. Events feed interest
analysis (play / like / view patterns per star and title).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.routers.auth import require_auth
from core import db, get_logger
from core.db.write_queue import db_write

router = APIRouter(prefix="/api/track", tags=["track"], dependencies=[Depends(require_auth)])
log = get_logger("track")


class TrackEvent(BaseModel):
    event: str = Field(..., min_length=1, max_length=64)
    code: str | None = Field(default=None, max_length=64)
    star_code: str | None = Field(default=None, max_length=64)
    meta: dict[str, Any] | None = None


class TrackRequest(BaseModel):
    events: list[TrackEvent] = Field(..., min_length=1, max_length=100)


@router.post("")
async def track_events(request: TrackRequest) -> dict[str, int]:
    """Batch-record user behavior events."""
    inserted = await db_write(
        db.insert_user_events, [e.model_dump() for e in request.events]
    )
    return {"inserted": inserted}
