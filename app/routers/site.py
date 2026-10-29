"""Server-rendered public pages for the v2 recipe site."""
import inspect
import json
import math
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..content import (
    CATEGORIES, GEAR_AUTO_LIMIT, LEGACY_CATEGORIES, RENAMED_RECIPES, auto_gear_ids, category_name,
    gear_item_links,
)
from ..database import BASE_DIR, get_db
from .. import models

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
# Starlette >= 0.29 takes (request, name, context); older releases take (name, context)
_NEW_TEMPLATE_API = next(iter(inspect.signature(templates.TemplateResponse).parameters)) != "name"


def render(name: str, context: dict, status_code: int = 200):
    if _NEW_TEMPLATE_API:
        return templates.TemplateResponse(context["request"], name, context, status_code=status_code)
    return templates.TemplateResponse(name, context, status_code=status_code)

SITE_URL = "https://www.dkcraftbbq.com"
SOCIAL = {
    "tiktok": "https://www.tiktok.com/@dkcraftbbq",
    "instagram": "https://www.instagram.com/dkcraftbbq/",
}
PER_PAGE = 12
SEASONAL = {
    "title": ("Cold", "Weather Cooks"),
    "blurb": "Below 50°F the smoke tube comes out, and the long braises taste even better.",
    "keywords": ["cold", "braise", "pastrami", "chuck"],
    "link": "/recipes",
}
SPOTLIGHT = {"title": ("Pastrami", "Recipes"), "keyword": "pastrami", "link": "/recipes?q=pastrami"}


def _recipe_view(r: models.Recipe) -> dict:
    return {
        "slug": r.slug,
        "title": r.title,
        "category": r.category,
        "category_name": category_name(r.category),
        "summary": r.summary,
        "image": r.image,
        "views": r.views,
        "needs_review": r.needs_review,
        "published_at": r.published_at,
    }


def _published(db: Session):
    # Only recipes whose videos are in one of the TikTok playlists show on the site
    return db.query(models.Recipe).filter(
        models.Recipe.is_published == True, models.Recipe.in_playlist == True
    )


def _settings(db: Session) -> dict:
    return {s.key: s.value for s in db.query(models.SiteSetting).all()}


def _ctx(request: Request, db: Session, **extra) -> dict:
    return {
        "request": request,
        "site": _settings(db),
        "categories": CATEGORIES,
        "social": SOCIAL,
        "site_url": SITE_URL,
        "year": datetime.now().year,
        **extra,
    }


_FRACTIONS = {"½": 0.5, "¼": 0.25, "¾": 0.75}
_UNIT_HOURS = {"minute": 1 / 60, "min": 1 / 60, "hour": 1, "hr": 1, "day": 24, "week": 168}


def iso_duration(text: str | None) -> str | None:
    """'About 4½ hours' -> 'PT4H30M', '10–14 hours plus hold' -> 'PT10H', '2 weeks' -> 'P14D'.
    Uses the first amount (the low end of a range); returns None if there isn't one."""
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)?\s*([½¼¾])?\s*(?:(?:–|-|to)\s*\d+[½¼¾]?\s*)?"
                  r"(minutes?|mins?|hours?|hrs?|days?|weeks?)\b", text, re.I)
    if not m or not (m.group(1) or m.group(2)):
        return None
    amount = float(m.group(1) or 0) + _FRACTIONS.get(m.group(2) or "", 0)
    unit = m.group(3).lower().rstrip("s")
    hours = amount * _UNIT_HOURS[unit]
    if hours >= 24:
        return f"P{round(hours / 24)}D"
    h, mins = int(hours), round((hours - int(hours)) * 60)
    return "PT" + (f"{h}H" if h else "") + (f"{mins}M" if mins else "") if (h or mins) else None


def fmt_views(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.1f}K".replace(".0K", "K")
    return str(n)


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


templates.env.filters["views"] = fmt_views
templates.env.filters["paragraphs"] = paragraphs
templates.env.filters["fromjson"] = lambda s: json.loads(s or "[]")


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    latest = _published(db).order_by(models.Recipe.published_at.desc())
    # The big top slot is always the newest recipe; the three beside it are the newest
    # ones ticked "Featured on home page" (topped up with the newest others if needed).
    newest = latest.first()
    featured = [newest] if newest else []
    featured += [r for r in latest.filter(models.Recipe.is_featured == True).limit(4).all() if r not in featured][:3]
    if len(featured) < 4:
        featured += [r for r in latest.limit(8).all() if r not in featured][: 4 - len(featured)]
    featured_ids = {r.id for r in featured}

    popular = _published(db).order_by(models.Recipe.views.desc()).limit(4).all()
    seasonal = (
        _published(db)
        .filter(or_(*[models.Recipe.title.ilike(f"%{k}%") for k in SEASONAL["keywords"]]))
        .order_by(models.Recipe.published_at.desc())
        .limit(5)
        .all()
    )
    spotlight = (
        _published(db)
        .filter(models.Recipe.title.ilike(f"%{SPOTLIGHT['keyword']}%"))
        .order_by(models.Recipe.views.desc())
        .limit(4)
        .all()
    )
    videos = db.query(models.Video).order_by(models.Video.posted_at.desc()).limit(4).all()
    total = _published(db).count()
    site = _settings(db)
    # Tells Google the site's name (shown above results) and links the social profiles
    site_schema = [
        {"@context": "https://schema.org", "@type": "WebSite", "name": "DKCraftBBQ",
         "alternateName": ["DK Craft BBQ", "dkcraftbbq"], "url": SITE_URL + "/"},
        {"@context": "https://schema.org", "@type": "Organization", "name": "DKCraftBBQ", "url": SITE_URL + "/",
         "sameAs": [SOCIAL["tiktok"], SOCIAL["instagram"]],
         **({"logo": SITE_URL + site["logo_path"]} if site.get("logo_path") else {})},
    ]

    return render("home.html", _ctx(
        request, db,
        featured=[_recipe_view(r) for r in featured],
        latest=[_recipe_view(r) for r in latest.limit(12).all() if r.id not in featured_ids][:6],
        popular=[_recipe_view(r) for r in popular],
        seasonal=[_recipe_view(r) for r in seasonal],
        seasonal_meta=SEASONAL,
        spotlight=[_recipe_view(r) for r in spotlight],
        spotlight_meta=SPOTLIGHT,
        videos=videos,
        total_pages=max(1, math.ceil(total / PER_PAGE)),
        site_schema=json.dumps(site_schema).replace("</", "<\\/"),
    ))


def _listing(request: Request, db: Session, query, page: int, title_parts, blurb, base_path, extra_qs=""):
    total = query.count()
    pages = max(1, math.ceil(total / PER_PAGE))
    if page < 1 or page > pages:
        raise HTTPException(status_code=404)
    rows = (
        query.order_by(models.Recipe.published_at.desc())
        .offset((page - 1) * PER_PAGE)
        .limit(PER_PAGE)
        .all()
    )
    return render("listing.html", _ctx(
        request, db,
        title_parts=title_parts,
        blurb=blurb,
        recipes=[_recipe_view(r) for r in rows],
        total=total,
        page=page,
        pages=pages,
        base_path=base_path,
        extra_qs=extra_qs,
    ))


@router.get("/recipes", response_class=HTMLResponse)
def recipe_index(request: Request, page: int = 1, q: str = "", db: Session = Depends(get_db)):
    query = _published(db)
    q = q.strip()[:100]
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            models.Recipe.title.ilike(like),
            models.Recipe.summary.ilike(like),
            models.Recipe.ingredients.ilike(like),
        ))
        heading = ("Results for", f"“{q}”")
        blurb = None
    else:
        heading = ("All", "Recipes")
        blurb = "Everything that's come off the smoker, newest first."
    from urllib.parse import quote_plus
    return _listing(request, db, query, page, heading, blurb, "/recipes",
                    extra_qs=f"&q={quote_plus(q)}" if q else "")


@router.get("/category/{slug}", response_class=HTMLResponse)
def category_page(slug: str, request: Request, page: int = 1, db: Session = Depends(get_db)):
    if slug in LEGACY_CATEGORIES:
        return RedirectResponse(f"/category/{LEGACY_CATEGORIES[slug]}", status_code=301)
    if slug not in CATEGORIES:
        raise HTTPException(status_code=404)
    name, blurb = CATEGORIES[slug]
    query = _published(db).filter(models.Recipe.category == slug)
    return _listing(request, db, query, page, (name, "Recipes"), blurb, f"/category/{slug}")


def _recipe_gear(db: Session, r: models.Recipe, ingredients: list, notes: list):
    """Shop products for the recipe card: the ones picked in admin, or else the ones the recipe
    mentions. Returns (products, {ingredient line: product}) for the box and the inline links."""
    if r.gear_ids is not None:
        ids = json.loads(r.gear_ids)
    else:
        ids = auto_gear_ids(ingredients, notes, r.wood, r.method, r.slug)
    found = {l.id: l for l in db.query(models.Link).filter(models.Link.id.in_(ids), models.Link.is_visible == True).all()} if ids else {}
    gear = [found[i] for i in ids if i in found]
    if r.gear_ids is None:
        gear = gear[:GEAR_AUTO_LIMIT]
    shown = {l.id: l for l in gear}
    return gear, {item: shown[i] for item, i in gear_item_links(ingredients, list(shown)).items()}


def _recipe_videos(db: Session, ids: list) -> list:
    """The videos a recipe page shows: every one of its videos that's in a TikTok playlist,
    oldest first. If none are, just the longest of its videos."""
    rows = db.query(models.Video).filter(models.Video.id.in_(ids)).all() if ids else []
    in_playlist = sorted((v for v in rows if v.playlist), key=lambda v: v.posted_at or datetime.min)
    if in_playlist:
        return [v.id for v in in_playlist]
    return [max(rows, key=lambda v: v.duration or 0).id] if rows else ids[:1]


@router.get("/recipe/{slug}", response_class=HTMLResponse)
def recipe_page(slug: str, request: Request, db: Session = Depends(get_db)):
    if slug in RENAMED_RECIPES:
        return RedirectResponse(f"/recipe/{RENAMED_RECIPES[slug]}", status_code=301)
    r = _published(db).filter(models.Recipe.slug == slug).first()
    if not r:
        raise HTTPException(status_code=404)
    related = (
        _published(db)
        .filter(models.Recipe.category == r.category, models.Recipe.id != r.id)
        .order_by(models.Recipe.views.desc())
        .limit(4)
        .all()
    )
    video_ids = _recipe_videos(db, json.loads(r.video_ids or "[]"))
    ingredients = json.loads(r.ingredients or "[]")
    steps = json.loads(r.steps or "[]")
    notes = json.loads(r.notes or "[]")
    gear, gear_by_item = _recipe_gear(db, r, ingredients, notes)
    # A dish built from other recipes (e.g. a sandwich) links to them instead of having its own card
    made_slugs = json.loads(r.made_with or "[]")
    made_rows = {x.slug: x for x in _published(db).filter(models.Recipe.slug.in_(made_slugs)).all()} if made_slugs else {}
    made_with = [_recipe_view(made_rows[s]) for s in made_slugs if s in made_rows]
    has_card = bool(ingredients or steps)
    schema = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": r.title,
        "description": r.summary,
        "author": {"@type": "Person", "name": "DKCraftBBQ", "url": SITE_URL + "/about"},
        "recipeCategory": category_name(r.category),
        "recipeIngredient": [i for g in ingredients for i in g.get("items", [])],
        "recipeInstructions": [{"@type": "HowToStep", "text": s} for s in steps],
    }
    if r.image:
        schema["image"] = [SITE_URL + r.image]
    if r.published_at:
        schema["datePublished"] = r.published_at.date().isoformat()
    if r.yield_text:
        schema["recipeYield"] = r.yield_text
    for key, value in (("prepTime", r.prep_time), ("cookTime", r.cook_time), ("totalTime", r.total_time)):
        if iso_duration(value):
            schema[key] = iso_duration(value)
    if r.method:
        schema["cookingMethod"] = r.method
    schema["keywords"] = f"{category_name(r.category)}, BBQ, DKCraftBBQ"
    if video_ids:
        schema["video"] = {
            "@type": "VideoObject",
            "name": r.title,
            "description": r.summary,
            "thumbnailUrl": [SITE_URL + r.image] if r.image else [],
            "embedUrl": f"https://www.tiktok.com/embed/v2/{video_ids[0]}",
            "uploadDate": r.published_at.date().isoformat() if r.published_at else None,
        }
    if not has_card:
        # Not a Recipe to Google without ingredients and steps; just describe the video
        schema = {"@context": "https://schema.org", **schema["video"]} if video_ids else None
    return render("recipe.html", _ctx(
        request, db,
        r=r,
        cat_name=category_name(r.category),
        ingredients=ingredients,
        steps=steps,
        notes=notes,
        has_card=has_card,
        made_with=made_with,
        gear=gear,
        gear_by_item=gear_by_item,
        video_ids=video_ids,
        related=[_recipe_view(x) for x in related],
        schema=json.dumps(schema).replace("</", "<\\/") if schema else None,
    ))


@router.get("/recipe/{slug}/print", response_class=HTMLResponse)
def recipe_print(slug: str, request: Request, db: Session = Depends(get_db)):
    """Just the recipe card, for printing. In-app browsers (TikTok, Instagram) ignore
    window.print(), so the page explains how to open it in a real browser there."""
    if slug in RENAMED_RECIPES:
        return RedirectResponse(f"/recipe/{RENAMED_RECIPES[slug]}/print", status_code=301)
    r = _published(db).filter(models.Recipe.slug == slug).first()
    if not r:
        raise HTTPException(status_code=404)
    if not json.loads(r.steps or "[]") and not json.loads(r.ingredients or "[]"):
        return RedirectResponse(f"/recipe/{slug}", status_code=302)  # nothing to print
    return render("recipe_print.html", _ctx(
        request, db,
        r=r,
        cat_name=category_name(r.category),
        ingredients=json.loads(r.ingredients or "[]"),
        steps=json.loads(r.steps or "[]"),
        notes=json.loads(r.notes or "[]"),
        printing=True,
    ))


@router.get("/videos", response_class=HTMLResponse)
def videos_page(request: Request, db: Session = Depends(get_db)):
    videos = db.query(models.Video).order_by(models.Video.posted_at.desc()).all()
    return render("videos.html", _ctx(request, db, videos=videos))


@router.get("/shop", response_class=HTMLResponse)
def shop_page(request: Request, db: Session = Depends(get_db)):
    sections = (
        db.query(models.Section)
        .filter(models.Section.is_visible == True)
        .order_by(models.Section.order_index)
        .all()
    )
    # The Amazon Storefront link (amazon.com/shop/...) shows as a banner, not a card
    storefront = None
    shop = []
    for s in sections:
        links = []
        for l in s.links:
            if not (l.is_visible and l.url):
                continue
            if "amazon.com/shop/" in l.url:
                storefront = storefront or l
                continue
            links.append({"id": l.id, "title": l.title, "url": l.url, "image_url": l.image_url,
                          "cta": _shop_cta(l), "photo": not _is_amazon(l.url)})
        shop.append({"title": s.title, "description": s.description, "links": links})
    return render("shop.html", _ctx(
        request, db, shop=[s for s in shop if s["links"]], storefront=storefront,
    ))


def _is_amazon(url: str) -> bool:
    return any(d in url for d in ("amazon.com", "amzn.to", "a.co/"))


def _shop_cta(link: models.Link) -> str:
    return "View on Amazon" if _is_amazon(link.url) else f"Shop {link.title}"


@router.get("/about", response_class=HTMLResponse)
def about_page(request: Request, db: Session = Depends(get_db)):
    recipe_count = _published(db).count()
    video_count = db.query(models.Video).count()
    return render("about.html", _ctx(
        request, db, recipe_count=recipe_count, video_count=video_count,
    ))


@router.get("/sitemap.xml")
def sitemap(db: Session = Depends(get_db)):
    def day(dt):
        return dt.date().isoformat() if dt else None

    # Publish dates, not updated_at: the TikTok sync touches every recipe's view count,
    # and a lastmod that's always "today" teaches search engines to ignore it.
    recipes = _published(db).with_entities(
        models.Recipe.slug, models.Recipe.category, models.Recipe.published_at).all()
    changed = {r.slug: r.published_at for r in recipes}
    newest = max(filter(None, changed.values()), default=None)
    newest_video = db.query(func.max(models.Video.posted_at)).scalar()
    newest_link = db.query(func.max(models.Link.updated_at)).scalar()

    urls = [("/", day(newest)), ("/recipes", day(newest)), ("/videos", day(newest_video)),
            ("/shop", day(newest_link)), ("/about", None)]
    for c in CATEGORIES:
        in_cat = [changed[r.slug] for r in recipes if r.category == c and changed[r.slug]]
        urls.append((f"/category/{c}", day(max(in_cat, default=None))))
    urls += [(f"/recipe/{r.slug}", day(changed[r.slug])) for r in recipes]
    body = "".join(
        f"<url><loc>{SITE_URL}{u}</loc>" + (f"<lastmod>{d}</lastmod>" if d else "") + "</url>"
        for u, d in urls
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'
    return Response(xml, media_type="application/xml")


@router.get("/robots.txt")
def robots():
    return Response(f"User-agent: *\nDisallow: /admin\nDisallow: /api/\nSitemap: {SITE_URL}/sitemap.xml\n",
                    media_type="text/plain")
