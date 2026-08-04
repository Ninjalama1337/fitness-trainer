import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from . import auth, config, db, garmin_sync
from .routers import (
    activities,
    auth as auth_router,
    debug,
    garmin,
    goals,
    plan,
    race,
    settings,
    stats,
    suggestion,
    upload,
    users,
    weekly_summary,
    monthly_summary,
    push,
)

logger = logging.getLogger("fitness")
logging.basicConfig(level=logging.INFO)

scheduler = BackgroundScheduler()


def run_scheduled_sync() -> None:
    from sqlmodel import select

    from .models import User

    with db.session() as s:
        users = s.exec(select(User)).all()
    for user in users:
        if not garmin_sync.is_configured(user.id):
            continue
        try:
            result = garmin_sync.sync_garmin(user.id, limit=50)
            garmin_sync.update_sync_state(
                user.id,
                "ok",
                f"Auto-Sync: {result['imported']} neu, {result['skipped']} übersprungen",
            )
            if result.get("imported", 0) > 0:
                from .plan_service import ensure_month_summary, ensure_week_summary

                ensure_week_summary(user.id)
                ensure_month_summary(user.id)
            logger.info("Auto-Sync OK (User %s): %s", user.id, result)
        except Exception as exc:
            logger.warning("Auto-Sync fehlgeschlagen (User %s): %s", user.id, exc)
            garmin_sync.update_sync_state(user.id, "error", f"Auto-Sync fehlgeschlagen: {exc}")
            from . import push

            push.notify_sync_error(user.id, str(exc))


def run_daily_plan_reminder() -> None:
    from .models import User
    from .push import notify_daily_plan

    with db.session() as s:
        users = s.exec(select(User)).all()
    for user in users:
        notify_daily_plan(user.id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    if config.SYNC_INTERVAL_MINUTES > 0:
        scheduler.add_job(
            run_scheduled_sync,
            "interval",
            minutes=config.SYNC_INTERVAL_MINUTES,
            id="garmin_sync",
        )
        scheduler.start()
    try:
        scheduler.add_job(
            run_daily_plan_reminder,
            "cron",
            hour=7,
            minute=30,
            id="plan_reminder",
        )
    except Exception:
        pass
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Fitness Trainer", version="1.3.0", lifespan=lifespan)


@app.middleware("http")
async def csrf_protect(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        try:
            auth.assert_csrf_valid(request)
        except Exception:
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=403, content={"detail": "CSRF: Anfrage abgelehnt"})
    response = await call_next(request)
    return response


app.include_router(auth_router.router)
app.include_router(users.router)
app.include_router(goals.router)
app.include_router(race.router)
app.include_router(activities.router)
app.include_router(stats.router)
app.include_router(garmin.router)
app.include_router(upload.router)
app.include_router(plan.router)
app.include_router(suggestion.router)
app.include_router(settings.router)
app.include_router(weekly_summary.router)
app.include_router(monthly_summary.router)
app.include_router(push.router)
app.include_router(debug.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "database": config.DATABASE_URL}


frontend_dir = Path(config.FRONTEND_DIR)
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
