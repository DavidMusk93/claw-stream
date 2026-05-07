from __future__ import annotations

import datetime
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/auth", tags=["auth"])


class AuthRequest(BaseModel):
    password: str


class AuthResponse(BaseModel):
    ok: bool


def _today_password() -> str:
    """Daily rotating password."""
    d = datetime.datetime.now()
    return f"rn{d.strftime('%y%m%d')}{d.day % 2}"


@router.post("", response_model=AuthResponse)
async def auth(req: AuthRequest):
    """Authenticate with daily password."""
    return AuthResponse(ok=req.password == _today_password())
