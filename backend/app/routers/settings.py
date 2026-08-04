from datetime import datetime, timedelta

from fastapi import APIRouter, Depends

from .. import auth, config, garmin_sync, llm
from ..db import session
from ..models import SyncState, User

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings(user: User = Depends(auth.get_current_user)):
    with session() as s:
        state = s.get(SyncState, user.id)
        last_sync = state.last_sync if state else None
        sync_status = state.status if state else "never"
        sync_message = state.message if state else "Noch nie synchronisiert"
    stale = False
    if last_sync:
        stale = (datetime.now() - last_sync) > timedelta(hours=config.SYNC_STALE_HOURS)
    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "is_admin": user.is_admin,
        },
        "garmin_configured": garmin_sync.is_configured(user.id),
        "garmin_email": garmin_sync.masked_email(user.id),
        "sync_status": sync_status,
        "sync_message": sync_message,
        "last_sync": (last_sync.isoformat() + "Z") if last_sync else None,
        "sync_stale": stale,
        "sync_interval_minutes": config.SYNC_INTERVAL_MINUTES,
        "llm": llm.status(user),
        "version": "1.3.0",
    }
