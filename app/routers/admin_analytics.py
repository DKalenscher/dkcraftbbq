import csv
import io
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, distinct
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user

router = APIRouter(prefix="/api/admin/analytics", tags=["admin-analytics"])


@router.get("/overview", response_model=schemas.AnalyticsOverview)
def get_overview(
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=now.weekday())
    month_start = today_start.replace(day=1)

    total_visits = db.query(func.count(models.PageView.id)).scalar() or 0
    unique_visitors = db.query(func.count(distinct(models.PageView.ip_hash))).scalar() or 0
    total_clicks = db.query(func.count(models.LinkClick.id)).scalar() or 0
    visits_today = db.query(func.count(models.PageView.id)).filter(
        models.PageView.timestamp >= today_start
    ).scalar() or 0
    clicks_today = db.query(func.count(models.LinkClick.id)).filter(
        models.LinkClick.timestamp >= today_start
    ).scalar() or 0
    visits_this_week = db.query(func.count(models.PageView.id)).filter(
        models.PageView.timestamp >= week_start
    ).scalar() or 0
    visits_this_month = db.query(func.count(models.PageView.id)).filter(
        models.PageView.timestamp >= month_start
    ).scalar() or 0

    return schemas.AnalyticsOverview(
        total_visits=total_visits,
        unique_visitors=unique_visitors,
        total_clicks=total_clicks,
        visits_today=visits_today,
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
            func.count(models.PageView.id).label("count"),
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
        db.query(
            func.coalesce(models.PageView.utm_source, models.PageView.referrer, "Direct").label("source"),
            func.count(models.PageView.id).label("count"),
        )
        .filter(models.PageView.timestamp >= start)
        .group_by("source")
        .order_by(func.count(models.PageView.id).desc())
        .limit(20)
        .all()
    )
    return [schemas.TrafficSource(source=r.source or "Direct", count=r.count) for r in rows]


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
            func.count(models.PageView.id).label("count"),
        )
        .filter(models.PageView.timestamp >= start)
        .filter(models.PageView.country != None)
        .group_by(models.PageView.country)
        .order_by(func.count(models.PageView.id).desc())
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
            func.count(models.PageView.id).label("count"),
        )
        .filter(models.PageView.timestamp >= start)
        .filter(models.PageView.utm_campaign != None)
        .group_by(models.PageView.utm_campaign, models.PageView.utm_source)
        .order_by(func.count(models.PageView.id).desc())
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
