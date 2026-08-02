from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .. import auth
from ..models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginPayload(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(payload: LoginPayload, response: Response, request: Request):
    client_ip = request.client.host if request.client else "?"
    key = (client_ip, payload.username.strip().lower())
    if auth.rate_limited(key):
        raise HTTPException(429, "Zu viele Login-Versuche – bitte kurz warten")
    user = auth.authenticate(payload.username.strip(), payload.password)
    if not user:
        raise HTTPException(401, "Benutzername oder Passwort falsch")
    auth.set_session_cookie(response, user)
    return {"ok": True, "user": _to_dict(user)}


@router.post("/logout")
def logout(response: Response):
    auth.clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(auth.get_current_user)):
    return {"user": _to_dict(user)}


def _to_dict(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "display_name": u.display_name,
        "is_admin": u.is_admin,
        "llm_provider": u.llm_provider or "",
        "llm_model": u.llm_model or "",
    }
