from __future__ import annotations

from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel, Field
from typing import Any, Literal

from backend.routers.auth import require_auth
from core import get_logger, get_trace_id, set_trace_id

router = APIRouter(prefix="/api/log", tags=["log"], dependencies=[Depends(require_auth)])
log = get_logger("frontend")


def _sanitize(text: str) -> str:
    """Strip newlines and control chars to prevent log forging."""
    return "".join(ch for ch in text if ch.isprintable())


class FrontendLog(BaseModel):
    trace_id: str = Field(default="", max_length=64)
    level: Literal["debug", "info", "warn", "warning", "error"] = "info"
    tag: str = Field(default="frontend", max_length=64)
    msg: str = Field(..., max_length=2000)
    data: str | None = Field(default=None, max_length=2000)
    ts: str | None = Field(default=None, max_length=64)


@router.post("")
async def receive_log(body: FrontendLog, request: Request):
    """Receive logs/errors reported by the frontend."""
    tid = _sanitize(body.trace_id) or get_trace_id() or "-"
    set_trace_id(tid)
    tag = _sanitize(body.tag)
    msg = _sanitize(body.msg)
    full_msg = f"[{tag}] {msg}"
    extra: dict[str, Any] = {"client": request.client.host if request.client else "-"}
    if body.data:
        extra["data"] = _sanitize(body.data)[:500]
    if body.ts:
        extra["ts"] = _sanitize(body.ts)

    level = body.level
    if level == "warning":
        level = "warn"

    if level == "error":
        log.error(full_msg, extra=extra)
    elif level == "warn":
        log.warning(full_msg, extra=extra)
    elif level == "debug":
        log.debug(full_msg, extra=extra)
    else:
        log.info(full_msg, extra=extra)
    return {"ok": True}
