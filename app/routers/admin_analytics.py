import csv
import io
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import String, cast, func, distinct
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user

router = APIRouter(prefix="/api/admin/analytics", tags=["admin-analytics"])

# A visit is one browsing session. Every page in the site records a page view,
# so counting rows would count each click around the site as a new visit.
# Rows from before session ids existed count as their own visit.
VISIT_KEY = func.coalesce(models.PageView.session_id, cast(models.PageView.id, String))
OWN_HOSTS = ("dkcraftbbq.com",)
SOURCE_NAMES = {
    "tiktok.com": "TikTok",
    "instagram.com": "Instagram",
    "facebook.com": "Facebook",
    "t.co": "X / Twitter",
    "x.com": "X / Twitter",
    "youtube.com": "YouTube",
    "reddit.com": "Reddit",
    "pinterest.com": "Pinterest",
    "linktr.ee": "Linktree",
}
# In-app browsers name themselves in the user agent, so a tap on a link in the
# TikTok or Instagram app is recognised even though those apps send no referrer.
IN_APP_BROWSERS = [
    (("musical_ly", "BytedanceWebview", "trill_"), "TikTok"),
    (("Instagram",), "Instagram"),
    (("FBAN/", "FBAV/", "FB_IAB"), "Facebook"),
    (("Barcelona",), "Threads"),
    (("Snapchat",), "Snapchat"),
    (("Pinterest",), "Pinterest"),
]
UTM_NAMES = {"ig": "Instagram", "instagram": "Instagram", "tiktok": "TikTok",
             "fb": "Facebook", "facebook": "Facebook", "yt": "YouTube", "youtube": "YouTube"}


def _count_visits(db: Session, since: Optional[datetime] = None) -> int:
    q = db.query(func.count(distinct(VISIT_KEY)))
    if since is not None:
        q = q.filter(models.PageView.timestamp >= since)
    return q.scalar() or 0


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _is_internal(referrer: Optional[str]) -> bool:
    host = _host(referrer) if referrer else ""
    return any(host == h or host.endswith("." + h) for h in OWN_HOSTS)


def _source_label(utm_source: Optional[str], referrer: Optional[str], user_agent: Optional[str] = None) -> str:
    if utm_source:
        return UTM_NAMES.get(utm_source.strip().lower(), utm_source)
    host = _host(referrer) if referrer and not _is_internal(referrer) else ""
    if not host:
        for markers, name in IN_APP_BROWSERS:
            if user_agent and any(m in user_agent for m in markers):
                return name
        return "Direct"
    for domain, name in SOURCE_NAMES.items():
        if host == domain or host.endswith("." + domain):
            return name
    if host.startswith("google.") or ".google." in host:
        return "Google"
    if host.startswith("bing.") or host == "duckduckgo.com":
        return "Search"
    return host


@router.get("/overview", response_model=schemas.AnalyticsOverview)
def get_overview(
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=now.weekday())
    month_start = today_start.replace(day=1)

    total_visits = _count_visits(db)
    unique_visitors = db.query(func.count(distinct(models.PageView.ip_hash))).scalar() or 0
    total_clicks = db.query(func.count(models.LinkClick.id)).scalar() or 0
    visits_today = _count_visits(db, today_start)
    pageviews_today = db.query(func.count(models.PageView.id)).filter(
        models.PageView.timestamp >= today_start
    ).scalar() or 0
    clicks_today = db.query(func.count(models.LinkClick.id)).filter(
        models.LinkClick.timestamp >= today_start
    ).scalar() or 0
    visits_this_week = _count_visits(db, week_start)
    visits_this_month = _count_visits(db, month_start)

    return schemas.AnalyticsOverview(
        total_visits=total_visits,
        unique_visitors=unique_visitors,
        total_clicks=total_clicks,
        visits_today=visits_today,
        pageviews_today=pageviews_today,
        clicks_today=clicks_today,
        visits_this_week=visits_this_week,
        visits_this_month=visits_this_month,
    )


@router.get("/daily", response_model=list[schemas.DailyVisit])
def get_daily_trend(
    days: int = Query(default=30, ge=7, le=365),
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    start = datetime.utcnow() - timedelta(days=days)

    visits_raw = (
        db.query(
            func.date(models.PageView.timestamp).label("date"),
            func.count(distinct(VISIT_KEY)).label("count"),
        )
        .filter(models.PageView.timestamp >= start)
        .group_by(func.date(models.PageView.timestamp))
        .all()
    )
    clicks_raw = (
        db.query(
            func.date(models.LinkClick.timestamp).label("date"),
            func.count(models.LinkClick.id).label("count"),
        )
        .filter(models.LinkClick.timestamp >= start)
        .group_by(func.date(models.LinkClick.timestamp))
        .all()
    )

    visit_map = {str(r.date): r.count for r in visits_raw}
    click_map = {str(r.date): r.count for r in clicks_raw}

    result = []
    for i in range(days):
        date = (start + timedelta(days=i + 1)).strftime("%Y-%m-%d")
        result.append(schemas.DailyVisit(
            date=date,
            visits=visit_map.get(date, 0),
            clicks=click_map.get(date, 0),
        ))
    return result


@router.get("/top-links", response_model=list[schemas.TopLink])
def get_top_links(
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    links = (
        db.query(models.Link)
        .order_by(models.Link.click_count.desc())
        .limit(limit)
        .all()
    )
    return [
        schemas.TopLink(id=l.id, title=l.title, url=l.url, clicks=l.click_count)
        for l in links
    ]


@router.get("/traffic-sources", response_model=list[schemas.TrafficSource])
def get_traffic_sources(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    start = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(VISIT_KEY.label("visit"), models.PageView.utm_source, models.PageView.referrer,
                 models.PageView.user_agent)
        .filter(models.PageView.timestamp >= start)
        .order_by(models.PageView.timestamp, models.PageView.id)
        .all()
    )
    # One source per visit: the first page view that came from outside the site
    # (by UTM tag, outside referrer, or an app's built-in browser).
    # A visit with no outside referrer counts as Direct.
    first: dict[str, str] = {}
    for r in rows:
        label = _source_label(r.utm_source, r.referrer, r.user_agent)
        if r.visit not in first or (first[r.visit] == "Direct" and label != "Direct"):
            first[r.visit] = label
    counts: dict[str, int] = {}
    for label in first.values():
        counts[label] = counts.get(label, 0) + 1
    top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:20]
    return [schemas.TrafficSource(source=k, count=v) for k, v in top]


@router.get("/top-recipes")
def get_top_recipes(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    """Recipe pages by visits (one per visit, however many times it was reloaded), plus prints."""
    start = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(VISIT_KEY.label("visit"), models.PageView.path)
        .filter(models.PageView.timestamp >= start, models.PageView.path.like("/recipe/%"))
        .all()
    )
    views: dict[str, set] = {}
    prints: dict[str, set] = {}
    for r in rows:
        parts = r.path.strip("/").split("/")  # ["recipe", slug] or ["recipe", slug, "print"]
        if len(parts) < 2:
            continue
        bucket = prints if parts[-1] == "print" else views
        bucket.setdefault(parts[1], set()).add(r.visit)
    titles = dict(db.query(models.Recipe.slug, models.Recipe.title).all())
    out = [
        {"slug": slug, "title": titles.get(slug, slug), "views": len(views.get(slug, ())),
         "prints": len(prints.get(slug, ()))}
        for slug in set(views) | set(prints) if slug in titles
    ]
    out.sort(key=lambda r: (r["views"], r["prints"]), reverse=True)
    return out[:limit]


@router.get("/top-searches")
def get_top_searches(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    """Recipe search terms by how many visits searched them, with the result count
    (0 results = a recipe people are looking for that the site doesn't have)."""
    start = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(VISIT_KEY.label("visit"), models.PageView.search, models.PageView.search_results)
        .filter(models.PageView.timestamp >= start, models.PageView.search != None)
        .order_by(models.PageView.timestamp)
        .all()
    )
    terms: dict[str, dict] = {}
    for r in rows:
        t = terms.setdefault(r.search, {"term": r.search, "visits": set(), "results": None})
        t["visits"].add(r.visit)
        t["results"] = r.search_results  # most recent count wins
    out = [{"term": t["term"], "searches": len(t["visits"]), "results": t["results"]} for t in terms.values()]
    out.sort(key=lambda r: r["searches"], reverse=True)
    return out[:limit]


@router.get("/top-products")
def get_top_products(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    """Shop link clicks in the period (every click counts)."""
    start = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(models.Link.title, models.Section.title.label("section"), models.Link.url,
                 func.count(models.LinkClick.id).label("clicks"))
        .join(models.LinkClick, models.LinkClick.link_id == models.Link.id)
        .join(models.Section, models.Section.id == models.Link.section_id)
        .filter(models.LinkClick.timestamp >= start)
        .group_by(models.Link.id)
        .order_by(func.count(models.LinkClick.id).desc())
        .limit(limit)
        .all()
    )
    return [{"title": r.title, "section": r.section, "url": r.url, "clicks": r.clicks} for r in rows]


@router.get("/geo", response_model=list[schemas.GeoStat])
def get_geo_stats(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    start = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(
            models.PageView.country,
            func.count(distinct(VISIT_KEY)).label("count"),
        )
        .filter(models.PageView.timestamp >= start)
        .filter(models.PageView.country != None)
        .group_by(models.PageView.country)
        .order_by(func.count(distinct(VISIT_KEY)).desc())
        .limit(20)
        .all()
    )
    return [schemas.GeoStat(country=r.country, count=r.count) for r in rows]


@router.get("/utm-campaigns", response_model=list[schemas.UtmCampaignStat])
def get_utm_campaigns(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    start = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(
            models.PageView.utm_campaign,
            models.PageView.utm_source,
            func.count(distinct(VISIT_KEY)).label("count"),
        )
        .filter(models.PageView.timestamp >= start)
        .filter(models.PageView.utm_campaign != None)
        .group_by(models.PageView.utm_campaign, models.PageView.utm_source)
        .order_by(func.count(distinct(VISIT_KEY)).desc())
        .limit(20)
        .all()
    )
    return [
        schemas.UtmCampaignStat(
            campaign=r.utm_campaign or "",
            source=r.utm_source or "unknown",
            count=r.count,
        )
        for r in rows
    ]


@router.get("/export/visits")
def export_visits_csv(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    start = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(models.PageView)
        .filter(models.PageView.timestamp >= start)
        .order_by(models.PageView.timestamp.desc())
        .all()
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "timestamp", "ip_hash", "referrer", "utm_source", "utm_medium",
        "utm_campaign", "utm_content", "country", "region", "city",
        "screen_width", "screen_height", "session_id",
    ])
    for r in rows:
        writer.writerow([
            r.id, r.timestamp, r.ip_hash, r.referrer, r.utm_source, r.utm_medium,
            r.utm_campaign, r.utm_content, r.country, r.region, r.city,
            r.screen_width, r.screen_height, r.session_id,
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=visits_{days}d.csv"},
    )


@router.get("/export/clicks")
def export_clicks_csv(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    start = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(models.LinkClick, models.Link.title, models.Link.url)
        .join(models.Link, models.LinkClick.link_id == models.Link.id)
        .filter(models.LinkClick.timestamp >= start)
        .order_by(models.LinkClick.timestamp.desc())
        .all()
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "timestamp", "link_id", "link_title", "link_url",
        "utm_source", "utm_medium", "utm_campaign", "country", "city", "session_id",
    ])
    for click, link_title, link_url in rows:
        writer.writerow([
            click.id, click.timestamp, click.link_id, link_title, link_url,
            click.utm_source, click.utm_medium, click.utm_campaign,
            click.country, click.city, click.session_id,
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=clicks_{days}d.csv"},
    )
