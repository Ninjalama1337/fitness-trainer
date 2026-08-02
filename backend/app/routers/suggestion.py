from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select

from .. import auth, db, llm, plan_service
from ..models import Suggestion, User

router = APIRouter(prefix="/api/suggestion", tags=["suggestion"])


def _to_dict(s: Suggestion) -> dict:
    return {
        "id": s.id,
        "created_at": s.created_at.isoformat(),
        "title": s.title,
        "sport": s.sport,
        "rationale": s.rationale,
        "workout": s.workout,
        "steps": s.steps,
        "garmin_workout_id": s.garmin_workout_id,
    }


@router.get("")
def get_latest(user: User = Depends(auth.get_current_user)):
    with db.session() as s:
        row = s.exec(
            select(Suggestion)
            .where(Suggestion.user_id == user.id)
            .order_by(Suggestion.created_at.desc())
            .limit(1)
        ).first()
        if not row:
            return {"ok": False, "message": "Noch kein Vorschlag vorhanden"}
        return {"ok": True, "suggestion": _to_dict(row)}


@router.post("/generate")
def generate(user: User = Depends(auth.get_current_user)):
    if not llm.status(user)["configured"]:
        raise HTTPException(400, "LLM nicht konfiguriert: " + (llm.status(user)["error"] or ""))
    try:
        sug = plan_service.generate_suggestion(user.id)
        return {"ok": True, "suggestion": _to_dict(sug)}
    except llm.LlmError as exc:
        raise HTTPException(502, str(exc))
