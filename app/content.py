"""Recipe/video catalog: category definitions and seeding from content/*.json.

Rows are only inserted when they're new, so admin edits and the data the
TikTok sync keeps up to date (views, playlists, new recipes) are never
overwritten by the seed files.
"""
import json
from datetime import datetime

from .database import BASE_DIR, SessionLocal, engine
from . import models

CONTENT_DIR = BASE_DIR / "content"

# slug -> (display name, short blurb). These mirror the TikTok playlists; a
# recipe's category follows the playlist its videos are in (app/tiktok_sync.py).
CATEGORIES = {
    "beef": ("Beef", "Brisket, pastrami, chuck roast, beef cheeks, short ribs and every other cut that rewards patience."),
    "pork": ("Pork", "Ribs, loin, belly and pulled shoulder off the smoker."),
    "poultry": ("Poultry", "Turkey breast, brined wings and lighter cooks."),
    "more": ("And More!", "Cold-smoked cheese, poppers, rubs, seafood and everything else."),
}

# TikTok playlist name (lowercased) -> category slug. Unknown playlists go to "more".
PLAYLIST_CATEGORIES = {"beef": "beef", "pork": "pork", "poultry": "poultry", "and more!": "more"}

# Categories from before the playlists, mapped to the closest current one.
LEGACY_CATEGORIES = {
    "pastrami-cured": "beef",
    "over-the-fire": "beef",
    "cold-smoked": "more",
    "apps-sides": "more",
}


# Old recipe addresses that moved; they 301 to the new slug
RENAMED_RECIPES = {
    "charcoal-grilled-wings-and-veggie-skewers-on-the-mill-scale-yakitori-grill": "charcoal-grilled-wings",
}

# Shop products a recipe links to when its ingredients, wood or notes mention them.
# (phrase to look for, lowercase) -> Link id in the shop. Rule order is display order.
# Admin can replace a recipe's automatic picks with its own (Recipe.gear_ids).
GEAR_RULES = [
    ("holy cow", 25),            # Meat Church Holy Cow
    ("the gospel", 47),          # Meat Church The Gospel (not Holy Gospel)
    ("honey hog", 29),           # Meat Church Honey Hog 2-Pack
    ("whomp", 33),               # Meat Mitch WHOMP!
    ("bachan's", 48),            # Bachan's Japanese Barbecue Sauce
    ("curing salt", 6),          # SPQR Prague Powder #1
    ("pickling spice", 5),       # Badia pickling spice
    ("bear mountain", 11),       # Bear Mountain pellets
    ("smokin' pecan", 12),       # Smokin' Pecan pellets
    ("smoke tube", 15),          # LIZZQ smoke tubes
    ("thermopop", 9),            # ThermoPop 2
    ("porter road", 24),
    ("snake river", 23),
]
GEAR_AUTO_LIMIT = 4


def auto_gear_ids(ingredients: list, notes: list, wood) -> list:
    """Shop Link ids the recipe text mentions, per GEAR_RULES (before the limit)."""
    text = "\n".join([i for g in ingredients for i in g.get("items", [])] + notes + [wood or ""]).lower()
    ids = []
    for phrase, link_id in GEAR_RULES:
        if phrase in text and link_id not in ids:
            ids.append(link_id)
    return ids


def gear_item_links(ingredients: list, ids: list) -> dict:
    """{ingredient line: Link id} for ingredient lines that name one of the given products."""
    out = {}
    for item in (i for g in ingredients for i in g.get("items", [])):
        for phrase, link_id in GEAR_RULES:
            if link_id in ids and phrase in item.lower():
                out.setdefault(item, link_id)
    return out


def normalize_category(slug: str) -> str:
    slug = LEGACY_CATEGORIES.get(slug, slug)
    return slug if slug in CATEGORIES else "more"


def category_name(slug: str) -> str:
    return CATEGORIES.get(slug, (slug.replace("-", " ").title(), ""))[0]


# Columns added after a table first shipped: (table, column, DDL)
_ADDED_COLUMNS = [
    ("recipes", "in_playlist", "BOOLEAN NOT NULL DEFAULT 1"),
    ("videos", "playlist", "VARCHAR(100)"),
    ("page_views", "path", "VARCHAR(300)"),
    ("page_views", "search", "VARCHAR(200)"),
    ("page_views", "search_results", "INTEGER"),
    ("recipes", "gear_ids", "TEXT"),
]


def migrate_db():
    """Add columns that create_all() can't add to existing tables, and remap old categories."""
    with engine.begin() as conn:
        for table, column, ddl in _ADDED_COLUMNS:
            have = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if have and column not in have:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        conn.exec_driver_sql("DROP TABLE IF EXISTS subscribers")  # newsletter dropped 2026-10-21
        for old, new in LEGACY_CATEGORIES.items():
            conn.exec_driver_sql("UPDATE recipes SET category = ? WHERE category = ?", (new, old))


def _dt(value):
    return datetime.fromisoformat(value) if value else None


def seed_content():
    recipes_file = CONTENT_DIR / "recipes.json"
    videos_file = CONTENT_DIR / "videos.json"
    db = SessionLocal()
    try:
        if videos_file.exists():
            for v in json.loads(videos_file.read_text()):
                if db.get(models.Video, v["id"]):
                    continue
                row = models.Video(id=v["id"])
                row.caption = v.get("caption", "")
                row.posted_at = _dt(v.get("posted_at"))
                row.duration = v.get("duration")
                row.thumb = v.get("thumb")
                row.views = v.get("views", 0)
                row.likes = v.get("likes", 0)
                row.recipe_slug = v.get("recipe_slug")
                db.add(row)

        if recipes_file.exists():
            existing = {slug for (slug,) in db.query(models.Recipe.slug).all()}
            for r in json.loads(recipes_file.read_text()):
                if r["slug"] in existing:
                    continue
                db.add(models.Recipe(
                    slug=r["slug"],
                    title=r["title"],
                    category=normalize_category(r["category"]),
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
