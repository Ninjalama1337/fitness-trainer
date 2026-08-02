"""Strukturierte Workouts (aus KI-Steps) an Garmin Connect senden."""
from garminconnect.workout import (
    CyclingWorkout,
    ExecutableStep,
    FitnessEquipmentWorkout,
    RunningWorkout,
    StepType,
    create_cooldown_step,
    create_interval_step,
    create_recovery_step,
    create_warmup_step,
)

from . import garmin_sync as g

STEP_TYPES = {"warmup", "interval", "recovery", "cooldown", "rest"}
SENDABLE_SPORTS = {"running": RunningWorkout, "cycling": CyclingWorkout}

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


def build_workout(name: str, sport: str, steps: list) -> dict:
    """Baut das Garmin-Workout-JSON aus den KI-Steps."""
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


def _connected_api(user_id: int):
    api = g._api(user_id)
    try:
        g.TOKEN_DIR.mkdir(parents=True, exist_ok=True)
        api.login(tokenstore=str(g.TOKEN_DIR))
    except g.GarminMfaRequired:
        raise
    except Exception as exc:
        raise WorkoutError(f"Garmin-Login fehlgeschlagen: {exc}") from exc
    return api


def upload_workout(user_id: int, name: str, sport: str, steps: list) -> str:
    payload = build_workout(name, sport, steps)
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


def push_workout_to_devices(user_id: int, workout_id: str) -> list[dict]:
    """Sendet ein Workout direkt an alle Garmin-Geräte (Uhr, Radcomputer …)."""
    api = _connected_api(user_id)
    try:
        devices = api.get_devices()
    except g.GarminMfaRequired:
        raise
    except Exception as exc:
        raise WorkoutError(f"Geräte abrufen fehlgeschlagen: {exc}") from exc
    targets = [d for d in devices if d.get("appSupport") is True]
    if not targets:
        raise WorkoutError(
            "Kein Garmin-Gerät gefunden, an das Workouts gesendet werden können"
        )
    results = []
    for d in targets:
        dev_id = d.get("deviceId")
        dev_key = d.get("applicationKey") or str(dev_id)
        try:
            api.push_workout_to_device(workout_id, dev_id)
            results.append({"device": dev_key, "ok": True})
        except g.GarminMfaRequired:
            raise
        except Exception as exc:
            results.append(
                {"device": dev_key, "ok": False, "error": str(exc)[:150]}
            )
    return results


def sendable(sport: str, steps: list | None) -> bool:
    return sport in SENDABLE_SPORTS and bool(steps)
