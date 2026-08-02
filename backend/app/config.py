import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = BACKEND_DIR.parent / "frontend"
DATA_DIR = PROJECT_ROOT / "data"

load_dotenv(PROJECT_ROOT / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, ""))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, ""))
    except ValueError:
        return default


def get(key: str, default: str | None = None) -> str | None:
    return os.getenv(key, default)


DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{DATA_DIR / 'fitness.db'}"
SYNC_INTERVAL_MINUTES = _int("SYNC_INTERVAL_MINUTES", 360)
SYNC_STALE_HOURS = _int("SYNC_STALE_HOURS", 24)
MAX_HR = _float("MAX_HR", 0.0)
AGE = _int("AGE", 0)

DATA_DIR.mkdir(parents=True, exist_ok=True)
