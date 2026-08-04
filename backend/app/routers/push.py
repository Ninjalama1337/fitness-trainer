from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth, push
from ..models import User

router = APIRouter(prefix="/api/push", tags=["push"])


class SubscriptionPayload(BaseModel):
    endpoint: str
    p256dh: str
    auth_key: str


@router.get("/status")
def status(user: User = Depends(auth.get_current_user)):
    keys = push.vapid_keys()
    return {
        "supported": push.webpush is not None and keys is not None,
        "public_key": keys[0] if keys else None,
        "subscriptions": len(push.subscriptions(user.id)),
    }


@router.post("/subscribe")
def subscribe(
    payload: SubscriptionPayload, user: User = Depends(auth.get_current_user)
):
    try:
        push.subscribe(user.id, payload.endpoint, payload.p256dh, payload.auth_key)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@router.delete("/unsubscribe")
def unsubscribe(
    payload: SubscriptionPayload, user: User = Depends(auth.get_current_user)
):
    push.unsubscribe(payload.endpoint)
    return {"ok": True}


@router.post("/test")
def test_send(user: User = Depends(auth.get_current_user)):
    sent = push.send_to_user(user.id, "Fitness Trainer", "Push-Benachrichtigungen funktionieren!")
    if not sent:
        raise HTTPException(400, "Kein aktives Push-Abonnement")
    return {"ok": True, "sent": sent}
