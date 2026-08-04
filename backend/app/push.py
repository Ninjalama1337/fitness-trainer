import json
import logging

from . import config, db
from .models import PushSubscription, User
from .sports import SPORT_LABELS

logger = logging.getLogger("fitness")

try:
    from pywebpush import WebPushException, webpush
except ImportError:  # pragma: no cover
    webpush = None


def vapid_keys() -> tuple[str, str] | None:
    public = config.get("VAPID_PUBLIC_KEY")
    private = config.get("VAPID_PRIVATE_KEY")
    if not public or not private:
        return None
    return public, private


def _build_vapid(sub: str) -> dict:
    public, private = vapid_keys()
    return {
        "vapid_private_key": private,
        "vapid_public_key": public,
        "vapid_subject": sub,
    }


def subscribe(user_id: int, endpoint: str, p256dh: str, auth_key: str) -> None:
    if not endpoint or not p256dh or not auth_key:
        raise ValueError("Unvollständiges Push-Abonnement")
    with db.session() as s:
        existing = s.exec(
            db.select(PushSubscription).where(PushSubscription.endpoint == endpoint)
        ).first()
        if existing:
            existing.user_id = user_id
            existing.p256dh = p256dh
            existing.auth_key = auth_key
            existing.enabled = True
            s.add(existing)
        else:
            s.add(
                PushSubscription(
                    user_id=user_id, endpoint=endpoint, p256dh=p256dh, auth_key=auth_key
                )
            )
        s.commit()


def unsubscribe(endpoint: str) -> None:
    with db.session() as s:
        row = s.exec(
            db.select(PushSubscription).where(PushSubscription.endpoint == endpoint)
        ).first()
        if row:
            s.delete(row)
            s.commit()


def subscriptions(user_id: int | None = None) -> list[PushSubscription]:
    with db.session() as s:
        q = db.select(PushSubscription)
        if user_id is not None:
            q = q.where(PushSubscription.user_id == user_id)
        return list(s.exec(q).all())


def send_to_user(user_id: int, title: str, body: str, tag: str | None = None) -> int:
    """Sendet eine Push-Nachricht an alle Abonnements des Users. Gibt Anzahl der Zustellungen zurück."""
    if webpush is None or not vapid_keys():
        return 0
    sent = 0
    for sub in subscriptions(user_id):
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth_key},
                },
                data=json.dumps({"title": title, "body": body, "tag": tag}),
                vapid_private_key=vapid_keys()[1],
                vapid_claims={"sub": f"mailto:{config.get('CONTACT_EMAIL', 'admin@localhost')}"},
            )
            sent += 1
        except Exception as exc:
            text = str(exc)
            if "410" in text or "404" in text:
                with db.session() as s:
                    row = s.exec(
                        db.select(PushSubscription).where(PushSubscription.endpoint == sub.endpoint)
                    ).first()
                    if row:
                        s.delete(row)
                        s.commit()
            else:
                logger.info("Push-Zustellung fehlgeschlagen: %s", text[:150])
    return sent


def notify_sync_error(user_id: int, message: str) -> None:
    if "404" in message or "401" in message or "login" in message.lower():
        return
    send_to_user(
        user_id,
        "Garmin-Sync fehlgeschlagen",
        message[:120],
        tag="sync-error",
    )


def notify_daily_plan(user_id: int) -> None:
    """Erinnerung: heutiges Plantraining, falls nicht erledigt."""
    from datetime import date

    from sqlmodel import select

    from .models import PlanDay
    from .plan_service import iso_week

    with db.session() as s:
        user = s.get(User, user_id)
    if not user:
        return
    today = date.today()
    week = iso_week(today)
    dow = today.weekday()
    with db.session() as s:
        entry = s.exec(
            select(PlanDay).where(
                PlanDay.user_id == user_id,
                PlanDay.week == week,
                PlanDay.day_offset == dow,
            )
        ).first()
    if not entry or entry.done:
        return
    label = SPORT_LABELS.get(entry.sport, entry.sport)
    send_to_user(
        user_id,
        f"Geplantes Training: {label}",
        entry.focus or entry.description or "Heute steht eine Einheit auf dem Plan.",
        tag="plan-today",
    )
