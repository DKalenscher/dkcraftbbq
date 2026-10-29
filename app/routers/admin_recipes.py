import fcntl
import json
import subprocess
import sys
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..content import CATEGORIES, GEAR_AUTO_LIMIT, auto_gear_ids
from ..database import BASE_DIR, get_db
from ..tiktok_sync import LOCK_FILE, STATUS_FILE
from .. import models

router = APIRouter(prefix="/api/admin", tags=["admin-recipes"])

_JSON_FIELDS = ("ingredients", "steps", "notes", "video_ids", "made_with")


class RecipeUpdate(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    summary: Optional[str] = None
    article: Optional[str] = None
    ingredients: Optional[list] = None
    steps: Optional[list] = None
    notes: Optional[list] = None
    prep_time: Optional[str] = None
    cook_time: Optional[str] = None
    total_time: Optional[str] = None
    yield_text: Optional[str] = None
    method: Optional[str] = None
    wood: Optional[str] = None
    image: Optional[str] = None
    needs_review: Optional[bool] = None
    review_notes: Optional[str] = None
    is_published: Optional[bool] = None
    is_featured: Optional[bool] = None
    made_with: Optional[list[str]] = None  # slugs of the recipes this dish is built from
    gear_ids: Optional[list[int]] = None   # null = pick automatically from the recipe text


def _serialize(r: models.Recipe) -> dict:
    data = {c.name: getattr(r, c.name) for c in r.__table__.columns}
    for f in _JSON_FIELDS:
        data[f] = json.loads(data[f] or "[]")
    data["gear_ids"] = json.loads(r.gear_ids) if r.gear_ids is not None else None
    data["auto_gear_ids"] = auto_gear_ids(data["ingredients"], data["notes"], r.wood, r.method, r.slug)[:GEAR_AUTO_LIMIT]
    for f in ("published_at", "created_at", "updated_at"):
        if isinstance(data[f], datetime):
            data[f] = data[f].isoformat()
    return data


@router.get("/recipes")
def list_recipes(db: Session = Depends(get_db), _: models.AdminUser = Depends(get_current_user)):
    rows = db.query(models.Recipe).order_by(models.Recipe.published_at.desc()).all()
    shop = (
        db.query(models.Link, models.Section.title)
        .join(models.Section)
        .filter(models.Link.is_visible == True)
        .order_by(models.Section.order_index, models.Link.order_index)
        .all()
    )
    return {
        "categories": CATEGORIES,
        "recipes": [_serialize(r) for r in rows],
        "shop": [{"id": l.id, "title": l.title, "section": s, "image_url": l.image_url} for l, s in shop],
    }


@router.put("/recipes/{recipe_id}")
def update_recipe(
    recipe_id: int,
    body: RecipeUpdate,
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    recipe = db.query(models.Recipe).filter(models.Recipe.id == recipe_id).first()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    changes = body.model_dump(exclude_unset=True)
    if "category" in changes and changes["category"] not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Unknown category")
    for field, value in changes.items():
        if field == "gear_ids":
            value = None if value is None else json.dumps(value)
        elif field in _JSON_FIELDS:
            value = json.dumps(value)
        setattr(recipe, field, value)
    db.commit()
    db.refresh(recipe)
    return _serialize(recipe)


def _sync_running() -> bool:
    if not LOCK_FILE.exists():
        return False
    with open(LOCK_FILE) as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
    return False


@router.get("/sync")
def sync_status(_: models.AdminUser = Depends(get_current_user)):
    last = json.loads(STATUS_FILE.read_text()) if STATUS_FILE.exists() else None
    return {"running": _sync_running(), "last": last}


@router.post("/sync")
def start_sync(_: models.AdminUser = Depends(get_current_user)):
    """Run the TikTok sync now, in the background (the same job cron runs)."""
    if _sync_running():
        return {"started": False, "running": True}
    log = open(STATUS_FILE.parent / "tiktok_sync.log", "a")
    subprocess.Popen([sys.executable, "-m", "app.tiktok_sync"], cwd=BASE_DIR,
                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return {"started": True, "running": True}
