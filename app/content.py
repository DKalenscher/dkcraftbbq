"""Recipe/video catalog: category definitions and seeding from content/*.json.

Videos are refreshed from the seed on every start (they mirror TikTok).
Recipes are only inserted when their slug is new, so edits made in the
admin panel are never overwritten.
"""
import json
from datetime import datetime

from .database import BASE_DIR, SessionLocal
from . import models

CONTENT_DIR = BASE_DIR / "content"

# slug -> (display name, short blurb)
CATEGORIES = {
    "beef": ("Beef", "Brisket, chuck roast, beef cheeks, short ribs and every other cut that rewards patience."),
    "pastrami-cured": ("Pastrami & Cured", "Brined, cured and smoked: pastrami, beef belly bacon, sausage and jerky."),
    "pork": ("Pork", "Ribs, loin, belly and pulled shoulder off the smoker."),
    "poultry": ("Poultry", "Turkey breast, brined wings and lighter cooks."),
    "over-the-fire": ("Over the Fire", "Steaks, lamb and skewers seared hard over live coals."),
    "cold-smoked": ("Cold Smoked", "Cheese and anything else that takes smoke but should never get warm."),
    "apps-sides": ("Apps, Sides & Basics", "Poppers, smoked cream cheese, tallow and other supporting cast."),
}


def category_name(slug: str) -> str:
    return CATEGORIES.get(slug, (slug.replace("-", " ").title(), ""))[0]


def _dt(value):
    return datetime.fromisoformat(value) if value else None


def seed_content():
    recipes_file = CONTENT_DIR / "recipes.json"
    videos_file = CONTENT_DIR / "videos.json"
    db = SessionLocal()
    try:
        if videos_file.exists():
            for v in json.loads(videos_file.read_text()):
                row = db.get(models.Video, v["id"]) or models.Video(id=v["id"])
                row.caption = v.get("caption", "")
                row.posted_at = _dt(v.get("posted_at"))
                row.duration = v.get("duration")
                row.thumb = v.get("thumb")
                row.views = v.get("views", 0)
                row.likes = v.get("likes", 0)
                row.recipe_slug = v.get("recipe_slug")
                db.merge(row)

        if recipes_file.exists():
            existing = {slug for (slug,) in db.query(models.Recipe.slug).all()}
            for r in json.loads(recipes_file.read_text()):
                if r["slug"] in existing:
                    continue
                db.add(models.Recipe(
                    slug=r["slug"],
                    title=r["title"],
                    category=r["category"],
                    summary=r.get("summary", ""),
                    article=r.get("article", ""),
                    ingredients=json.dumps(r.get("ingredients", [])),
                    steps=json.dumps(r.get("steps", [])),
                    notes=json.dumps(r.get("notes", [])),
                    prep_time=r.get("prep_time"),
                    cook_time=r.get("cook_time"),
                    total_time=r.get("total_time"),
                    yield_text=r.get("yield_text"),
                    method=r.get("method"),
                    wood=r.get("wood"),
                    image=r.get("image"),
                    video_ids=json.dumps(r.get("video_ids", [])),
                    views=r.get("views", 0),
                    needs_review=r.get("needs_review", False),
                    review_notes=r.get("review_notes"),
                    is_published=r.get("is_published", True),
                    is_featured=r.get("is_featured", False),
                    published_at=_dt(r.get("published_at")),
                ))
        db.commit()
    finally:
        db.close()
