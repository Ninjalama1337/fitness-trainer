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
{{"plan": [{{"tag": 0, "sport": "running|cycling|strength|rest", "fokus": "Kurzer Titel", "beschreibung": "Konkrete Anweisung", "steps": [{{"typ": "warmup|interval|recovery|cooldown|rest", "dauer_min": 10, "zone": 3}}], "kraft_steps": null}}]}}
"tag" ist 0=Montag bis 6=Sonntag. Nur Tage mit sport != rest brauchen steps (in Minuten, zone optional 1-5, bei Intervallen auch rest-steps dazwischen). Bei sport == rest: "steps": null. Bei sport == strength: "steps": null und stattdessen "kraft_steps": [{{"uebung": "Kniebeuge", "saetze": 3, "wiederholungen": 10, "gewicht_kg": 60}}] mit 5-8 verschiedenen Übungen (Krafttage mit Wiederholungszahlen und Gewichten planen)."""
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
                kraft_steps=_clean_strength_steps(item.get("kraft_steps")),
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


def create_race_plan(
    user_id: int, target_date: date, distance_km: float, name: str = ""
) -> dict:
    """Erstellt einen mehrwöchigen KI-Plan rückwärts vom Zieltermin."""
    from .models import RaceGoal

    today = date.today()
    days_left = (target_date - today).days
    if days_left < 14:
        raise ValueError("Das Ziel muss mindestens 2 Wochen in der Zukunft liegen")
    weeks = min(16, max(3, (days_left + 6) // 7))

    ctx = build_context(user_id)
    system = (
        "Du bist ein erfahrener Wettkampf-Trainer für Läufer. "
        "Erstelle progressive Wochenpläne rückwärts vom Zieltermin. "
        "Antworte NUR mit validem JSON, kein anderer Text."
    )
    user = f"""Erstelle einen {weeks}-Wochen-Trainingsplan für den Wettkampf "{name or 'Wettkampf'}" über {distance_km:g} km am {target_date.isoformat()}.

Trainingsdaten (letzte 14 Tage):
{json_dumps(ctx, indent=2)}

Regeln:
- Woche 1 ist die aktuelle Woche (leicht), die Belastung steigt progressiv, letzte Woche = Tapering (wenig Umfang)
- 1-2 Ruhetage pro Woche, max. 1 Qualitätseinheit pro Woche
- Der Wettkampf-Tag ist in der letzten Woche: tag = Wochentag des Zieltermins ({target_date.strftime('%A')} = {target_date.weekday()})
- Kurze konkrete Beschreibungen mit Dauer/Umfang (z.B. '45 min locker, Zone 2')

Antworte als JSON:
{{"weeks": [{{"woche": 1, "fokus": "Wochentitel", "tage": [{{"tag": 0, "sport": "running|cycling|strength|rest", "fokus": "Titel", "beschreibung": "Anweisung", "steps": [{{"typ": "warmup|interval|recovery|cooldown|rest", "dauer_min": 10, "zone": 3}}], "kraft_steps": null}}]}}]}}
"tag" 0=Montag bis 6=Sonntag. Nur running/cycling-Tage brauchen steps; rest: steps null. Bei strength-Tagen: steps null und "kraft_steps": [{{"uebung": "Kniebeuge", "saetze": 3, "wiederholungen": 10, "gewicht_kg": 60}}] mit 5-8 Übungen."""
    db_user = auth.get_user(user_id)
    try:
        result = llm.chat_json(system, user, db_user, max_tokens=16000)
    except llm.LlmError:
        raise
    race = RaceGoal(
        user_id=user_id,
        name=name or "Wettkampf",
        target_date=target_date,
        distance_km=distance_km,
    )
    with db.session() as s:
        # Alten Rennplan ersetzen
        old = s.exec(
            select(RaceGoal).where(RaceGoal.user_id == user_id)
        ).all()
        for g in old:
            for d in s.exec(
                select(PlanDay).where(
                    PlanDay.user_id == user_id, PlanDay.race_goal_id == g.id
                )
            ).all():
                s.delete(d)
            s.delete(g)
        s.add(race)
        s.commit()
        s.refresh(race)

        this_monday = today - timedelta(days=today.weekday())
        created = 0
        week_items = result.get("weeks") or []
        for wi in week_items:
            week_no = int(wi.get("woche", 1))
            monday = this_monday + timedelta(days=(week_no - 1) * 7)
            for td in wi.get("tage", []):
                day_offset = int(td.get("tag", 0)) % 7
                day_date = monday + timedelta(days=day_offset)
                is_race_day = (
                    week_no == weeks and day_date == target_date
                )
                focus = td.get("fokus") or td.get("focus", "")
                if is_race_day:
                    sport = "running"
                    focus = f"🏁 WETTKAMPF: {race.name} ({distance_km:g} km)"
                    description = f"Rennen über {distance_km:g} km – gut einlaufen, dein Rennen laufen, auslaufen."
                    steps = [
                        {"typ": "warmup", "dauer_min": 15, "zone": 2},
                        {"typ": "interval", "dauer_min": int(distance_km * 60 / 10), "zone": 4},
                        {"typ": "cooldown", "dauer_min": 10, "zone": 2},
                    ]
                else:
                    sport = str(td.get("sport", "rest"))
                    description = td.get("beschreibung") or td.get("description", "")
                    steps = _clean_steps(td.get("steps"))
                day = PlanDay(
                    user_id=user_id,
                    week=iso_week(day_date),
                    day_offset=day_offset,
                    sport=sport,
                    focus=focus,
                    description=description,
                    steps=steps,
                    kraft_steps=_clean_strength_steps(td.get("kraft_steps")),
                    race_goal_id=race.id,
                )
                s.add(day)
                created += 1
        s.commit()
        race_id = race.id
        race_name = race.name
        race_target = race.target_date.isoformat()
        race_distance = race.distance_km
    return {
        "ok": True,
        "race_id": race_id,
        "name": race_name,
        "target_date": race_target,
        "distance_km": race_distance,
        "weeks": weeks,
        "days": created,
    }
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


def _clean_strength_steps(steps) -> list | None:
    """Validiert Kraft-Steps: [{uebung, saetze, wiederholungen, gewicht_kg}]."""
    if not isinstance(steps, list) or not steps:
        return None
    cleaned = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        uebung = str(s.get("uebung") or s.get("exercise") or s.get("name") or "").strip()
        if not uebung:
            continue
        try:
            saetze = int(s.get("saetze") or s.get("sets") or 3)
            wiederholungen = int(s.get("wiederholungen") or s.get("reps") or 10)
        except (TypeError, ValueError):
            continue
        gewicht = s.get("gewicht_kg") or s.get("weight_kg")
        try:
            gewicht = round(float(gewicht), 1) if gewicht is not None else None
        except (TypeError, ValueError):
            gewicht = None
        cleaned.append(
            {
                "uebung": uebung,
                "saetze": max(1, saetze),
                "wiederholungen": max(1, wiederholungen),
                "gewicht_kg": gewicht,
            }
        )
    return cleaned or None


def json_dumps(obj, indent=2) -> str:
    import json

    return json.dumps(obj, indent=indent, ensure_ascii=False, default=str)
