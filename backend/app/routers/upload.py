from fastapi import APIRouter, Depends, HTTPException, UploadFile

from .. import auth, fit_upload
from ..garmin_sync import update_sync_state
from ..models import User

router = APIRouter(prefix="/api/upload", tags=["upload"])


@router.post("/fit")
async def upload_fit(
    files: list[UploadFile], user: User = Depends(auth.get_current_user)
):
    results = []
    imported = 0
    for f in files:
        data = await f.read()
        try:
            result = fit_upload.import_fit(user.id, data)
            results.append({"file": f.filename, **result})
            if result["imported"]:
                imported += 1
        except fit_upload.FitError as exc:
            results.append({"file": f.filename, "imported": False, "error": str(exc)})
        except Exception as exc:
            results.append({"file": f.filename, "imported": False, "error": f"Unbekannter Fehler: {exc}"})
    if imported:
        update_sync_state(user.id, "ok", f"{imported} FIT-Datei(en) importiert")
    return {"results": results}
