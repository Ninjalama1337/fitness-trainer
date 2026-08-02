from datetime import date, datetime, timedelta

from sqlmodel import select

from . import auth, db, llm
from .models import Activity, HealthDay, PlanDay, Suggestion
from .sports import SPORT_LABELS

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def iso_week(d: date) -> str:
    return f"{d.isocalendar().year}-W{d.isocalendar().week:02d}"


def build_context(user_id: int, days: int = 14) -> dict:
    since = datetime.now() - timedelta(days=days)
    with db.session() as s:
        acts = s.exec(
            select(Activity).where(Activity.user_id == user_id, Activity.start_time >= since).order_by(Activity.start_time.desc())
        ).all()
        health = s.exec(select(HealthDay).where(HealthDay.user_id == user_id)).all()
    health_by_date = {h.date: h for h in health}
    weekly: dict[str, dict] = {}
    for a in acts:
        w = iso_week(a.start_time.date())
        wk = weekly.setdefault(
            w,
            {
                "sessions": 0,
                "running_km": 0.0,
                "cycling_km": 0.0,
                "strength_count": 0,
                "calories": 0.0,
            },
        )
        wk["sessions"] += 1
        if a.sport == "running" and a.distance_km:
            wk["running_km"] += a.distance_km
        elif a.sport == "cycling" and a.distance_km:
            wk["cycling_km"] += a.distance_km
        elif a.sport == "strength":
            wk["strength_count"] += 1
        wk["calories"] += a.calories or 0

    recent = []
    for a in acts[:10]:
        recent.append(
            {
                "datum": a.start_time.strftime("%d.%m. %H:%M"),
                "sport": SPORT_LABELS.get(a.sport, a.sport),
                "dauer_min": round(a.duration_seconds / 60),
                "distanz_km": a.distance_km,
                "avg_hr": a.avg_hr,
                "kalorien": a.calories,
            }
        )
    sleep_vals = [h.sleep_seconds for h in health if h.sleep_seconds]
    cal_vals = [h.active_calories for h in health if h.active_calories]
    return {
        "wochen": [
            {**wk, "woche": w, "lauf_km_gerundet": round(wk["running_km"], 1)}
            for w, wk in weekly.items()
        ],
        "letzte_aktivitaeten": recent,
        "schnitt_schlaf_h": round(sum(sleep_vals) / len(sleep_vals) / 3600, 1)
        if sleep_vals
        else None,
        "schnitt_kalorien": round(sum(cal_vals) / len(cal_vals)) if cal_vals else None,
        "sportarten": ["laufen", "radfahren", "kraft"],
    }


def generate_plan(user_id: int) -> list[PlanDay]:
    ctx = build_context(user_id)
    system = (
        "Du bist ein erfahrener Sportwissenschaftler und Trainer. "
        "Erstelle Trainingspläne auf Basis von Garmin-Daten. "
        "Antworte NUR mit validem JSON, kein anderer Text."
    )
    user = f"""Erstelle einen 7-Tage-Trainingsplan für die kommende Woche (Montag bis Sonntag).

Trainingsdaten (letzte 14 Tage):
{json_dumps(ctx, indent=2)}

Regeln:
- Mischung aus Laufen, Radfahren und Kraft (kein Krafttag wenn der Nutzer nur laufen/radeln macht, dann Pause einbauen)
- Berücksichtige Belastung: nach harten Wochen regenerativ, sonst progressiv
- 1-2 Ruhetage, max. 1 Intervall-Einheit pro Woche
- Kurze, konkrete Beschreibungen mit Dauer/Umfang (z.B. '45 min locker, Zone 2')

Antworte als JSON:
{{"plan": [{{"tag": 0, "sport": "running|cycling|strength|rest", "fokus": "Kurzer Titel", "beschreibung": "Konkrete Anweisung", "steps": [{{"typ": "warmup|interval|recovery|cooldown|rest", "dauer_min": 10, "zone": 3}}]}}]}}
"tag" ist 0=Montag bis 6=Sonntag. Nur Tage mit sport != rest und sport != strength brauchen steps (in Minuten, zone optional 1-5, bei Intervallen auch rest-steps dazwischen). Bei sport == rest ODER strength: "steps": null."""
    db_user = auth.get_user(user_id)
    try:
        result = llm.chat_json(system, user, db_user)
    except llm.LlmError:
        raise
    plan_items = result.get("plan") or result.get("days") or []
    week = iso_week(date.today() + timedelta(days=7))
    with db.session() as s:
        for old in s.exec(select(PlanDay).where(PlanDay.user_id == user_id, PlanDay.week == week)).all():
            s.delete(old)
        created = []
        for item in plan_items:
            steps = item.get("steps")
            day = PlanDay(
                user_id=user_id,
                week=week,
                day_offset=int(item.get("tag", 0)),
                sport=str(item.get("sport", "rest")),
                focus=str(item.get("fokus", item.get("focus", ""))),
                description=str(item.get("beschreibung", item.get("description", ""))),
                steps=_clean_steps(steps),
            )
            s.add(day)
            created.append(day)
        s.commit()
        for d in created:
            s.refresh(d)
        return created


def generate_suggestion(user_id: int) -> Suggestion:
    ctx = build_context(user_id)
    system = (
        "Du bist ein erfahrener Lauftrainer und Sportwissenschaftler. "
        "Gib eine konkrete Trainingsempfehlung für den heutigen Tag. "
        "Antworte NUR mit validem JSON, kein anderer Text."
    )
    user = f"""Empfiehl das heutige Training anhand dieser Daten:
{json_dumps(ctx, indent=2)}

Antworte als JSON:
{{"titel": "Kurzer Titel", "sport": "running|cycling", "begruendung": "2-3 Sätze warum (Datenbezug)", "training": "Konkrete Anweisung mit Dauer und Intensität", "steps": [{{"typ": "warmup|interval|recovery|cooldown|rest", "dauer_min": 10, "zone": 3}}]}}
"steps" muss die Trainingseinheit als strukturierte Schritte beschreiben (in Minuten, zone 1-5 optional, null wenn kein Ziel). steps darf nicht leer sein."""
    db_user = auth.get_user(user_id)
    try:
        result = llm.chat_json(system, user, db_user)
    except llm.LlmError:
        raise
    with db.session() as s:
        sug = Suggestion(
            user_id=user_id,
            title=str(result.get("titel", "")),
            sport=str(result.get("sport", "running")),
            rationale=str(result.get("begruendung", "")),
            workout=str(result.get("training", "")),
            steps=_clean_steps(result.get("steps")),
            context=ctx,
        )
        s.add(sug)
        s.commit()
        s.refresh(sug)
        return sug


def json_dumps(obj, indent=2) -> str:
    import json

    return json.dumps(obj, indent=indent, ensure_ascii=False, default=str)


def _clean_steps(steps) -> list | None:
    """Validiert LLM-Steps auf das interne Format."""
    if not isinstance(steps, list) or not steps:
        return None
    valid_types = {"warmup", "interval", "recovery", "cooldown", "rest"}
    cleaned = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        typ = str(s.get("typ") or s.get("type") or "").lower()
        if typ not in valid_types:
            continue
        try:
            dauer = float(s.get("dauer_min") or s.get("duration_min") or 0)
        except (TypeError, ValueError):
            dauer = 0
        if dauer <= 0:
            continue
        zone = s.get("zone")
        try:
            zone = int(zone) if zone is not None else None
        except (TypeError, ValueError):
            zone = None
        if zone is not None and not 1 <= zone <= 5:
            zone = None
        cleaned.append({"typ": typ, "dauer_min": round(dauer, 1), "zone": zone})
    return cleaned or None
