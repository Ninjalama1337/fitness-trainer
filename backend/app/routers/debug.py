from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select

from .. import auth, garmin_sync as g
from ..db import session
from ..models import HealthDay, User

router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.get("/health")
def debug_health(user: User = Depends(auth.require_admin)):
    """Zeigt die gespeicherten Health-Daten aus der DB."""
    with session() as s:
        rows = s.exec(
            select(HealthDay)
            .where(HealthDay.user_id == user.id)
            .order_by(HealthDay.date.desc())
            .limit(7)
        ).all()
        return {
            "items": [
                {
                    "date": h.date.isoformat(),
                    "sleep_h": round((h.sleep_seconds or 0) / 3600, 2) if h.sleep_seconds else None,
                    "deep_sleep_h": round((h.deep_sleep_seconds or 0) / 3600, 2) if h.deep_sleep_seconds else None,
                    "resting_hr": h.resting_hr,
                    "hrv": h.hrv_avg,
                    "hrv_status": h.hrv_status,
                    "calories": h.active_calories,
                    "steps": h.steps,
                    "stress": h.stress_avg,
                }
                for h in rows
            ]
        }


@router.get("/garmin")
def debug_garmin(user: User = Depends(auth.require_admin)):
    """Zeigt die rohe Garmin-Antwort (ohne Secrets) – fürs Debugging."""
    if not g.is_configured(user.id):
        raise HTTPException(400, "Garmin nicht konfiguriert")
    api = g._api(user.id)
    try:
        g.token_dir(user.id).mkdir(parents=True, exist_ok=True)
        api.login(tokenstore=str(g.token_dir(user.id)))
    except Exception as exc:
        raise HTTPException(502, f"Login fehlgeschlagen: {exc}")

    out: dict = {}
    try:
        acts = g._api_get(api, lambda: api.get_activities(0, 3))
        out["activities_count"] = len(acts)
        out["activities_keys"] = sorted(acts[0].keys()) if acts else []
        out["activities"] = [
            {
                "name": a.get("activityName"),
                "sportType": a.get("sportType"),
                "sportTypeId": a.get("sportTypeId"),
                "activityType": a.get("activityType"),
                "startTimeLocal": a.get("startTimeLocal"),
                "distance": a.get("distance"),
                "duration": a.get("duration"),
                "averageHR": a.get("averageHR"),
                "calories": a.get("calories"),
            }
            for a in acts
        ]
    except Exception as exc:
        out["activities_error"] = str(exc)[:300]

    today = date.today()
    try:
        stats = g._api_get(api, lambda: api.get_stats(today.isoformat()))
        if isinstance(stats, dict):
            out["stats_today"] = {
                k: stats.get(k)
                for k in (
                    "totalKilocalories",
                    "activeKilocalories",
                    "burnedKilocalories",
                    "totalSteps",
                    "restingHeartRate",
                    "averageStressLevel",
                    "sleepingSeconds",
                    "measurableAsleepDuration",
                )
            }
    except Exception as exc:
        out["stats_today_error"] = str(exc)[:200]

    try:
        sleep = g._api_get(api, lambda: api.get_sleep_data(today.isoformat()))
        if isinstance(sleep, dict):
            dto = sleep.get("dailySleepDTO") or {}
            out["sleep_today"] = {
                "sleepTimeSeconds": dto.get("sleepTimeSeconds"),
                "avgOvernightHrv": sleep.get("avgOvernightHrv"),
                "hrvStatus": sleep.get("hrvStatus"),
                "hrvData_keys": sorted(sleep.get("hrvData", {}).keys()) if isinstance(sleep.get("hrvData"), dict) else None,
                "restingHeartRate": sleep.get("restingHeartRate"),
            }
    except Exception as exc:
        out["sleep_today_error"] = str(exc)[:200]

    return out
