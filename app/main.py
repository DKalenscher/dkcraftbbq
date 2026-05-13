import asyncio
import hashlib
import hmac
import json
import logging
import os
import warnings

# Suppress passlib/bcrypt version warning (bcrypt 5.x dropped __about__)
warnings.filterwarnings("ignore", ".*error reading bcrypt version.*")
warnings.filterwarnings("ignore", ".*trapped.*bcrypt.*")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .config import get_settings
from .database import BASE_DIR, Base, engine, SessionLocal, _UPLOAD_DIR
from . import models
from .auth import hash_password
from .routers import (
    auth_router,
    public,
    admin_sections,
    admin_links,
    admin_analytics,
    admin_settings,
    amazon,
)

settings = get_settings()

DEFAULT_SITE_SETTINGS = {
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


def _initialize_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not db.query(models.AdminUser).first():
            admin = models.AdminUser(
                username=settings.ADMIN_USERNAME,
                password_hash=hash_password(settings.ADMIN_PASSWORD),
            )
            db.add(admin)

        existing_keys = {s.key for s in db.query(models.SiteSetting).all()}
        for key, value in DEFAULT_SITE_SETTINGS.items():
            if key not in existing_keys:
                db.add(models.SiteSetting(key=key, value=str(value)))

        db.commit()
    finally:
        db.close()


# Run DB init at import time — avoids lifespan compatibility issues with newer FastAPI
_initialize_db()

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="DKCraftBBQ",
    docs_url=None,
    redoc_url=None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth_router.router)
app.include_router(public.router)
app.include_router(admin_sections.router)
app.include_router(admin_links.router)
app.include_router(admin_analytics.router)
app.include_router(admin_settings.router)
app.include_router(amazon.router)

async def _restart_service():
    await asyncio.sleep(1)
    env = {**os.environ, "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}
    await asyncio.create_subprocess_exec(
        "systemctl", "--user", "restart", "dkcraftbbq.service",
        env=env,
    )


@app.post("/webhook/github", include_in_schema=False)
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    secret = settings.WEBHOOK_SECRET
    if not secret:
        raise HTTPException(status_code=503, detail="WEBHOOK_SECRET not configured")

    body = await request.body()
    sig_header = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig_header, expected):
        raise HTTPException(status_code=403, detail="Invalid signature")

    event = request.headers.get("X-GitHub-Event", "")
    if event != "push":
        return {"status": "ignored", "event": event}

    payload = json.loads(body)
    ref = payload.get("ref", "")
    if not ref.endswith(("/master", "/main")):
        return {"status": "ignored", "ref": ref}

    proc = await asyncio.create_subprocess_exec(
        "git", "pull",
        cwd=str(BASE_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    logging.getLogger(__name__).info("git pull: %s", stdout.decode().strip())

    if proc.returncode == 0:
        background_tasks.add_task(_restart_service)

    return {
        "status": "ok" if proc.returncode == 0 else "error",
        "output": stdout.decode(),
        "stderr": stderr.decode(),
    }


# /admin serves the admin panel (nicer URL than /admin.html)
_STATIC_DIR = BASE_DIR / "static"

@app.get("/admin", include_in_schema=False)
async def admin_page():
    return FileResponse(str(_STATIC_DIR / "admin.html"))

app.mount("/uploads", StaticFiles(directory=_UPLOAD_DIR), name="uploads")
app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
