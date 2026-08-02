from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from .. import auth, llm, plan_service
from ..db import session
from ..models import PlanDay, RaceGoal, User

router = APIRouter(prefix="/api/race-plan", tags=["race-plan"])


class RacePayload(BaseModel):
    name: str = ""
    target_date: date
    distance_km: float = 10.0


def _to_dict(g: RaceGoal, s) -> dict:
    days = s.exec(
        select(PlanDay).where(
            PlanDay.user_id == g.user_id, PlanDay.race_goal_id == g.id
        )
    ).all()
    weeks = {d.week for d in days}
    done = sum(1 for d in days if d.done)
    return {
        "id": g.id,
        "name": g.name,
        "target_date": g.target_date.isoformat(),
        "distance_km": g.distance_km,
        "weeks": len(weeks),
        "days": len(days),
        "days_done": done,
        "first_week": sorted(weeks)[0] if weeks else None,
        "created_at": g.created_at.isoformat(),
    }


@router.get("")
def get_race_plan(user: User = Depends(auth.get_current_user)):
    with session() as s:
        goal = s.exec(
            select(RaceGoal)
            .where(RaceGoal.user_id == user.id)
            .order_by(RaceGoal.created_at.desc())
            .limit(1)
        ).first()
        if not goal:
            return {"ok": False}
        return {"ok": True, "goal": _to_dict(goal, s)}


@router.post("")
def create_race_plan(payload: RacePayload, user: User = Depends(auth.get_current_user)):
    if not llm.status(user)["configured"]:
        raise HTTPException(400, "LLM nicht konfiguriert")
    try:
        result = plan_service.create_race_plan(
            user.id, payload.target_date, payload.distance_km, payload.name.strip()
        )
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except llm.LlmError as exc:
        raise HTTPException(502, str(exc))


@router.delete("")
def delete_race_plan(user: User = Depends(auth.get_current_user)):
    with session() as s:
        goals = s.exec(
            select(RaceGoal).where(RaceGoal.user_id == user.id)
        ).all()
        for g in goals:
            for d in s.exec(
                select(PlanDay).where(
                    PlanDay.user_id == user.id, PlanDay.race_goal_id == g.id
                )
            ).all():
                s.delete(d)
            s.delete(g)
        s.commit()
        return {"ok": True}
