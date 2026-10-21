"""Server-rendered public pages for the v2 recipe site."""
import inspect
import json
import math
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..content import CATEGORIES, category_name
from ..database import BASE_DIR, get_db
from .. import models

router = APIRouter(include_in_schema=False)
limiter = Limiter(key_func=get_remote_address)
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
    "categories": ["cold-smoked", "pastrami-cured"],
    "link": "/category/cold-smoked",
}


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
    return db.query(models.Recipe).filter(models.Recipe.is_published == True)


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
    featured = latest.filter(models.Recipe.is_featured == True).limit(4).all()
    if len(featured) < 4:
        taken = {r.id for r in featured}
        featured += [r for r in latest.limit(8).all() if r.id not in taken][: 4 - len(featured)]
    featured_ids = {r.id for r in featured}

    popular = _published(db).order_by(models.Recipe.views.desc()).limit(4).all()
    seasonal = (
        _published(db)
        .filter(models.Recipe.category.in_(SEASONAL["categories"]))
        .order_by(models.Recipe.published_at.desc())
        .limit(5)
        .all()
    )
    spotlight_cat = "pastrami-cured"
    spotlight = (
        _published(db)
        .filter(models.Recipe.category == spotlight_cat)
        .order_by(models.Recipe.views.desc())
        .limit(4)
        .all()
    )
    videos = db.query(models.Video).order_by(models.Video.posted_at.desc()).limit(4).all()
    total = _published(db).count()

    return render("home.html", _ctx(
        request, db,
        featured=[_recipe_view(r) for r in featured],
        latest=[_recipe_view(r) for r in latest.limit(12).all() if r.id not in featured_ids][:6],
        popular=[_recipe_view(r) for r in popular],
        seasonal=[_recipe_view(r) for r in seasonal],
        seasonal_meta=SEASONAL,
        spotlight=[_recipe_view(r) for r in spotlight],
        spotlight_cat=spotlight_cat,
        videos=videos,
        total_pages=max(1, math.ceil(total / PER_PAGE)),
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
    if slug not in CATEGORIES:
        raise HTTPException(status_code=404)
    name, blurb = CATEGORIES[slug]
    query = _published(db).filter(models.Recipe.category == slug)
    return _listing(request, db, query, page, (name, "Recipes"), blurb, f"/category/{slug}")


@router.get("/recipe/{slug}", response_class=HTMLResponse)
def recipe_page(slug: str, request: Request, db: Session = Depends(get_db)):
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
    video_ids = json.loads(r.video_ids or "[]")
    ingredients = json.loads(r.ingredients or "[]")
    steps = json.loads(r.steps or "[]")
    schema = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": r.title,
        "description": r.summary,
        "author": {"@type": "Person", "name": "DKCraftBBQ"},
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
    if video_ids:
        schema["video"] = {
            "@type": "VideoObject",
            "name": r.title,
            "description": r.summary,
            "thumbnailUrl": [SITE_URL + r.image] if r.image else [],
            "embedUrl": f"https://www.tiktok.com/embed/v2/{video_ids[0]}",
            "uploadDate": r.published_at.date().isoformat() if r.published_at else None,
        }
    return render("recipe.html", _ctx(
        request, db,
        r=r,
        cat_name=category_name(r.category),
        ingredients=ingredients,
        steps=steps,
        notes=json.loads(r.notes or "[]"),
        video_ids=video_ids,
        related=[_recipe_view(x) for x in related],
        schema=json.dumps(schema).replace("</", "<\\/"),
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
    shop = [
        {"title": s.title, "description": s.description,
         "links": [l for l in s.links if l.is_visible and l.url]}
        for s in sections
    ]
    return render("shop.html", _ctx(
        request, db, shop=[s for s in shop if s["links"]],
    ))


@router.get("/about", response_class=HTMLResponse)
def about_page(request: Request, db: Session = Depends(get_db)):
    recipe_count = _published(db).count()
    video_count = db.query(models.Video).count()
    return render("about.html", _ctx(
        request, db, recipe_count=recipe_count, video_count=video_count,
    ))


class SubscribeRequest(BaseModel):
    email: str
    source: str | None = None


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.post("/api/subscribe")
@limiter.limit("10/minute")
def subscribe(request: Request, body: SubscribeRequest, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if len(email) > 320 or not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    db.add(models.Subscriber(email=email, source=(body.source or "")[:100]))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # already subscribed — treat as success
    return {"status": "ok"}


@router.get("/sitemap.xml")
def sitemap(db: Session = Depends(get_db)):
    urls = ["/", "/recipes", "/videos", "/shop", "/about"]
    urls += [f"/category/{c}" for c in CATEGORIES]
    urls += [f"/recipe/{slug}" for (slug,) in _published(db).with_entities(models.Recipe.slug).all()]
    body = "".join(f"<url><loc>{SITE_URL}{u}</loc></url>" for u in urls)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'
    return Response(xml, media_type="application/xml")


@router.get("/robots.txt")
def robots():
    return Response(f"User-agent: *\nDisallow: /admin\nDisallow: /api/\nSitemap: {SITE_URL}/sitemap.xml\n",
                    media_type="text/plain")
