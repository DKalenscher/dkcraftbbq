import os
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user
from ..config import get_settings

router = APIRouter(prefix="/api/admin/settings", tags=["admin-settings"])
settings = get_settings()

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml"}
MAX_UPLOAD_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024


def _get_all_settings(db: Session) -> dict:
    return {s.key: s.value for s in db.query(models.SiteSetting).all()}


@router.get("")
def get_settings_endpoint(
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    return _get_all_settings(db)


@router.put("")
def update_settings(
    body: schemas.SiteSettings,
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    updates = body.model_dump(exclude_none=False)
    for key, value in updates.items():
        if value is None:
            continue
        row = db.query(models.SiteSetting).filter(models.SiteSetting.key == key).first()
        if row:
            row.value = str(value)
        else:
            db.add(models.SiteSetting(key=key, value=str(value)))
    db.commit()
    return _get_all_settings(db)


@router.post("/logo")
def upload_logo(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only image files are allowed")

    contents = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail=f"File too large (max {settings.MAX_UPLOAD_SIZE_MB}MB)")

    ext = os.path.splitext(file.filename or "logo.png")[1] or ".png"
    filename = f"logo_{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(settings.UPLOAD_DIR, filename)

    with open(filepath, "wb") as f:
        f.write(contents)

    logo_url = f"/uploads/{filename}"
    row = db.query(models.SiteSetting).filter(models.SiteSetting.key == "logo_path").first()
    if row:
        old_path = os.path.join(settings.UPLOAD_DIR, os.path.basename(row.value))
        if os.path.exists(old_path) and row.value != logo_url:
            try:
                os.remove(old_path)
            except OSError:
                pass
        row.value = logo_url
    else:
        db.add(models.SiteSetting(key="logo_path", value=logo_url))
    db.commit()

    return {"logo_path": logo_url}
