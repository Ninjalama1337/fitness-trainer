import logging
import shutil
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import garminconnect

from . import config, crypto, db
from .db import get_activity_by_garmin_id
from .models import Activity, GarminCred, HealthDay, SyncState
from .sports import map_sport, map_sport_id, normalize_zones

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
    sport = "other"
    for raw in (a.get("sportType"), a.get("activityType")):
        if raw:
            mapped = map_sport(raw)
            if mapped != "other":
                sport = mapped
                break
    else:
        sport = map_sport_id(a.get("sportTypeId"))
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
    zones = None
    for key in ("hrTimeInZone_1", "hrTimeInZone_2", "hrTimeInZone_3", "hrTimeInZone_4", "hrTimeInZone_5"):
        if a.get(key) is not None:
            zones = {
                f"zone{i + 1}": int(a.get(f"hrTimeInZone_{i + 1}", 0) or 0)
                for i in range(5)
            }
            break
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
        "training_load": a.get("activityTrainingLoad"),
        "hr_zones": zones,
        "source": "garmin",
        "extra": None,
    }


def _api_get(api, call, retries: int = 3):
    """Führt einen Garmin-API-Call mit Backoff aus (429/5xx drosseln)."""
    for attempt in range(retries + 1):
        try:
            result = call()
            time.sleep(0.6)  # freundlich zu Garmin bleiben
            return result
        except Exception as exc:
            text = str(exc)
            if ("429" in text or "rate limit" in text.lower()) and attempt < retries:
                wait = 2 + attempt * 3
                logger.info("Garmin 429 erkannt, warte %ss (Versuch %s/3)…", wait, attempt + 1)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("unreachable")


def _fetch_hr_zones(api, garmin_id: str) -> dict | None:
    """Zonen-Sekunden aus der Aktivität holen – toleriert mehrere Antwortformate."""
    try:
        zones = _api_get(api, lambda: api.get_activity_hr_in_timezones(str(garmin_id)))
        normalized = normalize_zones(zones)
        if normalized:
            return normalized
        logger.info("Zone-Fetch unerwartetes Format: %s", str(zones)[:200])
    except Exception as exc:
        logger.info("Zone-Fetch (timezones) fehlgeschlagen: %s", str(exc)[:150])
    try:
        details = _api_get(api, lambda: api.get_activity(str(garmin_id)))
        values = details.get("hrTimeInZones") if isinstance(details, dict) else None
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
            except (ValueError, TypeError) as exc:
                logger.warning("Aktivitaet uebersprungen (Normalisierung): %s", exc)
                skipped += 1
                continue
            try:
                existing = get_activity_by_garmin_id(s, user_id, row["garmin_id"])
                if (
                    existing
                    and existing.start_time == row["start_time"]
                    and existing.hr_zones
                    and existing.sport == row["sport"]
                    and existing.training_load is not None
                ):
                    skipped += 1
                    continue
                if existing:
                    row["hr_zones"] = existing.hr_zones or row["hr_zones"] or _fetch_hr_zones(api, row["garmin_id"])
                else:
                    row["hr_zones"] = row["hr_zones"] or _fetch_hr_zones(api, row["garmin_id"])
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
            except Exception as exc:
                logger.exception("Aktivitaet %s fehlgeschlagen", row.get("garmin_id"))
                skipped += 1
        _sync_health(s, user_id, api, days=14)
        s.commit()
    return {"imported": imported, "skipped": skipped}


def _user_timezone(api) -> str:
    """Leitet die Zeitzone des Users aus der neuesten Aktivität ab."""
    try:
        acts = api.get_activities(0, 3)
        for a in acts:
            tz = a.get("timeZoneId")
            if tz:
                return tz
    except Exception:
        pass
    return "UTC"


def _sync_health(s, user_id: int, api, days: int = 14) -> None:
    from zoneinfo import ZoneInfo

    tz_name = _user_timezone(api)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    today = datetime.now(tz).date()
    _sync_body_composition(s, user_id, api, today, days=min(days, 30))
    for offset in range(days):
        day = today - timedelta(days=offset)
        existing = s.get(HealthDay, (user_id, day))
        # Überspringen nur, wenn der Eintrag vollständig ist (Schlaf als Indikator),
        # sonst werden fehlende Werte (z.B. nach 429) nachgeholt.
        if existing and existing.sleep_seconds:
            continue
        day_str = day.isoformat()
        entry = existing or HealthDay(user_id=user_id, date=day)
        try:
            stats = _api_get(api, lambda: api.get_stats(day_str))
            if isinstance(stats, dict):
                entry.resting_hr = stats.get("restingHeartRate") or stats.get("restHR")
                entry.steps = stats.get("totalSteps") or stats.get("steps")
                entry.stress_avg = stats.get("averageStressLevel") or stats.get("stressAvg")
                entry.active_calories = (
                    stats.get("totalKilocalories")
                    or stats.get("burnedKilocalories")
                    or stats.get("activeKilocalories")
                    or stats.get("activeCalories")
                )
        except Exception as exc:
            logger.info("get_stats(%s) fehlgeschlagen: %s", day_str, str(exc)[:120])
        try:
            sleep = _api_get(api, lambda: api.get_sleep_data(day_str))
            if isinstance(sleep, dict):
                dto = sleep.get("dailySleepDTO") or {}
                secs = dto.get("sleepTimeSeconds")
                levels = sleep.get("sleepLevels")
                if not isinstance(levels, dict):
                    levels = {}
                if not secs:
                    raw_levels = levels.get("levels") or []
                    secs = sum(int(l.get("seconds") or 0) for l in raw_levels) if raw_levels else 0
                deep = None
                raw_levels = levels.get("levels") or []
                if raw_levels:
                    deep = sum(
                        int(l.get("seconds") or 0)
                        for l in raw_levels
                        if (l.get("name") or l.get("level") or "").lower() in ("deep", "deep_sleep")
                    )
                entry.sleep_seconds = secs or None
                entry.deep_sleep_seconds = deep or None
                hrv = sleep.get("avgOvernightHrv")
                if not hrv and isinstance(sleep.get("hrvData"), dict):
                    hrv = sleep["hrvData"].get("avgOvernightHrv")
                entry.hrv_avg = hrv or sleep.get("hrvAverage")
                entry.hrv_status = sleep.get("hrvStatus")
                if not entry.resting_hr:
                    entry.resting_hr = sleep.get("restingHeartRate")
                logger.info(
                    "Health %s: schlaf=%s hrv=%s ruhepuls=%s kcal=%s",
                    day_str,
                    round((secs or 0) / 3600, 2),
                    entry.hrv_avg,
                    entry.resting_hr,
                    entry.active_calories,
                )
        except Exception:
            logger.exception("get_sleep_data(%s) fehlgeschlagen", day_str)
        if entry.sleep_seconds or entry.active_calories or entry.steps or entry.hrv_avg or entry.resting_hr:
            s.add(entry)


def _sync_body_composition(s, user_id: int, api, today: date, days: int = 30) -> None:
    """Gewicht/Körperfett aus dem Garmin Weight-Service (ein Range-Call)."""
    try:
        start = (today - timedelta(days=days)).isoformat()
        body = _api_get(api, lambda: api.get_body_composition(start, today.isoformat()))
    except Exception as exc:
        logger.info("get_body_composition fehlgeschlagen: %s", str(exc)[:150])
        return
    if not isinstance(body, dict):
        return
    date_list = body.get("dateWeightList")
    if not isinstance(date_list, list):
        logger.info("Körperdaten unerwartetes Format: %s", str(body)[:150])
        return
    for item in date_list:
        if not isinstance(item, dict):
            continue
        try:
            day = date.fromisoformat(str(item.get("date", "")))
        except ValueError:
            continue
        entry = s.get(HealthDay, (user_id, day)) or HealthDay(user_id=user_id, date=day)
        if item.get("weight") is not None:
            entry.weight_kg = round(float(item["weight"]), 1)
        if item.get("bodyFat") is not None:
            entry.body_fat_pct = round(float(item["bodyFat"]), 1)
        s.add(entry)


def update_sync_state(user_id: int, status: str, message: str) -> None:
    with db.session() as s:
        state = s.get(SyncState, user_id) or SyncState(user_id=user_id)
        state.status = status
        state.message = message
        state.last_sync = datetime.now()
        s.add(state)
        s.commit()
