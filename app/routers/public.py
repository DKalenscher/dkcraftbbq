import hashlib
import ipaddress
import json
import time
from fastapi import APIRouter, Depends, Request, BackgroundTasks
from sqlalchemy.orm import Session
from slowapi import Limiter
from ..client_ip import client_ip

from ..database import get_db
from .. import models, schemas
from ..config import get_settings

router = APIRouter(tags=["public"])
limiter = Limiter(key_func=client_ip)
settings = get_settings()

DEFAULT_SETTINGS = {
    "site_title": "DKCraftBBQ",
    "site_tagline": "Gear I Actually Use",
    "primary_color": "#E25822",
    "secondary_color": "#8B4513",
    "background_color": "#1a1a1a",
    "background_gradient_end": "#2d1810",
    "text_color": "#f5f0eb",
    "card_background": "#2a1f1a",
    "accent_color": "#FF6B35",
    "logo_path": "",
    "custom_css": "",
    "background_style": "gradient",
}


# Visits from the owner's home network aren't recorded. The site runs at home,
# so its own public IP is the home IP; it's looked up again every 10 minutes
# because the ISP changes it from time to time.
_home_ip = {"ip": None, "checked": 0.0}


def _current_home_ip():
    if time.time() - _home_ip["checked"] > 600:
        _home_ip["checked"] = time.time()
        try:
            import httpx
            r = httpx.get("https://api.ipify.org", timeout=3)
            if r.status_code == 200:
                _home_ip["ip"] = r.text.strip()
        except Exception:
            pass  # keep the last known address
    return _home_ip["ip"]


def _is_excluded(ip: str) -> bool:
    if not settings.EXCLUDE_HOME_NETWORK:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.is_private or addr.is_loopback:
        return True
    extra = {x.strip() for x in settings.EXCLUDE_IPS.split(",") if x.strip()}
    return ip in extra or ip == _current_home_ip()


def _hash_ip(ip: str) -> str:
    return hashlib.sha256((ip + settings.SECRET_KEY).encode()).hexdigest()[:16]


def _fetch_geo(record_id: int, ip: str, model_class, db_factory):
    """Background task: look up geolocation and update record."""
    import httpx
    try:
        with httpx.Client(timeout=5) as client:
            r = client.get(f"http://ip-api.com/json/{ip}?fields=country,regionName,city,status")
            data = r.json()
        if data.get("status") == "success":
            db = db_factory()
            try:
                record = db.query(model_class).filter(model_class.id == record_id).first()
                if record:
                    record.country = data.get("country")
                    record.region = data.get("regionName")
                    record.city = data.get("city")
                    db.commit()
            finally:
                db.close()
    except Exception:
        pass


@router.get("/api/public/data")
def get_public_data(db: Session = Depends(get_db)):
    raw = db.query(models.SiteSetting).all()
    site_settings = {**DEFAULT_SETTINGS, **{s.key: s.value for s in raw}}

    sections = (
        db.query(models.Section)
        .filter(models.Section.is_visible == True)
        .order_by(models.Section.order_index)
        .all()
    )

    result = []
    for section in sections:
        links = [
            {
                "id": lnk.id,
                "title": lnk.title,
                "description": lnk.description,
                "url": lnk.url,
                "image_url": lnk.image_url,
                "order_index": lnk.order_index,
            }
            for lnk in section.links
            if lnk.is_visible
        ]
        result.append({
            "id": section.id,
            "title": section.title,
            "description": section.description,
            "order_index": section.order_index,
            "links": links,
        })

    return {"settings": site_settings, "sections": result}


@router.post("/api/track/pageview")
@limiter.limit("60/minute")
def track_pageview(
    request: Request,
    body: schemas.PageViewCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    ip = client_ip(request)
    if _is_excluded(ip):
        return {"status": "ok"}
    ip_hash = _hash_ip(ip)

    pv = models.PageView(
        ip_hash=ip_hash,
        user_agent=body.user_agent or request.headers.get("user-agent", "")[:500],
        referrer=body.referrer,
        utm_source=body.utm_source,
        utm_medium=body.utm_medium,
        utm_campaign=body.utm_campaign,
        utm_content=body.utm_content,
        utm_term=body.utm_term,
        screen_width=body.screen_width,
        screen_height=body.screen_height,
        session_id=body.session_id,
        path=(body.path or "")[:300] or None,
        search=" ".join((body.search or "").lower().split())[:200] or None,
        search_results=body.search_results,
    )
    db.add(pv)
    db.commit()
    db.refresh(pv)

    if ip not in ("127.0.0.1", "::1"):
        from ..database import SessionLocal
        background_tasks.add_task(_fetch_geo, pv.id, ip, models.PageView, SessionLocal)

    return {"status": "ok"}


@router.post("/api/track/click")
@limiter.limit("120/minute")
def track_click(
    request: Request,
    body: schemas.LinkClickCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    link = db.query(models.Link).filter(models.Link.id == body.link_id).first()
    if not link:
        return {"status": "ok"}

    ip = client_ip(request)
    if _is_excluded(ip):
        return {"status": "ok"}
    ip_hash = _hash_ip(ip)

    click = models.LinkClick(
        link_id=body.link_id,
        ip_hash=ip_hash,
        user_agent=body.user_agent or request.headers.get("user-agent", "")[:500],
        referrer=body.referrer,
        utm_source=body.utm_source,
        utm_medium=body.utm_medium,
        utm_campaign=body.utm_campaign,
        utm_content=body.utm_content,
        session_id=body.session_id,
    )
    db.add(click)
    link.click_count += 1
    db.commit()
    db.refresh(click)

    if ip not in ("127.0.0.1", "::1"):
        from ..database import SessionLocal
        background_tasks.add_task(_fetch_geo, click.id, ip, models.LinkClick, SessionLocal)

    return {"status": "ok"}
