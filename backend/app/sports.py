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


def map_sport(sport_type: str | None) -> str:
    if not sport_type:
        return "other"
    key = sport_type.lower().replace(" ", "_")
    return SPORT_ALIASES.get(key, "other")


def normalize_zones(values: list | None, zones: int = 5) -> dict[str, int] | None:
    """Normalisiert Sekunden-pro-Zone Listen (Garmin) auf {'zone1'..'zone5'}."""
    if not values:
        return None
    try:
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
