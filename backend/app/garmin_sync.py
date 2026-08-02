import logging
import shutil
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import garminconnect

from . import config, crypto, db
from .db import get_activity_by_garmin_id
from .models import Activity, GarminCred, HealthDay, SyncState
from .sports import map_sport, normalize_zones

logger = logging.getLogger("fitness")


class GarminError(Exception):
    pass


class GarminMfaRequired(GarminError):
    def __init__(self):
        super().__init__(
            "Garmin verlangt einen Bestätigungscode (2FA). Bitte Code eingeben."
        )


class GarminNotConfigured(GarminError):
    def __init__(self):
        super().__init__(
            "Garmin-Login fehlt. Bitte unter Einstellungen deine Garmin-Daten eingeben."
        )


def token_dir(user_id: int) -> Path:
    return config.DATA_DIR / "garmin_tokens" / str(user_id)


def db_credentials(user_id: int) -> tuple[str, str] | None:
    with db.session() as s:
        row = s.get(GarminCred, user_id)
        if row and row.email and row.password:
            return row.email, crypto.decrypt_secret(row.password)
    return None


def is_configured(user_id: int) -> bool:
    return db_credentials(user_id) is not None


def masked_email(user_id: int) -> str | None:
    creds = db_credentials(user_id)
    if not creds:
        return None
    email = creds[0]
    if "@" in email:
        name, dom = email.split("@", 1)
        return f"{name[:2]}***@{dom}"
    return email[:2] + "***"


def save_credentials(user_id: int, email: str, password: str) -> None:
    with db.session() as s:
        row = s.get(GarminCred, user_id) or GarminCred(user_id=user_id)
        row.email = email.strip()
        row.password = crypto.encrypt_secret(password)
        s.add(row)
        s.commit()
    old = config.DATA_DIR / "garmin_tokens"
    if old.exists() and old.is_dir() and not token_dir(user_id).exists():
        try:
            shutil.move(str(old), str(token_dir(user_id)))
        except Exception:
            pass
    if token_dir(user_id).exists():
        shutil.rmtree(token_dir(user_id), ignore_errors=True)


def _api(user_id: int, mfa_code: str | None = None) -> garminconnect.Garmin:
    creds = db_credentials(user_id)
    if not creds:
        raise GarminNotConfigured()
    email, password = creds

    def prompt_mfa() -> str:
        if mfa_code:
            return mfa_code
        raise GarminMfaRequired()

    return garminconnect.Garmin(
        email, password, prompt_mfa=prompt_mfa, retry_attempts=2
    )


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(value)[:26], fmt)
        except ValueError:
            continue
    return None


def _normalize_activity(a: dict) -> dict:
    sport = map_sport(a.get("sportType"))
    start = _parse_time(a.get("startTimeLocal") or a.get("startTimeGMT"))
    if not start:
        raise ValueError(f"Aktivitaet ohne Startzeit: {a.get('activityId')}")
    distance_m = a.get("distance") or 0
    distance_km = round(distance_m / 1000, 3) if distance_m else None
    duration = a.get("duration") or a.get("elapsedDuration") or 0
    avg_speed_ms = a.get("averageSpeed") or 0
    avg_speed_kmh = round(avg_speed_ms * 3.6, 2) if avg_speed_ms else None
    avg_pace = (
        round(1000 / (avg_speed_ms * 60), 2) if avg_speed_ms and sport == "running" else None
    )
    return {
        "garmin_id": str(a.get("activityId", "")),
        "name": a.get("activityName") or sport,
        "sport": sport,
        "start_time": start,
        "duration_seconds": float(duration or 0),
        "distance_km": distance_km,
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "calories": a.get("calories"),
        "avg_pace_min_km": avg_pace,
        "avg_speed_kmh": avg_speed_kmh,
        "hr_zones": None,
        "source": "garmin",
        "extra": a.get("details") or None,
    }


def _api_get(api, call, retries: int = 2):
    """Führt einen Garmin-API-Call mit kurzem Backoff aus (429/5xx drosseln)."""
    for attempt in range(retries + 1):
        try:
            result = call()
            time.sleep(0.35)  # freundlich zu Garmin bleiben
            return result
        except Exception as exc:
            text = str(exc)
            if ("429" in text or "rate limit" in text.lower()) and attempt < retries:
                logger.info("Garmin 429 erkannt, warte %ss (Versuch %s/2)…", 2 + attempt * 2, attempt + 1)
                time.sleep(2 + attempt * 2)
                continue
            raise
    raise RuntimeError("unreachable")


def _fetch_hr_zones(api, garmin_id: str) -> dict | None:
    """Zonen-Sekunden aus der Aktivität holen – toleriert mehrere Antwortformate."""
    try:
        zones = _api_get(api, lambda: api.get_activity_hr_in_timezones(str(garmin_id)))
        if isinstance(zones, dict):
            values = (
                zones.get("hrTimeInZones")
                or zones.get("zones")
                or zones.get("hrTimeInZonesByActivity")
            )
            if values is None and "hrTimeInZones" in str(zones.keys()):
                pass
            normalized = normalize_zones(values)
            if normalized:
                return normalized
            logger.info("get_activity_hr_in_timezones unerwartetes Format: %s", str(zones)[:200])
    except Exception as exc:
        logger.info("Zone-Fetch (timezones) fehlgeschlagen: %s", str(exc)[:150])
    try:
        details = _api_get(api, lambda: api.get_activity(str(garmin_id)))
        values = (
            details.get("hrTimeInZones")
            if isinstance(details, dict)
            else None
        )
        normalized = normalize_zones(values)
        if normalized:
            return normalized
        logger.info("get_activity Details: hrTimeInZones=%s (Typ %s)", values, type(values).__name__)
    except Exception as exc:
        logger.info("Zone-Fetch (details) fehlgeschlagen: %s", str(exc)[:150])
    return None


def sync_garmin(user_id: int, limit: int = 50, mfa_code: str | None = None) -> dict:
    api = _api(user_id, mfa_code=mfa_code)
    tdir = token_dir(user_id)
    try:
        tdir.mkdir(parents=True, exist_ok=True)
        api.login(tokenstore=str(tdir))
    except GarminMfaRequired:
        raise
    except Exception as exc:
        raise GarminError(f"Garmin-Login fehlgeschlagen: {exc}") from exc

    imported = 0
    skipped = 0
    with db.session() as s:
        activities = _api_get(api, lambda: api.get_activities(0, limit))
        logger.info("Garmin-Antwort: %s Aktivitaeten fuer User %s", len(activities), user_id)
        for a in activities:
            try:
                row = _normalize_activity(a)
            except (ValueError, TypeError):
                skipped += 1
                continue
            existing = get_activity_by_garmin_id(s, user_id, row["garmin_id"])
            if existing and existing.start_time == row["start_time"] and existing.hr_zones:
                skipped += 1
                continue
            if existing:
                row["hr_zones"] = existing.hr_zones or _fetch_hr_zones(api, row["garmin_id"])
            else:
                row["hr_zones"] = _fetch_hr_zones(api, row["garmin_id"])
            logger.info(
                "Aktivitaet %s: sport=%s km=%s avg_hr=%s zones=%s",
                row["garmin_id"], row["sport"], row["distance_km"],
                row["avg_hr"], bool(row["hr_zones"]),
            )
            if existing:
                for k, v in row.items():
                    setattr(existing, k, v)
            else:
                s.add(Activity(user_id=user_id, **row))
            imported += 1
        _sync_health(s, user_id, api, days=14)
        s.commit()
    return {"imported": imported, "skipped": skipped}


def _sync_health(s, user_id: int, api, days: int = 14) -> None:
    today = date.today()
    for offset in range(days):
        day = today - timedelta(days=offset)
        existing = s.get(HealthDay, (user_id, day))
        if existing and (existing.sleep_seconds or existing.active_calories or existing.steps):
            continue
        day_str = day.isoformat()
        entry = existing or HealthDay(user_id=user_id, date=day)
        try:
            stats = _api_get(api, lambda: api.get_stats(day_str))
            if isinstance(stats, dict):
                entry.resting_hr = stats.get("restHR")
                entry.steps = stats.get("steps")
                entry.stress_avg = stats.get("stressAvg")
                entry.active_calories = stats.get("activeKilocalories")
        except Exception:
            pass
        try:
            sleep = _api_get(api, lambda: api.get_sleep_data(day_str))
            if isinstance(sleep, dict) and sleep.get("sleepLevels"):
                levels = sleep["sleepLevels"]["levels"] or []
                secs = sum(int(l.get("seconds") or 0) for l in levels)
                deep = sum(
                    int(l.get("seconds") or 0)
                    for l in levels
                    if (l.get("name") or "").lower() in ("deep", "deep_sleep")
                )
                entry.sleep_seconds = secs or None
                entry.deep_sleep_seconds = deep or None
        except Exception:
            pass
        if entry.sleep_seconds or entry.active_calories or entry.steps:
            s.add(entry)


def update_sync_state(user_id: int, status: str, message: str) -> None:
    with db.session() as s:
        state = s.get(SyncState, user_id) or SyncState(user_id=user_id)
        state.status = status
        state.message = message
        state.last_sync = datetime.now()
        s.add(state)
        s.commit()
