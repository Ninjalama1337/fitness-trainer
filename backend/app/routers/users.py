from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from .. import auth, crypto
from ..db import session
from ..models import User

router = APIRouter(prefix="/api/users", tags=["users"])


class CreateUserPayload(BaseModel):
    username: str
    password: str
    display_name: str = ""


class UpdatePasswordPayload(BaseModel):
    password: str


class LlmPayload(BaseModel):
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


def _to_dict(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "display_name": u.display_name,
        "is_admin": u.is_admin,
        "llm_provider": u.llm_provider or "",
        "llm_model": u.llm_model or "",
        "llm_has_key": bool(u.llm_api_key),
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


@router.get("")
def list_users(admin: User = Depends(auth.require_admin)):
    with session() as s:
        users = s.exec(select(User).order_by(User.id)).all()
        return {"items": [_to_dict(u) for u in users]}


@router.post("")
def create_user(payload: CreateUserPayload, admin: User = Depends(auth.require_admin)):
    username = payload.username.strip().lower()
    if not username or len(username) < 3:
        raise HTTPException(400, "Benutzername muss mind. 3 Zeichen haben")
    if len(payload.password) < 8:
        raise HTTPException(400, "Passwort muss mind. 8 Zeichen haben")
    with session() as s:
        if s.exec(select(User).where(User.username == username)).first():
            raise HTTPException(409, "Benutzername existiert bereits")
        user = User(
            username=username,
            password_hash=auth.hash_password(payload.password),
            display_name=payload.display_name.strip() or username,
        )
        s.add(user)
        s.commit()
        s.refresh(user)
        return {"ok": True, "user": _to_dict(user)}


@router.delete("/{user_id}")
def delete_user(user_id: int, admin: User = Depends(auth.require_admin)):
    if user_id == admin.id:
        raise HTTPException(400, "Der eigene Account kann nicht gelöscht werden")
    with session() as s:
        user = s.get(User, user_id)
        if not user:
            raise HTTPException(404, "User nicht gefunden")
        s.delete(user)
        s.commit()
        return {"ok": True}


@router.post("/{user_id}/password")
def reset_password(
    user_id: int, payload: UpdatePasswordPayload, admin: User = Depends(auth.require_admin)
):
    if len(payload.password) < 8:
        raise HTTPException(400, "Passwort muss mind. 8 Zeichen haben")
    with session() as s:
        user = s.get(User, user_id)
        if not user:
            raise HTTPException(404, "User nicht gefunden")
        user.password_hash = auth.hash_password(payload.password)
        s.add(user)
        s.commit()
        return {"ok": True}


@router.put("/me/llm")
def update_my_llm(payload: LlmPayload, user: User = Depends(auth.get_current_user)):
    with session() as s:
        me = s.get(User, user.id)
        provider = (payload.provider or "").strip().lower() or None
        if provider and provider not in ("opencode", "openai", "anthropic", "ollama"):
            raise HTTPException(400, "Unbekannter LLM-Provider")
        me.llm_provider = provider
        me.llm_base_url = payload.base_url.strip() if payload.base_url else None
        me.llm_model = payload.model.strip() if payload.model else None
        if payload.api_key is not None:
            key = payload.api_key.strip()
            me.llm_api_key = crypto.encrypt_secret(key) if key else None
        s.add(me)
        s.commit()
        return {"ok": True}
