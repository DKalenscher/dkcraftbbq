import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..content import CATEGORIES
from ..database import get_db
from .. import models

router = APIRouter(prefix="/api/admin", tags=["admin-recipes"])

_JSON_FIELDS = ("ingredients", "steps", "notes", "video_ids")


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


def _serialize(r: models.Recipe) -> dict:
    data = {c.name: getattr(r, c.name) for c in r.__table__.columns}
    for f in _JSON_FIELDS:
        data[f] = json.loads(data[f] or "[]")
    for f in ("published_at", "created_at", "updated_at"):
        if isinstance(data[f], datetime):
            data[f] = data[f].isoformat()
    return data


@router.get("/recipes")
def list_recipes(db: Session = Depends(get_db), _: models.AdminUser = Depends(get_current_user)):
    rows = db.query(models.Recipe).order_by(models.Recipe.published_at.desc()).all()
    return {"categories": CATEGORIES, "recipes": [_serialize(r) for r in rows]}


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
        setattr(recipe, field, json.dumps(value) if field in _JSON_FIELDS else value)
    db.commit()
    db.refresh(recipe)
    return _serialize(recipe)
