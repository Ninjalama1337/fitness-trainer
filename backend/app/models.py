import datetime as dt
from typing import Any, Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str = ""
    display_name: str = ""
    is_admin: bool = False
    llm_provider: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    created_at: dt.datetime = Field(default_factory=dt.datetime.now)


class Activity(SQLModel, table=True):
    __tablename__ = "activities"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    garmin_id: str = Field(default="", index=True)
    name: str = ""
    sport: str = Field(default="other", index=True)
    start_time: dt.datetime = Field(index=True)
    duration_seconds: float = 0
    distance_km: Optional[float] = None
    avg_hr: Optional[float] = None
    max_hr: Optional[float] = None
    calories: Optional[float] = None
    avg_pace_min_km: Optional[float] = None
    avg_speed_kmh: Optional[float] = None
    training_load: Optional[float] = None
    hr_zones: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    source: str = "garmin"
    extra: Optional[dict] = Field(default=None, sa_column=Column(JSON))


class HealthDay(SQLModel, table=True):
    __tablename__ = "health_days"

    user_id: int = Field(primary_key=True)
    date: dt.date = Field(primary_key=True)
    sleep_seconds: Optional[float] = None
    deep_sleep_seconds: Optional[float] = None
    active_calories: Optional[float] = None
    resting_hr: Optional[float] = None
    hrv_avg: Optional[float] = None
    hrv_status: Optional[str] = None
    stress_avg: Optional[float] = None
    steps: Optional[int] = None


class PlanDay(SQLModel, table=True):
    __tablename__ = "plan_days"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    week: str = Field(index=True)
    day_offset: int = 0
    sport: str = "running"
    focus: str = ""
    description: str = ""
    done: bool = False
    steps: Optional[list] = Field(default=None, sa_column=Column(JSON))
    kraft_steps: Optional[list] = Field(default=None, sa_column=Column(JSON))
    garmin_workout_id: Optional[str] = None
    race_goal_id: Optional[int] = None
    created_at: dt.datetime = Field(default_factory=dt.datetime.now)


class Suggestion(SQLModel, table=True):
    __tablename__ = "suggestions"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    created_at: dt.datetime = Field(default_factory=dt.datetime.now)
    title: str = ""
    sport: str = "running"
    rationale: str = ""
    workout: str = ""
    steps: Optional[list] = Field(default=None, sa_column=Column(JSON))
    garmin_workout_id: Optional[str] = None
    context: Optional[dict] = Field(default=None, sa_column=Column(JSON))


class SyncState(SQLModel, table=True):
    __tablename__ = "sync_state"

    user_id: int = Field(primary_key=True)
    last_sync: Optional[dt.datetime] = None
    status: str = "never"
    message: str = "Noch nie synchronisiert"


class GarminCred(SQLModel, table=True):
    __tablename__ = "garmin_creds"

    user_id: int = Field(primary_key=True)
    email: str = ""
    password: str = ""


class Goal(SQLModel, table=True):
    __tablename__ = "goals"

    user_id: int = Field(primary_key=True)
    running_km: Optional[float] = None
    cycling_km: Optional[float] = None
    updated_at: dt.datetime = Field(default_factory=dt.datetime.now)


class RaceGoal(SQLModel, table=True):
    __tablename__ = "race_goals"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    name: str = ""
    target_date: dt.date
    distance_km: float = 10.0
    created_at: dt.datetime = Field(default_factory=dt.datetime.now)
