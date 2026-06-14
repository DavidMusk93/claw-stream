from __future__ import annotations

import datetime
import hmac
import os

from fastapi import APIRouter, Request, Response, HTTPException, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/auth", tags=["auth"])

# httponly=False is required because the frontend reads this cookie via
# document.cookie for client-side navigation guards. Secure is enabled in
# production via the SECURE_COOKIES env var.
_COOKIE_SECURE = os.environ.get("SECURE_COOKIES", "0") == "1"


class AuthRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=128)


class AuthResponse(BaseModel):
    ok: bool


def _today_password() -> str:
    """Daily rotating password."""
    d = datetime.datetime.now(datetime.timezone.utc)
    return f"rn{d.strftime('%y%m%d')}{d.day % 2}"


def require_auth(request: Request) -> None:
    """Dependency that enforces the `claw_auth=ok` cookie set by /api/auth."""
    if request.cookies.get("claw_auth") != "ok":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )


@router.post("", response_model=AuthResponse)
async def auth(req: AuthRequest, response: Response):
    """Authenticate with daily password and set HTTP cookie.

    The cookie must be set by the backend (not client-side JS) so that SSR
    renders of the homepage can see it on the very next request.
    """
    ok = hmac.compare_digest(req.password, _today_password())
    if ok:
        response.set_cookie(
            "claw_auth",
            "ok",
            max_age=86400,
            path="/",
            httponly=False,
            samesite="strict",
            secure=_COOKIE_SECURE,
        )
    return AuthResponse(ok=ok)
