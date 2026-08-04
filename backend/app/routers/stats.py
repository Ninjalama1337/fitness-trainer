from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import select

from .. import auth
from ..db import session
from ..models import Activity, HealthDay, User
from ..plan_service import iso_week

router = APIRouter(prefix="/api/stats", tags=["stats"])

PERIOD_DAYS = {"week": 7, "month": 28, "year": 365}
PERIOD_LABELS = {
    "week": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
    "month": [],
    "year": [],
}
MONTH_NAMES = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


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
        day_calories = (
            round(h.active_calories)
            if h and h.active_calories
            else entry.get("calories", 0)
        )
        series.append(
            {
                "date": d.isoformat(),
                "running_km": round(entry.get("running_km", 0), 1),
                "cycling_km": round(entry.get("cycling_km", 0), 1),
                "sessions": entry.get("sessions", 0),
                "strength_count": entry.get("strength_count", 0),
                "calories": day_calories,
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


@router.get("/trend")
def trend(
    user: User = Depends(auth.get_current_user),
    period: str = Query(default="week", pattern="^(week|month|year)$"),
):
    """Verlauf: wöchentliche (7 Tage), monatliche (4 Wochen) oder jährliche (12 Monate) Aggregation."""
    days = PERIOD_DAYS[period]
    since = datetime.now() - timedelta(days=days)
    since_day = date.today() - timedelta(days=days)
    with session() as s:
        acts = s.exec(
            select(Activity).where(Activity.user_id == user.id, Activity.start_time >= since)
        ).all()
        health = s.exec(
            select(HealthDay).where(HealthDay.user_id == user.id, HealthDay.date >= since_day)
        ).all()

    def bucket_key(d: date) -> str:
        if period == "week":
            return d.isoformat()
        if period == "month":
            return iso_week(d)
        return f"{d.year:04d}-{d.month:02d}"

    def bucket_label(key: str) -> str:
        if period == "week":
            return ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"][date.fromisoformat(key).weekday()]
        if period == "month":
            return "W" + key.split("-W")[1]
        _, m = key.split("-")
        return MONTH_NAMES[int(m) - 1]

    buckets: dict[str, dict] = {}
    for a in acts:
        key = bucket_key(a.start_time.date())
        b = buckets.setdefault(
            key,
            {
                "running_km": 0.0,
                "cycling_km": 0.0,
                "strength_count": 0,
                "sessions": 0,
                "calories": 0.0,
                "sleep_h": None,
                "resting_hr": None,
                "hrv": None,
            },
        )
        b["sessions"] += 1
        if a.sport == "running" and a.distance_km:
            b["running_km"] += a.distance_km
        elif a.sport == "cycling" and a.distance_km:
            b["cycling_km"] += a.distance_km
        elif a.sport == "strength":
            b["strength_count"] += 1
        b["calories"] += a.calories or 0

    for h in health:
        key = bucket_key(h.date)
        b = buckets.setdefault(
            key,
            {
                "running_km": 0.0,
                "cycling_km": 0.0,
                "strength_count": 0,
                "sessions": 0,
                "calories": 0.0,
                "sleep_h": None,
                "resting_hr": None,
                "hrv": None,
            },
        )
        if h.sleep_seconds:
            b["sleep_h"] = round(h.sleep_seconds / 3600, 1)
        if h.resting_hr:
            b["resting_hr"] = round(h.resting_hr)
        if h.hrv_avg:
            b["hrv"] = round(h.hrv_avg, 1)

    ordered: list[dict] = []
    empty = {
        "running_km": 0.0,
        "cycling_km": 0.0,
        "strength_count": 0,
        "sessions": 0,
        "calories": 0.0,
        "sleep_h": None,
        "resting_hr": None,
        "hrv": None,
    }

    def bucket(buckets: dict, key: str) -> dict:
        return {**empty, **(buckets.get(key) or {})}

    if period == "week":
        for i in range(7):
            d = date.today() - timedelta(days=6 - i)
            key = bucket_key(d)
            ordered.append({"label": bucket_label(key), "key": key, **bucket(buckets, key)})
    elif period == "month":
        # letzte 4 ISO-Wochen (aktuell rückwärts)
        start = date.today() - timedelta(days=21)
        seen = set()
        current = start
        week_keys = []
        while len(week_keys) < 4:
            wk = iso_week(current)
            if wk not in seen:
                week_keys.append(wk)
                seen.add(wk)
            current += timedelta(days=7)
        for wk in week_keys:
            ordered.append({"label": bucket_label(wk), "key": wk, **bucket(buckets, wk)})
    else:
        start = date.today().replace(day=1)
        for i in range(12):
            y = start.year - (11 - i) // 12
            m = start.month - ((11 - i) % 12)
            if m < 1:
                m += 12
                y -= 1
            key = f"{y:04d}-{m:02d}"
            ordered.append({"label": bucket_label(key), "key": key, **bucket(buckets, key)})

    totals = {
        "sessions": sum(b["sessions"] for b in ordered),
        "running_km": round(sum(b["running_km"] for b in ordered), 1),
        "cycling_km": round(sum(b["cycling_km"] for b in ordered), 1),
        "strength_count": sum(b["strength_count"] for b in ordered),
        "calories": sum(b["calories"] for b in ordered),
    }
    sleeps = [b["sleep_h"] for b in ordered if b["sleep_h"]]
    rests = [b["resting_hr"] for b in ordered if b["resting_hr"]]
    hrvs = [b["hrv"] for b in ordered if b["hrv"]]
    totals["avg_sleep_h"] = round(sum(sleeps) / len(sleeps), 1) if sleeps else None
    totals["avg_resting_hr"] = round(sum(rests) / len(rests)) if rests else None
    totals["avg_hrv"] = round(sum(hrvs) / len(hrvs), 1) if hrvs else None

    return {"period": period, "buckets": ordered, "totals": totals}


@router.get("/pbs")
def personal_bests(user: User = Depends(auth.get_current_user)):
    """Beste Leistungen für 5k/10k/Halbmarathon/Marathon (Gesamtpace der Einheit)."""
    with session() as s:
        runs = s.exec(
            select(Activity).where(
                Activity.user_id == user.id,
                Activity.sport == "running",
                Activity.distance_km.isnot(None),
            )
        ).all()
    targets = [(5.0, "5k"), (10.0, "10k"), (21.1, "Halbmarathon"), (42.2, "Marathon")]
    out = []
    for dist, label in targets:
        candidates = [a for a in runs if (a.distance_km or 0) >= dist * 0.98]
        if not candidates:
            continue
        best = min(
            candidates,
            key=lambda a: (
                (a.avg_pace_min_km or 0)
                if a.avg_pace_min_km
                else (a.duration_seconds / max(a.distance_km or 1, 0.001) / 60)
            ),
        )
        pace = best.avg_pace_min_km or (best.duration_seconds / max(best.distance_km or 1, 0.001) / 60)
        out.append(
            {
                "label": label,
                "distance_km": dist,
                "time_seconds": int(pace * 60 * dist),
                "pace_min_km": round(pace, 2),
                "date": best.start_time.date().isoformat(),
                "activity_name": best.name,
                "activity_distance_km": round(best.distance_km, 1),
            }
        )
    return {"items": out}


@router.get("/load")
def training_load(user: User = Depends(auth.get_current_user)):
    """Akute (7 Tage) und chronische (28 Tage) Trainingsbelastung + Verhältnis."""
    now = datetime.now()
    since_28 = now - timedelta(days=28)
    since_7 = now - timedelta(days=7)
    with session() as s:
        acts = s.exec(
            select(Activity).where(
                Activity.user_id == user.id,
                Activity.start_time >= since_28,
                Activity.training_load.isnot(None),
            )
        ).all()

    def load_since(since: datetime) -> float:
        return sum(a.training_load or 0 for a in acts if a.start_time >= since)

    acute = round(load_since(since_7), 1)
    chronic = round(load_since(since_28) / 4, 1)  # Wochenschnitt über 4 Wochen
    ratio = round(acute / chronic, 2) if chronic > 0 else None
    zone = "optimal"
    if ratio is not None:
        if ratio < 0.8:
            zone = "unterlastet"
        elif ratio > 1.5:
            zone = "überlastet"
        elif ratio > 1.3:
            zone = "erhöht"
    days_with_training = len({a.start_time.date() for a in acts})
    return {
        "acute_7d": acute,
        "chronic_28d": chronic,
        "ratio": ratio,
        "zone": zone,
        "training_days_28d": days_with_training,
        "loads": [
            {
                "date": a.start_time.date().isoformat(),
                "load": round(a.training_load or 0, 1),
                "sport": a.sport,
            }
            for a in sorted(acts, key=lambda x: x.start_time)
        ],
    }


@router.get("/weight")
def weight_series(
    user: User = Depends(auth.get_current_user),
    days: int = Query(default=90, le=365),
):
    """Gewicht + Körperfett als Tagesreihe (nur Tage mit Messwerten)."""
    since = date.today() - timedelta(days=days)
    with session() as s:
        health = s.exec(
            select(HealthDay).where(HealthDay.user_id == user.id, HealthDay.date >= since)
        ).all()
    points = [
        {
            "date": h.date.isoformat(),
            "weight_kg": h.weight_kg,
            "body_fat_pct": h.body_fat_pct,
        }
        for h in sorted(health, key=lambda x: x.date)
        if h.weight_kg is not None or h.body_fat_pct is not None
    ]
    return {"days": days, "points": points}


@router.get("/recovery")
def recovery_status(user: User = Depends(auth.get_current_user)):
    """Übertrainings-Erkennung: ACWR (Belastung), HFV-Trend, Ruhepuls-Trend."""
    now = datetime.now()
    today = date.today()
    with session() as s:
        acts = s.exec(
            select(Activity).where(
                Activity.user_id == user.id,
                Activity.start_time >= now - timedelta(days=28),
                Activity.training_load.isnot(None),
            )
        ).all()
        health = s.exec(
            select(HealthDay).where(HealthDay.user_id == user.id, HealthDay.date >= today - timedelta(days=28))
        ).all()

    load_7 = sum(a.training_load or 0 for a in acts if a.start_time >= now - timedelta(days=7))
    load_28 = sum(a.training_load or 0 for a in acts)
    acwr = round(load_7 / (load_28 / 4), 2) if load_28 > 0 else None

    def avg(vals: list[float]) -> float | None:
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    by_day = {h.date: h for h in health}
    days_7 = [today - timedelta(days=i) for i in range(7)]
    days_28 = [today - timedelta(days=i) for i in range(28)]

    hrv_7 = avg([by_day.get(d).hrv_avg for d in days_7 if by_day.get(d)])
    hrv_28 = avg([by_day.get(d).hrv_avg for d in days_28 if by_day.get(d)])
    rhr_7 = avg([by_day.get(d).resting_hr for d in days_7 if by_day.get(d)])
    rhr_28 = avg([by_day.get(d).resting_hr for d in days_28 if by_day.get(d)])

    hrv_delta = round((hrv_7 - hrv_28) * 100 / hrv_28, 1) if hrv_7 and hrv_28 else None
    rhr_delta = round(rhr_7 - rhr_28, 1) if rhr_7 and rhr_28 else None

    signals: list[dict] = []
    if acwr is not None:
        if acwr > 1.5:
            signals.append({"level": "warnung", "text": f"Belastung stark erhöht (ACWR {acwr}) – Regeneration einplanen"})
        elif acwr > 1.3:
            signals.append({"level": "achtung", "text": f"Belastung erhöht (ACWR {acwr}) – Umfang nicht weiter steigern"})
        elif acwr < 0.8:
            signals.append({"level": "achtung", "text": f"Belastung niedrig (ACWR {acwr}) – Form erhältst du nur mit Reizen"})
    if hrv_delta is not None and hrv_delta < -10:
        signals.append({"level": "warnung", "text": f"HFV 7 Tage {hrv_delta}% unter Baseline – Erholung oder Krankheit möglich"})
    if rhr_delta is not None and rhr_delta >= 5:
        signals.append({"level": "warnung", "text": f"Ruhepuls +{rhr_delta} bpm über Baseline – Regeneration dringend empfohlen"})

    status = "erholt"
    if any(sg["level"] == "warnung" for sg in signals):
        status = "warnung"
    elif any(sg["level"] == "achtung" for sg in signals):
        status = "achtung"

    return {
        "status": status,
        "acwr": acwr,
        "load_7d": round(load_7, 1),
        "load_28d_avg": round(load_28 / 4, 1),
        "hrv_7d": round(hrv_7, 1) if hrv_7 else None,
        "hrv_28d": round(hrv_28, 1) if hrv_28 else None,
        "hrv_delta_pct": hrv_delta,
        "resting_hr_7d": round(rhr_7, 1) if rhr_7 else None,
        "resting_hr_28d": round(rhr_28, 1) if rhr_28 else None,
        "resting_hr_delta": rhr_delta,
        "signals": signals,
    }


@router.get("/race-predictions")
def race_predictions(user: User = Depends(auth.get_current_user)):
    """Zeitprognose 5k/10k/HM/M nach Cameron aus dem besten vorhandenen Lauf."""
    with session() as s:
        runs = s.exec(
            select(Activity).where(
                Activity.user_id == user.id,
                Activity.sport == "running",
                Activity.distance_km.isnot(None),
                Activity.distance_km >= 1.0,
            )
        ).all()
    if not runs:
        return {"items": [], "base": None}
    def pace_of(a: Activity) -> float:
        return a.avg_pace_min_km or (a.duration_seconds / max(a.distance_km or 1, 0.001) / 60)
    fastest = min(runs, key=pace_of)
    base_pace = pace_of(fastest)
    base_km = fastest.distance_km

    targets = [
        ("5k", 5.0),
        ("10k", 10.0),
        ("Halbmarathon", 21.0975),
        ("Marathon", 42.195),
    ]
    items = []
    for label, dist in targets:
        time_s = base_pace * 60 * dist * (dist / base_km) ** 0.06
        items.append(
            {
                "label": label,
                "distance_km": dist,
                "time_seconds": int(time_s),
                "pace_min_km": round(time_s / 60 / dist, 2),
            }
        )
    return {
        "items": items,
        "base": {
            "activity_name": fastest.name,
            "distance_km": round(base_km, 1),
            "pace_min_km": round(base_pace, 2),
            "date": fastest.start_time.date().isoformat(),
        },
    }


@router.get("/heatmap")
def heatmap(
    user: User = Depends(auth.get_current_user),
    days: int = Query(default=365, le=400),
):
    """Aktivität pro Kalendertag (km + Sessions + Belastung) für den Jahreskalender."""
    since = datetime.now() - timedelta(days=days)
    with session() as s:
        acts = s.exec(
            select(Activity).where(Activity.user_id == user.id, Activity.start_time >= since)
        ).all()
    per_day: dict[str, dict] = {}
    for a in acts:
        key = a.start_time.date().isoformat()
        e = per_day.setdefault(key, {"km": 0.0, "sessions": 0, "load": 0.0})
        e["km"] += a.distance_km or 0
        e["sessions"] += 1
        e["load"] += a.training_load or 0
    series = []
    for i in range(days):
        d = (date.today() - timedelta(days=days - 1 - i)).isoformat()
        e = per_day.get(d, {"km": 0.0, "sessions": 0, "load": 0.0})
        series.append({"date": d, "km": round(e["km"], 1), "sessions": e["sessions"], "load": round(e["load"], 1)})
    return {"days": days, "series": series}


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
