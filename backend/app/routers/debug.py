from fastapi import APIRouter, Depends, HTTPException

from .. import auth, garmin_sync as g
from ..models import User

router = APIRouter(prefix="/api/debug", tags=["debug"])


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

    if acts:
        aid = str(acts[0].get("activityId", ""))
        try:
            zones = g._api_get(api, lambda: api.get_activity_hr_in_timezones(aid))
            out["hr_in_timezones_type"] = type(zones).__name__
            out["hr_in_timezones"] = str(zones)[:500]
        except Exception as exc:
            out["hr_in_timezones_error"] = str(exc)[:200]

    try:
        stats = g._api_get(api, lambda: api.get_stats("2026-08-02"))
        out["stats_today_keys"] = sorted(stats.keys()) if isinstance(stats, dict) else []
        out["stats_today"] = str(stats)[:400]
    except Exception as exc:
        out["stats_today_error"] = str(exc)[:200]

    try:
        sleep = g._api_get(api, lambda: api.get_sleep_data("2026-08-02"))
        out["sleep_today_keys"] = sorted(sleep.keys()) if isinstance(sleep, dict) else []
        out["sleep_today"] = str(sleep)[:400]
    except Exception as exc:
        out["sleep_today_error"] = str(exc)[:200]

    return out
