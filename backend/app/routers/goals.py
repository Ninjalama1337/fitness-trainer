from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from .. import auth
from ..db import session
from ..models import Activity, Goal, User

router = APIRouter(prefix="/api/goals", tags=["goals"])


class GoalPayload(BaseModel):
    running_km: float | None = None
    cycling_km: float | None = None


@router.get("")
def get_goals(user: User = Depends(auth.get_current_user)):
    with session() as s:
        goal = s.get(Goal, user.id)
    running_goal = goal.running_km if goal else None
    cycling_goal = goal.cycling_km if goal else None

    month_start = date.today().replace(day=1)
    with session() as s:
        acts = s.exec(
            select(Activity).where(
                Activity.user_id == user.id, Activity.start_time >= month_start
            )
        ).all()
    running_km = round(sum(a.distance_km or 0 for a in acts if a.sport == "running"), 1)
    cycling_km = round(sum(a.distance_km or 0 for a in acts if a.sport == "cycling"), 1)

    def progress(so_far, target):
        if not target:
            return None
        return round(min(1.0, so_far / target) * 100)

    return {
        "month": month_start.strftime("%B %Y"),
        "running_km": running_km,
        "cycling_km": cycling_km,
        "running_goal": running_goal,
        "cycling_goal": cycling_goal,
        "running_progress": progress(running_km, running_goal),
        "cycling_progress": progress(cycling_km, cycling_goal),
    }


@router.put("")
def set_goals(payload: GoalPayload, user: User = Depends(auth.get_current_user)):
    with session() as s:
        goal = s.get(Goal, user.id) or Goal(user_id=user.id)
        goal.running_km = payload.running_km if payload.running_km is not None else goal.running_km
        goal.cycling_km = payload.cycling_km if payload.cycling_km is not None else goal.cycling_km
        goal.updated_at = datetime.now()
        s.add(goal)
        s.commit()
        return {"ok": True}
