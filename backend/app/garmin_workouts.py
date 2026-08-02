"""Strukturierte Workouts (aus KI-Steps) an Garmin Connect senden."""
import logging
import re

from garminconnect import exercises
from garminconnect.workout import (
    CyclingWorkout,
    ExecutableStep,
    FitnessEquipmentWorkout,
    RunningWorkout,
    StrengthWorkout,
    StepType,
    create_cooldown_step,
    create_interval_step,
    create_recovery_step,
    create_strength_set,
    create_warmup_step,
)

from . import garmin_sync as g

logger = logging.getLogger("fitness")

STEP_TYPES = {"warmup", "interval", "recovery", "cooldown", "rest"}
SENDABLE_SPORTS = {"running": RunningWorkout, "cycling": CyclingWorkout, "strength": StrengthWorkout}

HR_ZONE_TARGET = {
    "workoutTargetTypeId": 4,
    "workoutTargetTypeKey": "heart.rate.zone",
}


class WorkoutError(Exception):
    pass


class NoStepsError(WorkoutError):
    def __init__(self):
        super().__init__("Keine strukturierten Schritte vorhanden – Vorschlag kann nicht als Garmin-Workout angelegt werden")


class UnsupportedSportError(WorkoutError):
    def __init__(self, sport: str):
        super().__init__(f"Sportart '{sport}' kann nicht als Workout übertragen werden (nur Laufen/Radfahren)")


def _hr_target(zone: int | None) -> dict | None:
    if zone is None:
        return None
    return {**HR_ZONE_TARGET, "zoneNumber": int(zone)}


def _step(typ: str, dauer_min: float, zone: int | None, order: int) -> ExecutableStep:
    dauer_sec = int(dauer_min * 60)
    target = _hr_target(zone)
    if typ == "warmup":
        return create_warmup_step(dauer_sec, step_order=order, target_type=target)
    if typ == "interval":
        return create_interval_step(dauer_sec, step_order=order, target_type=target)
    if typ == "recovery":
        return create_recovery_step(dauer_sec, step_order=order, target_type=target)
    if typ == "cooldown":
        return create_cooldown_step(dauer_sec, step_order=order, target_type=target)
    return ExecutableStep(
        stepOrder=order,
        stepType={"stepTypeId": StepType.REST, "stepTypeKey": "rest", "displayOrder": 5},
        endCondition={
            "conditionTypeId": 2,
            "conditionTypeKey": "time",
            "displayOrder": 2,
            "displayable": True,
        },
        endConditionValue=dauer_sec,
        targetType={
            "workoutTargetTypeId": 1,
            "workoutTargetTypeKey": "no.target",
            "displayOrder": 1,
        },
    )


def _norm_de(s: str) -> str:
    return (
        s.lower()
        .replace("ü", "ue")
        .replace("ö", "oe")
        .replace("ä", "ae")
        .replace("ß", "ss")
        .strip()
    )


# Deutsche Übungsbegriffe → englischer Katalog-Begriff
DE_EXERCISE_MAP: dict[str, str] = {
    "kniebeuge": "squat",
    "kniebeugen": "squat",
    "bankdruecken": "bench press",
    "bankpressen": "bench press",
    "kreuzheben": "deadlift",
    "klimmzug": "pull-up",
    "klimmzuege": "pull-up",
    "liegestuetz": "push-up",
    "liegestuetze": "push-up",
    "rudern": "barbell row",
    "schulterdruecken": "shoulder press",
    "ausfallschritt": "lunge",
    "ausfallschritte": "lunge",
    "dips": "dip",
    "plank": "plank",
    "unterarmstuetz": "plank",
    "bizepscurl": "dumbbell curl",
    "bizeps": "dumbbell curl",
    "curl": "dumbbell curl",
    "curls": "dumbbell curl",
    "trizeps": "triceps extension",
    "trizepsdruecken": "triceps extension",
    "crunch": "crunch",
    "crunches": "crunch",
    "rumpfbeuge": "crunch",
    "wadenheben": "calf raise",
    "seitheben": "lateral raise",
    "beinstrecker": "leg extension",
    "beinbeuger": "leg curl",
    "hip thrust": "hip thrust",
    "glute bridge": "glute bridge",
    "ghd": "hyperextension",
    "butterfly": "butterfly",
    "latzug": "lat pulldown",
    "latzug": "lat pulldown",
    "kabelzug": "cable",
    "hantel": "dumbbell",
    "langhantel": "barbell",
}


def _resolve_exercise(uebung: str) -> tuple[str, str] | None:
    """Übungsname (DE/EN) → (category, exercise) aus dem Garmin-Katalog."""
    norm = _norm_de(uebung)
    term = DE_EXERCISE_MAP.get(norm) or DE_EXERCISE_MAP.get(norm.split(" ")[0]) or uebung

    def find_all(t: str) -> list:
        try:
            return exercises.find(t)
        except Exception:
            return []

    matches = find_all(term)
    if not matches:
        matches = find_all(term.split(" ")[0])
    if not matches:
        return None
    # Bevorzugt exakte/ab-Heftige Treffer (z.B. "Squat" statt "Banded Squat")
    term_norm = _norm_de(term)
    best = next(
        (m for m in matches if _norm_de(m.get("name", "")) == term_norm),
        None,
    )
    if not best:
        best = next(
            (m for m in matches if _norm_de(m.get("name", "")).startswith(term_norm)),
            None,
        )
    if not best:
        best = next(
            (
                m
                for m in matches
                if _norm_de(m.get("exercise", "")).replace("_", " ") == term_norm
            ),
            None,
        )
    best = best or matches[0]
    return best.get("category"), best.get("exercise")


def build_workout(name: str, sport: str, steps: list, kraft_steps: list | None = None) -> dict:
    """Baut das Garmin-Workout-JSON aus den KI-Steps."""
    if sport == "strength":
        if not kraft_steps:
            raise NoStepsError()
        return _build_strength_workout(name, kraft_steps)
    if not steps:
        raise NoStepsError()
    model_cls = SENDABLE_SPORTS.get(sport)
    if not model_cls:
        raise UnsupportedSportError(sport)
    workout_steps = [
        _step(
            str(s.get("typ", "rest")),
            float(s.get("dauer_min", 0) or 0),
            s.get("zone"),
            i + 1,
        )
        for i, s in enumerate(steps)
    ]
    total_secs = int(sum(float(s.get("dauer_min", 0) or 0) * 60 for s in steps))
    workout = model_cls(
        workoutName=name[:50],
        estimatedDurationInSecs=total_secs,
        workoutSegments=[
            {
                "segmentOrder": 1,
                "sportType": model_cls.model_fields["sportType"].default_factory(),
                "workoutSteps": workout_steps,
            }
        ],
        description=name,
    )
    return workout.to_dict()


def _build_strength_workout(name: str, kraft_steps: list) -> dict:
    """Kraft-Workout mit Sätzen/Wiederholungen/Gewicht (RepeatGroups)."""
    workout_steps = []
    order = 1
    skipped = []
    for s in kraft_steps:
        uebung = str(s.get("uebung", "")).strip()
        resolved = _resolve_exercise(uebung)
        if not resolved:
            skipped.append(uebung)
            logger.info("Übung nicht im Garmin-Katalog: %s", uebung)
            continue
        category, exercise = resolved
        rest_sec = 60 if s.get("saetze", 3) > 3 else 90
        try:
            group = create_strength_set(
                category=category,
                step_order=order,
                sets=int(s.get("saetze", 3)),
                reps=int(s.get("wiederholungen", 10)),
                rest_seconds=float(rest_sec),
                exercise_name=exercise,
                weight_kg=s.get("gewicht_kg"),
            )
        except Exception as exc:
            logger.info("Kraft-Step fehlgeschlagen (%s): %s", uebung, exc)
            skipped.append(uebung)
            continue
        workout_steps.append(group)
        order += 3
    if not workout_steps:
        raise NoStepsError()
    if skipped:
        name = (name[:40] + " …") if len(skipped) > 1 else name
    workout = StrengthWorkout(
        workoutName=name[:50],
        estimatedDurationInSecs=max(1800, order * 60),
        workoutSegments=[
            {
                "segmentOrder": 1,
                "sportType": StrengthWorkout.model_fields["sportType"].default_factory(),
                "workoutSteps": workout_steps,
            }
        ],
        description=name,
    )
    return workout.to_dict()


def _connected_api(user_id: int):
    api = g._api(user_id)
    try:
        tdir = g.token_dir(user_id)
        tdir.mkdir(parents=True, exist_ok=True)
        api.login(tokenstore=str(tdir))
    except g.GarminMfaRequired:
        raise
    except Exception as exc:
        raise WorkoutError(f"Garmin-Login fehlgeschlagen: {exc}") from exc
    return api


def upload_workout(
    user_id: int,
    name: str,
    sport: str,
    steps: list,
    kraft_steps: list | None = None,
) -> str:
    payload = build_workout(name, sport, steps, kraft_steps)
    api = _connected_api(user_id)
    try:
        result = api.upload_workout(payload)
    except g.GarminMfaRequired:
        raise
    except Exception as exc:
        raise WorkoutError(f"Upload fehlgeschlagen: {exc}") from exc
    if isinstance(result, dict):
        wid = result.get("workoutId")
        if wid:
            return str(wid)
    raise WorkoutError(f"Upload fehlgeschlagen (unerwartete Antwort): {str(result)[:200]}")


def delete_workout(user_id: int, workout_id: str) -> None:
    api = _connected_api(user_id)
    try:
        api.delete_workout(workout_id)
    except g.GarminMfaRequired:
        raise
    except Exception as exc:
        raise WorkoutError(f"Löschen fehlgeschlagen: {exc}") from exc


WATCH_KEYS = (
    "forerunner", "fenix", "venu", "instinct", "vivomove", "vivoactive",
    "marq", "epix", "tactix", "descent", "enduro", "approach", "d2", "quatix",
)
BIKE_KEYS = ("edge",)
HRM_KEYS = ("hrm", "hr_pro", "heart_rate", "hr_pro")


def classify_device(key: str) -> str:
    """Klassifiziert ein Gerät: watch | bike_computer | hrm | other."""
    k = (key or "").lower()
    if any(s in k for s in WATCH_KEYS):
        return "watch"
    if any(s in k for s in BIKE_KEYS):
        return "bike_computer"
    if any(s in k for s in HRM_KEYS):
        return "hrm"
    return "other"


def _friendly_name(device: dict, key: str) -> str:
    for f in ("friendlyName", "deviceName", "productName", "name"):
        v = device.get(f)
        if v:
            return str(v)
    base = re.sub(r"\d+.*$", "", key).strip("_-")
    if base:
        return base.capitalize()
    return {
        "watch": "Uhr",
        "bike_computer": "Radcomputer",
        "hrm": "Herzfrequenzgurt",
        "other": "Gerät",
    }.get(classify_device(key), "Gerät")


def list_devices(user_id: int) -> list[dict]:
    """Listet alle Garmin-Geräte, an die Workouts gesendet werden können."""
    api = _connected_api(user_id)
    try:
        devices = api.get_devices()
    except g.GarminMfaRequired:
        raise
    except Exception as exc:
        raise WorkoutError(f"Geräte abrufen fehlgeschlagen: {exc}") from exc
    out = []
    for d in devices:
        if d.get("appSupport") is not True:
            continue
        dev_id = d.get("deviceId")
        key = d.get("applicationKey") or ""
        out.append(
            {
                "device_id": str(dev_id),
                "name": _friendly_name(d, key),
                "kind": classify_device(key),
                "application_key": key,
            }
        )
    return out


def default_device_ids(devices: list[dict], sport: str) -> list[str]:
    """Bevorzugte Geräte für eine Sportart: Rad → Radcomputer, sonst → Uhr."""
    kind = "bike_computer" if sport == "cycling" else "watch"
    ids = [d["device_id"] for d in devices if d["kind"] == kind]
    if not ids:
        ids = [d["device_id"] for d in devices if d["kind"] == "other"]
    return ids


def push_workout_to_devices(
    user_id: int, workout_id: str, device_ids: list[str] | None = None
) -> list[dict]:
    """Sendet ein Workout an Garmin-Geräte (default: alle sendbaren)."""
    devices = list_devices(user_id)
    if not devices:
        raise WorkoutError(
            "Kein Garmin-Gerät gefunden, an das Workouts gesendet werden können"
        )
    if device_ids is not None:
        wanted = set(device_ids)
        targets = [d for d in devices if d["device_id"] in wanted]
    else:
        targets = [d for d in devices if d["kind"] != "hrm"]
    if not targets:
        raise WorkoutError("Keine passenden Garmin-Geräte ausgewählt")
    api = _connected_api(user_id)
    results = []
    for d in targets:
        dev_id = d["device_id"]
        dev_key = d.get("application_key") or dev_id
        try:
            try:
                dev_id_int = int(dev_id)
            except (TypeError, ValueError):
                dev_id_int = dev_id
            api.push_workout_to_device(workout_id, dev_id_int)
            results.append({"device": dev_key, "ok": True})
        except g.GarminMfaRequired:
            raise
        except Exception as exc:
            results.append(
                {"device": dev_key, "ok": False, "error": str(exc)[:150]}
            )
    return results


def sendable(sport: str, steps: list | None, kraft_steps: list | None = None) -> bool:
    if sport == "strength":
        return bool(kraft_steps)
    return sport in SENDABLE_SPORTS and bool(steps)
