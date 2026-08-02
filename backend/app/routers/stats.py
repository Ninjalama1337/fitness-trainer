from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlmodel import select

from .. import auth
from ..db import session
from ..models import Activity, HealthDay, User

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/summary")
def summary(
    user: User = Depends(auth.get_current_user),
    days: int = Query(default=7, le=90),
):
    since = datetime.now() - timedelta(days=days)
    since_day = date.today() - timedelta(days=days)
    with session() as s:
        acts = s.exec(
            select(Activity).where(Activity.user_id == user.id, Activity.start_time >= since)
        ).all()
        health = s.exec(
            select(HealthDay).where(HealthDay.user_id == user.id, HealthDay.date >= since_day)
        ).all()

    health_by_day = {h.date: h for h in health}
    per_day: dict[date, dict] = {}
    for a in acts:
        d = a.start_time.date()
        entry = per_day.setdefault(
            d,
            {
                "sessions": 0,
                "running_km": 0.0,
                "cycling_km": 0.0,
                "calories": 0.0,
                "sleep_h": None,
                "strength_count": 0,
                "activities": [],
            },
        )
        entry["sessions"] += 1
        if a.sport == "running" and a.distance_km:
            entry["running_km"] += a.distance_km
        elif a.sport == "cycling" and a.distance_km:
            entry["cycling_km"] += a.distance_km
        elif a.sport == "strength":
            entry["strength_count"] += 1
        entry["calories"] += a.calories or 0
        entry["activities"].append(
            {
                "name": a.name,
                "sport": a.sport,
                "distance_km": a.distance_km,
                "duration_min": round(a.duration_seconds / 60),
                "avg_hr": a.avg_hr,
                "calories": a.calories,
                "start_time": a.start_time.isoformat(),
            }
        )
    entry["activities"].sort(key=lambda x: x["start_time"])

    series = []
    for i in range(days):
        d = date.today() - timedelta(days=days - 1 - i)
        entry = per_day.get(d, {})
        h = health_by_day.get(d)
        series.append(
            {
                "date": d.isoformat(),
                "running_km": round(entry.get("running_km", 0), 1),
                "cycling_km": round(entry.get("cycling_km", 0), 1),
                "sessions": entry.get("sessions", 0),
                "strength_count": entry.get("strength_count", 0),
                "calories": round(entry.get("calories", 0)),
                "sleep_h": round((h.sleep_seconds or 0) / 3600, 1) if h else None,
                "steps": h.steps if h else None,
                "resting_hr": h.resting_hr if h else None,
                "hrv": h.hrv_avg if h else None,
                "hrv_status": h.hrv_status if h else None,
                "activities": entry.get("activities", []),
            }
        )

    totals = {
        "sessions": len(acts),
        "running_km": round(sum(s_["running_km"] for s_ in series), 1),
        "cycling_km": round(sum(s_["cycling_km"] for s_ in series), 1),
        "strength_count": sum(s_["strength_count"] for s_ in series),
        "calories": sum(s_["calories"] for s_ in series),
    }
    sleep_vals = [s_["sleep_h"] for s_ in series if s_["sleep_h"] is not None]
    totals["avg_sleep_h"] = round(sum(sleep_vals) / len(sleep_vals), 1) if sleep_vals else None
    totals["avg_hr"] = None
    rest_vals = [s_["resting_hr"] for s_ in series if s_["resting_hr"]]
    totals["avg_resting_hr"] = round(sum(rest_vals) / len(rest_vals)) if rest_vals else None
    hrv_vals = [s_["hrv"] for s_ in series if s_["hrv"]]
    totals["avg_hrv"] = round(sum(hrv_vals) / len(hrv_vals), 1) if hrv_vals else None

    hrs = [a.avg_hr for a in acts if a.avg_hr]
    if hrs:
        totals["avg_hr"] = round(sum(hrs) / len(hrs), 0)

    return {"days": days, "totals": totals, "series": series}


@router.get("/zones")
def zone_stats(
    user: User = Depends(auth.get_current_user),
    days: int = Query(default=30, le=90),
):
    since = datetime.now() - timedelta(days=days)
    with session() as s:
        acts = s.exec(
            select(Activity).where(
                Activity.user_id == user.id,
                Activity.start_time >= since,
                Activity.hr_zones.isnot(None),
            )
        ).all()
    totals = {"zone1": 0, "zone2": 0, "zone3": 0, "zone4": 0, "zone5": 0}
    per_activity = []
    for a in acts:
        zones = a.hr_zones or {}
        zs = {k: int(zones.get(k, 0)) for k in totals}
        per_activity.append({"id": a.id, "name": a.name, "sport": a.sport, "zones": zs})
        for k in totals:
            totals[k] += zs[k]
    total_sec = sum(totals.values())
    shares = {
        k: round(v * 100 / total_sec, 1) if total_sec else 0
        for k, v in totals.items()
    }
    return {
        "totals_seconds": totals,
        "shares": shares,
        "total_minutes": round(total_sec / 60),
        "activities": per_activity[-20:],
    }
