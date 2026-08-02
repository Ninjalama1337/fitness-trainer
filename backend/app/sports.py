SPORT_ALIASES: dict[str, str] = {
    "running": "running",
    "treadmill_running": "running",
    "track_running": "running",
    "trail_running": "running",
    "indoor_running": "running",
    "street_running": "running",
    "cycling": "cycling",
    "road_biking": "cycling",
    "mountain_biking": "cycling",
    "indoor_cycling": "cycling",
    "gravel_cycling": "cycling",
    "cyclocross": "cycling",
    "biking": "cycling",
    "virtual_ride": "cycling",
    "strength_training": "strength",
    "strength": "strength",
    "cardio": "strength",
    "hiit": "strength",
    "cross_training": "strength",
    "functional_fitness": "strength",
    "custom": "other",
    "other": "other",
}

SPORT_LABELS = {
    "running": "Laufen",
    "cycling": "Radfahren",
    "strength": "Kraft",
    "other": "Sonstiges",
}


def map_sport(sport_type) -> str:
    if sport_type is None:
        return "other"
    if isinstance(sport_type, dict):
        for key in ("typeKey", "key", "sportTypeKey", "name", "value"):
            v = sport_type.get(key)
            if isinstance(v, str) and v:
                sport_type = v
                break
        else:
            tid = sport_type.get("typeId") or sport_type.get("sportTypeId")
            return map_sport_id(tid)
    if not isinstance(sport_type, str):
        return "other"
    key = sport_type.lower().replace(" ", "_")
    return SPORT_ALIASES.get(key, "other")


# Garmin-Aktivitäts-IDs (Sportart) → interne Sportart
SPORT_TYPE_ID_MAP: dict[int, str] = {
    1: "running",
    2: "cycling",
    3: "other",       # transition
    4: "strength",    # fitness equipment
    5: "other",       # swimming
    6: "other",       # basketball
    9: "other",       # american football
    10: "other",      # training
    11: "other",      # walking
    12: "other",      # cross country skiing
    13: "other",      # alpine skiing
    15: "other",      # rowing
    16: "other",      # mountaineering
    17: "other",      # hiking
    18: "other",      # multisport
    20: "other",      # flying
    21: "cycling",    # e_biking
    24: "other",      # driving
    25: "other",      # golf
    26: "other",      # hang gliding
    30: "other",      # rock climbing
    33: "other",      # sky diving
    34: "other",      # snowshoeing
    37: "other",      # surfing
    43: "other",      # acrobics
    44: "strength",   # strength training
    45: "other",      # badminton
    49: "other",      # hockey
    50: "other",      # pilates
    52: "other",      # squash
    54: "other",      # volleyball
}


def map_sport_id(sport_type_id) -> str:
    try:
        return SPORT_TYPE_ID_MAP.get(int(sport_type_id), "other")
    except (TypeError, ValueError):
        return "other"


def normalize_zones(values, zones: int = 5) -> dict[str, int] | None:
    """Normalisiert Zonen-Daten (Sekunden) auf {'zone1'..'zone5'}.

    Akzeptiert: Liste von Zahlen, dict mit 'hrTimeInZones', oder eine Liste von
    Dicts {'zoneNumber': n, 'secsInZone': s} (Garmin 'hr in timezones'-Format).
    """
    if not values:
        return None
    if isinstance(values, dict):
        values = values.get("hrTimeInZones") or values.get("zones")
        if not values:
            return None
    try:
        if all(isinstance(v, dict) for v in values):
            nums = [0] * zones
            for v in values:
                try:
                    zn = int(v.get("zoneNumber", 0))
                    secs = int(v.get("secsInZone", 0))
                except (TypeError, ValueError):
                    continue
                if 1 <= zn <= zones:
                    nums[zn - 1] = secs
            return {f"zone{i + 1}": nums[i] for i in range(zones)}
        nums = [int(v) for v in values if v is not None]
    except (TypeError, ValueError):
        return None
    if not nums:
        return None
    if len(nums) > zones:
        nums = nums[-zones:]
    while len(nums) < zones:
        nums.insert(0, 0)
    return {f"zone{i + 1}": nums[i] for i in range(zones)}


def compute_zones_from_hr(
    heart_rates: list[float], max_hr: float
) -> dict[str, int] | None:
    """Berechnet Zone-Sekunden aus Herzfrequenz-Sequenz (FIT-Fallback)."""
    if not heart_rates or max_hr <= 0:
        return None
    thresholds = [0.60 * max_hr, 0.70 * max_hr, 0.80 * max_hr, 0.90 * max_hr]
    counts = [0, 0, 0, 0, 0]
    for hr in heart_rates:
        if hr <= 0:
            continue
        zone = 4
        for i, t in enumerate(thresholds):
            if hr < t:
                zone = i
                break
        counts[zone] += 1
    return {f"zone{i + 1}": counts[i] for i in range(5)}


def estimate_max_hr() -> float:
    """Eigene MAX_HR aus .env, sonst 220-Alter, sonst 185."""
    from . import config

    if config.MAX_HR > 0:
        return config.MAX_HR
    if config.AGE > 0:
        return 220 - config.AGE
    return 185.0
