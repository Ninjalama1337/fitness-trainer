from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from sqlmodel import select

from .. import auth
from ..db import session
from ..models import Activity, User
from ..sports import SPORT_LABELS

router = APIRouter(prefix="/api/activities", tags=["activities"])


def _to_dict(a: Activity) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "sport": a.sport,
        "sport_label": SPORT_LABELS.get(a.sport, a.sport),
        "start_time": a.start_time.isoformat(),
        "duration_min": round(a.duration_seconds / 60),
        "distance_km": a.distance_km,
        "avg_hr": a.avg_hr,
        "max_hr": a.max_hr,
        "calories": a.calories,
        "avg_pace_min_km": a.avg_pace_min_km,
        "avg_speed_kmh": a.avg_speed_kmh,
        "source": a.source,
    }


@router.get("")
def list_activities(
    user: User = Depends(auth.get_current_user),
    sport: str | None = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
    since: date | None = None,
):
    with session() as s:
        filters = [Activity.user_id == user.id]
        if sport:
            filters.append(Activity.sport == sport)
        if since:
            filters.append(Activity.start_time >= datetime.combine(since, datetime.min.time()))
        q = select(Activity).where(*filters).order_by(Activity.start_time.desc())
        rows = s.exec(q.offset(offset).limit(limit)).all()
        total = len(s.exec(select(Activity).where(*filters)).all())
        return {"total": total, "items": [_to_dict(a) for a in rows]}


@router.get("/zones")
def activity_zones(
    user: User = Depends(auth.get_current_user),
    limit: int = Query(default=30, le=200),
):
    with session() as s:
        q = (
            select(Activity)
            .where(Activity.user_id == user.id, Activity.hr_zones.isnot(None))
            .order_by(Activity.start_time.desc())
            .limit(limit)
        )
        rows = s.exec(q).all()
        out = []
        for a in rows:
            zones = a.hr_zones or {}
            total = sum(zones.values()) or 1
            out.append(
                {
                    "id": a.id,
                    "name": a.name,
                    "sport": a.sport,
                    "start_time": a.start_time.isoformat(),
                    "zones": zones,
                    "shares": {
                        k: round(v * 100 / total, 1) for k, v in zones.items()
                    },
                }
            )
        return {"items": out}
