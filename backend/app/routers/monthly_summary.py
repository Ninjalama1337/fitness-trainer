from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import select

from .. import auth, llm, plan_service
from ..db import session
from ..models import MonthSummary, User

router = APIRouter(prefix="/api/monthly-summary", tags=["monthly-summary"])


@router.get("")
def get_summary(
    user: User = Depends(auth.get_current_user),
    month: str | None = Query(default=None, pattern="^\\d{4}-\\d{2}$"),
):
    month = month or __import__("datetime").date.today().strftime("%Y-%m")
    with session() as s:
        row = s.get(MonthSummary, (user.id, month))
        if not row:
            return {"ok": False, "month": month}
        return {
            "ok": True,
            "month": row.month,
            "summary": row.summary,
            "advice": row.advice,
            "created_at": row.created_at.isoformat(),
        }


@router.post("/generate")
def generate_summary(user: User = Depends(auth.get_current_user)):
    if not llm.status(user)["configured"]:
        raise HTTPException(400, "LLM nicht konfiguriert")
    try:
        return {"ok": True, **plan_service.generate_month_summary(user.id)}
    except llm.LlmError as exc:
        raise HTTPException(502, str(exc))
