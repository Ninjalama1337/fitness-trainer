from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select

from .. import auth, llm, plan_service
from ..db import session
from ..models import User, WeekSummary

router = APIRouter(prefix="/api/weekly-summary", tags=["weekly-summary"])


@router.get("")
def get_summary(user: User = Depends(auth.get_current_user)):
    week = plan_service.iso_week(__import__("datetime").date.today())
    with session() as s:
        row = s.exec(
            select(WeekSummary).where(
                WeekSummary.user_id == user.id, WeekSummary.week == week
            )
        ).first()
        if not row:
            return {"ok": False}
        return {
            "ok": True,
            "week": row.week,
            "summary": row.summary,
            "improvement": row.improvement,
            "created_at": row.created_at.isoformat(),
        }


@router.post("/generate")
def generate_summary(user: User = Depends(auth.get_current_user)):
    if not llm.status(user)["configured"]:
        raise HTTPException(400, "LLM nicht konfiguriert")
    try:
        return {"ok": True, **plan_service.generate_week_summary(user.id)}
    except llm.LlmError as exc:
        raise HTTPException(502, str(exc))
