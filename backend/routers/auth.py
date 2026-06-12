from __future__ import annotations

import datetime
from fastapi import APIRouter, Response
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
async def auth(req: AuthRequest, response: Response):
    """Authenticate with daily password and set HTTP cookie.

    The cookie must be set by the backend (not client-side JS) so that SSR
    renders of the homepage can see it on the very next request.
    """
    ok = req.password == _today_password()
    if ok:
        response.set_cookie(
            "claw_auth",
            "ok",
            max_age=86400,
            path="/",
            httponly=False,
            samesite="lax",
        )
    return AuthResponse(ok=ok)
