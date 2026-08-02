import hashlib
import io
from datetime import datetime

import fitparse

from . import db
from .db import get_activity_by_garmin_id
from .models import Activity
from .sports import compute_zones_from_hr, estimate_max_hr, map_sport


class FitError(Exception):
    pass


def _msg_values(msg):
    out = {}
    for field in msg.fields:
        if field.value is None:
            continue
        try:
            out[field.name] = field.value
        except Exception:
            continue
    return out


def parse_fit(data: bytes) -> dict:
    try:
        fitfile = fitparse.FitFile(io.BytesIO(data))
    except Exception as exc:
        raise FitError(f"Kein valides FIT-File: {exc}") from exc

    sport = "other"
    start = None
    total_distance = None
    total_timer = None
    total_calories = None
    heart_rates: list[float] = []
    last_record_ts = None
    first_record_ts = None
    distances: list[float] = []
    speeds: list[float] = []
    avgs: list[float] = []

    for msg in fitfile.get_messages():
        m = _msg_values(msg)
        if msg.name == "sport" and not sport or sport == "other":
            st = m.get("sport")
            if isinstance(st, bytes):
                st = st.decode(errors="ignore")
            if st:
                sport = map_sport(str(st).lower())
        elif msg.name == "activity":
            start = m.get("timestamp") or start
            total_distance = m.get("total_distance", total_distance)
            total_timer = m.get("total_timer_time", total_timer)
            total_calories = m.get("total_calories", total_calories)
        elif msg.name == "session":
            start = m.get("start_time") or start
            total_distance = m.get("total_distance", total_distance)
            total_timer = m.get("total_timer_time", total_timer)
            total_calories = m.get("total_calories", total_calories)
        elif msg.name == "record":
            ts = m.get("timestamp")
            if ts:
                first_record_ts = first_record_ts or ts
                last_record_ts = ts
            hr = m.get("heart_rate")
            if hr is not None:
                try:
                    heart_rates.append(float(hr))
                except (TypeError, ValueError):
                    pass
            if m.get("distance") is not None:
                distances.append(float(m["distance"]))
            if m.get("speed") is not None:
                speeds.append(float(m["speed"]))
            avg = m.get("enhanced_speed") or m.get("speed")
            if avg is not None:
                try:
                    avgs.append(float(avg))
                except (TypeError, ValueError):
                    pass

    if not start:
        raise FitError("Keine Startzeit in der FIT-Datei gefunden")

    distance_m = total_distance or (distances[-1] if distances else 0)
    duration = total_timer
    if not duration and first_record_ts and last_record_ts:
        duration = (last_record_ts - first_record_ts).total_seconds()

    avg_hr = round(sum(heart_rates) / len(heart_rates), 1) if heart_rates else None
    max_hr = max(heart_rates) if heart_rates else None
    avg_speed = sum(avgs) / len(avgs) if avgs else None
    distance_km = round(distance_m / 1000, 3) if distance_m else None

    garmin_id = (
        "fit-"
        + hashlib.sha1(
            f"{start.isoformat()}|{sport}|{distance_km}|{duration}".encode()
        ).hexdigest()[:16]
    )

    return {
        "garmin_id": garmin_id,
        "name": sport,
        "sport": sport,
        "start_time": start,
        "duration_seconds": float(duration or 0),
        "distance_km": distance_km,
        "avg_hr": avg_hr,
        "max_hr": max_hr,
        "calories": total_calories,
        "avg_pace_min_km": (
            round(1000 / (avg_speed * 60), 2)
            if avg_speed and sport == "running"
            else None
        ),
        "avg_speed_kmh": round(avg_speed * 3.6, 2) if avg_speed else None,
        "hr_zones": compute_zones_from_hr(heart_rates, estimate_max_hr()),
        "source": "fit",
        "extra": None,
    }


def import_fit(user_id: int, data: bytes) -> dict:
    row = parse_fit(data)
    with db.session() as s:
        existing = get_activity_by_garmin_id(s, user_id, row["garmin_id"])
        if existing:
            return {"imported": False, "skipped": True, "activity": row}
        s.add(Activity(user_id=user_id, **row))
        s.commit()
        return {"imported": True, "skipped": False, "activity": row}
