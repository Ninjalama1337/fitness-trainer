from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from .. import auth, garmin_sync as g
from .. import garmin_workouts as gw
from ..db import session
from ..models import PlanDay, Suggestion, User
from ..plan_service import WEEKDAYS

router = APIRouter(prefix="/api/garmin", tags=["garmin"])


class CredPayload(BaseModel):
    email: str
    password: str


class SyncPayload(BaseModel):
    mfa_code: str | None = None


class WorkoutPayload(BaseModel):
    device_ids: list[str] | None = None


def _handle_garmin_error(exc: Exception, user_id: int, action: str):
    if isinstance(exc, g.GarminMfaRequired):
        g.update_sync_state(user_id, "mfa", str(exc))
        raise HTTPException(428, {"error": str(exc), "mfa": True})
    if isinstance(exc, (gw.WorkoutError, g.GarminError)):
        g.update_sync_state(user_id, "error", str(exc))
        raise HTTPException(502, {"error": str(exc), "mfa": False})
    g.update_sync_state(user_id, "error", f"{action} fehlgeschlagen: {exc}")
    raise HTTPException(500, {"error": f"{action} fehlgeschlagen: {exc}", "mfa": False})


@router.post("/credentials")
def save_credentials(
    payload: CredPayload, user: User = Depends(auth.get_current_user)
):
    email = payload.email.strip()
    if not email or "@" not in email or not payload.password:
        raise HTTPException(400, "Bitte E-Mail und Passwort eingeben")
    g.save_credentials(user.id, email, payload.password)
    return {"ok": True, "garmin_email": g.masked_email(user.id)}


@router.delete("/credentials")
def delete_credentials(user: User = Depends(auth.get_current_user)):
    with session() as s:
        from ..models import GarminCred

        row = s.get(GarminCred, user.id)
        if row:
            s.delete(row)
            s.commit()
    g.update_sync_state(user.id, "never", "Garmin-Login entfernt")
    return {"ok": True}


@router.post("/sync")
def sync(
    payload: SyncPayload | None = None,
    limit: int = 50,
    user: User = Depends(auth.get_current_user),
):
    if not g.is_configured(user.id):
        raise HTTPException(400, {"error": "Garmin nicht konfiguriert", "mfa": False})
    try:
        result = g.sync_garmin(
            user.id, limit=limit, mfa_code=payload.mfa_code if payload else None
        )
        g.update_sync_state(
            user.id,
            "ok",
            f"Sync erfolgreich: {result['imported']} neu, {result['skipped']} übersprungen",
        )
        return {"ok": True, **result}
    except Exception as exc:
        import logging

        logging.getLogger("fitness").exception("Sync fehlgeschlagen: %s", exc)
        _handle_garmin_error(exc, user.id, "Sync")


@router.get("/devices")
def get_devices(user: User = Depends(auth.get_current_user)):
    if not g.is_configured(user.id):
        raise HTTPException(400, {"error": "Garmin nicht konfiguriert", "mfa": False})
    try:
        devices = gw.list_devices(user.id)
        return {"ok": True, "items": devices}
    except Exception as exc:
        _handle_garmin_error(exc, user.id, "Geräte-Abruf")


@router.post("/workout/suggestion/{sug_id}")
def send_suggestion_workout(
    sug_id: int,
    payload: WorkoutPayload | None = None,
    user: User = Depends(auth.get_current_user),
):
    if not g.is_configured(user.id):
        raise HTTPException(400, {"error": "Garmin nicht konfiguriert", "mfa": False})
    with session() as s:
        sug = s.get(Suggestion, sug_id)
        if not sug or sug.user_id != user.id:
            raise HTTPException(404, "Vorschlag nicht gefunden")
        try:
            wid = gw.upload_workout(user.id, sug.title or "Training", sug.sport, sug.steps)
            pushed = gw.push_workout_to_devices(
                user.id, wid, payload.device_ids if payload else None
            )
            sug.garmin_workout_id = wid
            s.add(sug)
            s.commit()
            return {
                "ok": True,
                "workout_id": wid,
                "name": sug.title,
                "sport": sug.sport,
                "pushed": pushed,
            }
        except Exception as exc:
            _handle_garmin_error(exc, user.id, "Workout-Upload")


@router.post("/workout/plan/{plan_id}")
def send_plan_workout(
    plan_id: int,
    payload: WorkoutPayload | None = None,
    user: User = Depends(auth.get_current_user),
):
    if not g.is_configured(user.id):
        raise HTTPException(400, {"error": "Garmin nicht konfiguriert", "mfa": False})
    with session() as s:
        day = s.get(PlanDay, plan_id)
        if not day or day.user_id != user.id:
            raise HTTPException(404, "Plan-Tag nicht gefunden")
        if day.sport == "rest":
            raise HTTPException(400, "Ruhetage können nicht als Workout übertragen werden")
        try:
            weekday = WEEKDAYS[day.day_offset % 7]
            name = f"{day.focus or 'Training'} ({weekday})"
            wid = gw.upload_workout(user.id, name, day.sport, day.steps, day.kraft_steps)
            pushed = gw.push_workout_to_devices(
                user.id, wid, payload.device_ids if payload else None
            )
            day.garmin_workout_id = wid
            s.add(day)
            s.commit()
            return {
                "ok": True,
                "workout_id": wid,
                "name": name,
                "sport": day.sport,
                "pushed": pushed,
            }
        except Exception as exc:
            _handle_garmin_error(exc, user.id, "Workout-Upload")


@router.post("/workout/plan-all")
def send_all_plan_workouts(week: str, user: User = Depends(auth.get_current_user)):
    if not g.is_configured(user.id):
        raise HTTPException(400, {"error": "Garmin nicht konfiguriert", "mfa": False})
    with session() as s:
        days = s.exec(
            select(PlanDay)
            .where(PlanDay.user_id == user.id, PlanDay.week == week)
            .order_by(PlanDay.day_offset)
        ).all()
        sent, skipped, errors = [], [], []
        devices = None
        for day in days:
            if day.garmin_workout_id:
                skipped.append({"id": day.id, "reason": "bereits gesendet"})
                continue
            if not gw.sendable(day.sport, day.steps, day.kraft_steps):
                skipped.append({"id": day.id, "reason": "nicht sendbar (keine Steps/Übungen)"})
                continue
            try:
                if devices is None:
                    devices = gw.list_devices(user.id)
                target_ids = gw.default_device_ids(devices, day.sport)
                wid = gw.upload_workout(user.id, f"{day.focus or 'Training'} W{week}", day.sport, day.steps, day.kraft_steps)
                gw.push_workout_to_devices(user.id, wid, target_ids)
                day.garmin_workout_id = wid
                sent.append({"id": day.id, "workout_id": wid, "devices": target_ids})
            except g.GarminMfaRequired:
                s.rollback()
                g.update_sync_state(user.id, "mfa", "MFA-Code erforderlich")
                raise HTTPException(428, {"error": "MFA-Code erforderlich", "mfa": True})
            except Exception as exc:
                errors.append({"id": day.id, "error": str(exc)})
        s.commit()
        return {"ok": True, "sent": sent, "skipped": skipped, "errors": errors}


@router.delete("/workout/{workout_id}")
def delete_workout(workout_id: str, user: User = Depends(auth.get_current_user)):
    if not g.is_configured(user.id):
        raise HTTPException(400, {"error": "Garmin nicht konfiguriert", "mfa": False})
    try:
        gw.delete_workout(user.id, workout_id)
    except Exception as exc:
        _handle_garmin_error(exc, user.id, "Workout-Löschen")
    with session() as s:
        for row in s.exec(
            select(Suggestion).where(
                Suggestion.user_id == user.id, Suggestion.garmin_workout_id == workout_id
            )
        ).all():
            row.garmin_workout_id = None
            s.add(row)
        for row in s.exec(
            select(PlanDay).where(
                PlanDay.user_id == user.id, PlanDay.garmin_workout_id == workout_id
            )
        ).all():
            row.garmin_workout_id = None
            s.add(row)
        s.commit()
    return {"ok": True}
