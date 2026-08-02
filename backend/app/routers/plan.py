from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select

from .. import auth, db, llm, plan_service
from ..models import PlanDay, User

router = APIRouter(prefix="/api/plan", tags=["plan"])


def _current_week() -> str:
    return plan_service.iso_week(date.today())


def _to_dict(d: PlanDay) -> dict:
    weekday = (d.day_offset + 0) % 7
    from ..plan_service import WEEKDAYS

    return {
        "id": d.id,
        "week": d.week,
        "day_offset": d.day_offset,
        "weekday": WEEKDAYS[weekday],
        "sport": d.sport,
        "focus": d.focus,
        "description": d.description,
        "done": d.done,
        "steps": d.steps,
        "kraft_steps": d.kraft_steps,
        "garmin_workout_id": d.garmin_workout_id,
    }


@router.get("")
def get_plan(
    week: str | None = None, user: User = Depends(auth.get_current_user)
):
    week = week or _current_week()
    with db.session() as s:
        rows = s.exec(
            select(PlanDay)
            .where(PlanDay.user_id == user.id, PlanDay.week == week)
            .order_by(PlanDay.day_offset)
        ).all()
        return {"week": week, "items": [_to_dict(d) for d in rows]}


@router.post("/generate")
def generate(user: User = Depends(auth.get_current_user)):
    if not llm.status(user)["configured"]:
        raise HTTPException(400, "LLM nicht konfiguriert: " + (llm.status(user)["error"] or ""))
    try:
        created = plan_service.generate_plan(user.id)
        return {"ok": True, "week": created[0].week if created else None, "count": len(created)}
    except llm.LlmError as exc:
        raise HTTPException(502, str(exc))


@router.post("/{plan_id}/toggle")
def toggle(plan_id: int, user: User = Depends(auth.get_current_user)):
    with db.session() as s:
        d = s.get(PlanDay, plan_id)
        if not d or d.user_id != user.id:
            raise HTTPException(404, "Plan-Tag nicht gefunden")
        d.done = not d.done
        s.add(d)
        s.commit()
        return _to_dict(d)
