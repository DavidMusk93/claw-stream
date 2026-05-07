from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Any

from core import get_logger, get_trace_id, set_trace_id

router = APIRouter(prefix="/api/log", tags=["log"])
log = get_logger("frontend")


class FrontendLog(BaseModel):
    trace_id: str = ""
    level: str = "info"
    tag: str = "frontend"
    msg: str
    data: str | None = None
    ts: str | None = None


@router.post("")
async def receive_log(body: FrontendLog, request: Request):
    """接收前端上报的日志/错误"""
    tid = body.trace_id or get_trace_id() or "-"
    set_trace_id(tid)
    msg = f"[{body.tag}] {body.msg}"
    extra = {"client": request.client.host if request.client else "-"}
    if body.data:
        extra["data"] = body.data[:500]
    if body.ts:
        extra["ts"] = body.ts

    if body.level == "error":
        log.error(msg, extra=extra)
    elif body.level == "warn":
        log.warning(msg, extra=extra)
    else:
        log.info(msg, extra=extra)
    return {"ok": True}
