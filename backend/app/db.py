from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import inspect
from sqlmodel import Session, SQLModel, create_engine, select

from . import config
from .models import Activity

engine = create_engine(
    config.DATABASE_URL, connect_args={"check_same_thread": False}
)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _migrate_columns()
    _migrate_existing_data()


def _table_columns(table: str) -> set[str]:
    try:
        return {col["name"] for col in inspect(engine).get_columns(table)}
    except Exception:
        return set()


def _add_columns(table: str, columns: dict[str, str]) -> None:
    existing = _table_columns(table)
    with engine.begin() as conn:
        for name, col_type in columns.items():
            if name not in existing:
                conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {name} {col_type}"
                )


def _migrate_columns() -> None:
    """Führt fehlende Spalten für bereits bestehende Datenbanken nach."""
    try:
        _add_columns("suggestions", {"sport": "VARCHAR DEFAULT 'running'"})
        _add_columns("suggestions", {"steps": "JSON", "garmin_workout_id": "VARCHAR"})
        _add_columns("plan_days", {"steps": "JSON", "garmin_workout_id": "VARCHAR"})
    except Exception:
        pass
    for table in (
        "activities",
        "health_days",
        "plan_days",
        "suggestions",
        "garmin_creds",
        "sync_state",
    ):
        try:
            _add_columns(table, {"user_id": "INTEGER"})
        except Exception:
            pass
    try:
        _add_columns("health_days", {"hrv_avg": "REAL", "hrv_status": "VARCHAR"})
    except Exception:
        pass
    try:
        _add_columns(
            "health_days",
            {"weight_kg": "REAL", "body_fat_pct": "REAL"},
        )
    except Exception:
        pass
    try:
        _add_columns("activities", {"training_load": "REAL"})
    except Exception:
        pass
    try:
        _add_columns("plan_days", {"race_goal_id": "INTEGER"})
    except Exception:
        pass
    try:
        _add_columns("plan_days", {"kraft_steps": "JSON"})
    except Exception:
        pass


def _migrate_existing_data() -> None:
    """Weist Daten ohne user_id dem Admin zu (Single-User → Multi-User)."""
    from sqlalchemy import text

    from .auth import ensure_admin_exists
    from .models import (
        Activity,
        GarminCred,
        HealthDay,
        PlanDay,
        Suggestion,
        SyncState,
    )

    models = (Activity, HealthDay, PlanDay, Suggestion, GarminCred, SyncState)
    with Session(engine) as s:
        has_orphan = any(
            s.exec(select(m).where(m.user_id.is_(None))).first() for m in models
        )
        if not has_orphan:
            ensure_admin_exists()
            return
        admin = ensure_admin_exists()
        if not admin:
            return
        for table in (
            "activities",
            "health_days",
            "plan_days",
            "suggestions",
            "garmin_creds",
            "sync_state",
        ):
            s.exec(
                text(f"UPDATE {table} SET user_id = :uid WHERE user_id IS NULL"),
                params={"uid": admin.id},
            )
        s.commit()
    _migrate_tokens(admin.id)


def _migrate_tokens(admin_id: int) -> None:
    """Verschiebt alte Garmin-Tokens in den User-Ordner."""
    import shutil

    old = config.DATA_DIR / "garmin_tokens"
    new = config.DATA_DIR / "garmin_tokens" / str(admin_id)
    if old.exists() and old.is_dir() and not (new).exists():
        try:
            if old != new.parent:
                shutil.move(str(old), str(new))
        except Exception:
            pass


@contextmanager
def session() -> Generator[Session, None, None]:
    with Session(engine) as s:
        yield s


def get_activity_by_garmin_id(s: Session, user_id: int, gid: str) -> Activity | None:
    return s.exec(
        select(Activity).where(Activity.user_id == user_id, Activity.garmin_id == gid)
    ).first()
