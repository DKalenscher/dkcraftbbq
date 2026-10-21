"""Keep the site in step with TikTok. Run it on a schedule:

    python3 -m app.tiktok_sync            # normal run
    python3 -m app.tiktok_sync --dry-run  # report what would change, write nothing

Each run:
1. Reads the TikTok playlists (Beef, Pork, Poultry, and more!). Only recipes
   with a video in a playlist show on the site, and a recipe's category
   follows its playlist.
2. Refreshes the Videos page from the latest posts (new videos, view counts).
3. For each playlist video that isn't part of a recipe yet, asks Claude whether
   it continues an existing recipe or is a new one. A new recipe is drafted from
   the caption and video frames, published with "Needs review" on, and given a
   photo picked from the video.

Needs ANTHROPIC_API_KEY in .env for step 3; without it, steps 1 and 2 still run.
"""
import argparse
import base64
import fcntl
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

import httpx

from .config import get_settings
from .content import CATEGORIES, PLAYLIST_CATEGORIES, migrate_db
from .database import SessionLocal, _UPLOAD_DIR, engine
from . import models

settings = get_settings()
log = logging.getLogger("tiktok_sync")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
API = "https://www.tiktok.com/api"
MEDIA_DIR = Path(_UPLOAD_DIR)
STATUS_FILE = MEDIA_DIR.parent / "tiktok_sync_status.json"  # last run, shown in the admin panel
LOCK_FILE = MEDIA_DIR.parent / "tiktok_sync.lock"
SKIP_VIDEOS = {"7599846529133841695"}  # "test post"
FRAME_POINTS = [30, 45, 58, 68, 78, 86, 93, 98]  # % through the video


# ── TikTok ──────────────────────────────────────────────────────────────────

def _get(client: httpx.Client, url: str, **params) -> dict:
    r = client.get(url, params={"aid": 1988, **params})
    r.raise_for_status()
    return r.json()


def _sec_uid(client: httpx.Client, db) -> str:
    """The account's stable internal id, read from the profile page once and cached."""
    row = db.get(models.SiteSetting, "tiktok_sec_uid")
    if row and row.value:
        return row.value
    html = client.get(f"https://www.tiktok.com/@{settings.TIKTOK_USERNAME}").text
    m = re.search(r'"secUid":"([^"]+)"', html)
    if not m:
        raise RuntimeError("couldn't find the TikTok account id on the profile page")
    db.merge(models.SiteSetting(key="tiktok_sec_uid", value=m.group(1)))
    db.commit()
    return m.group(1)


def fetch_playlists(client: httpx.Client, sec_uid: str) -> list[dict]:
    """[{name, category, items: [tiktok item]}] for every public playlist."""
    out, cursor = [], 0
    while True:
        d = _get(client, f"{API}/user/playlist/", secUid=sec_uid, count=20, cursor=cursor)
        for p in d.get("playList") or []:
            name = p.get("name", "")
            items, c = [], 0
            while True:
                page = _get(client, f"{API}/mix/item_list/", mixId=p["mixId"], count=30, cursor=c)
                items += page.get("itemList") or []
                if not page.get("hasMore"):
                    break
                c = page.get("cursor")
            if p.get("videoCount") and not items:
                raise RuntimeError(f"TikTok returned no videos for playlist {name!r}")
            out.append({
                "name": name,
                "category": PLAYLIST_CATEGORIES.get(name.strip().lower(), "more"),
                "items": items,
            })
        if not d.get("hasMore"):
            break
        cursor = d.get("cursor")
    if not out:
        raise RuntimeError("TikTok returned no playlists")  # never hide every recipe on a bad read
    return out


def fetch_recent_posts(limit: int = 60) -> list[dict]:
    """Latest posts from the profile, via yt-dlp (TikTok's post list API needs signing)."""
    cmd = [settings.YTDLP_PATH, "--flat-playlist", "-j", "--playlist-end", str(limit),
           f"https://www.tiktok.com/@{settings.TIKTOK_USERNAME}"]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    posts = []
    for line in res.stdout.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        cover = next((t["url"] for t in d.get("thumbnails") or [] if t.get("id") == "cover"), None)
        posts.append({
            "id": str(d["id"]),
            "caption": d.get("description") or "",
            "posted_at": datetime.fromtimestamp(int(d["timestamp"])) if d.get("timestamp") else None,
            "duration": d.get("duration"),
            "views": d.get("view_count") or 0,
            "likes": d.get("like_count") or 0,
            "cover": cover,
        })
    if not posts:
        log.warning("yt-dlp returned no posts: %s", res.stderr.strip()[-300:])
    return posts


def _item_post(item: dict) -> dict:
    """A playlist API item in the same shape as fetch_recent_posts()."""
    video, stats = item.get("video") or {}, item.get("stats") or {}
    return {
        "id": str(item["id"]),
        "caption": item.get("desc") or "",
        "posted_at": datetime.fromtimestamp(int(item["createTime"])) if item.get("createTime") else None,
        "duration": video.get("duration"),
        "views": stats.get("playCount") or 0,
        "likes": stats.get("diggCount") or 0,
        "cover": video.get("cover") or video.get("originCover"),
    }


def _feed_worthy(post: dict) -> bool:
    cap = post["caption"].strip()
    if post["id"] in SKIP_VIDEOS or not cap:
        return False
    return not (cap.startswith("Replying to") and len(cap) < 40)


# ── Media ───────────────────────────────────────────────────────────────────

def _ffmpeg(*args):
    return subprocess.run([settings.FFMPEG_PATH, "-loglevel", "error", "-y", *args],
                          capture_output=True, text=True, timeout=300)


def save_thumb(client: httpx.Client, video_id: str, cover_url: str | None) -> str | None:
    if not cover_url:
        return None
    out = MEDIA_DIR / "videos" / f"{video_id}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = client.get(cover_url)
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("thumbnail for %s failed: %s", video_id, e)
        return None
    with tempfile.NamedTemporaryFile(suffix=".img") as tmp:
        tmp.write(r.content)
        tmp.flush()
        _ffmpeg("-i", tmp.name, "-vf", "scale=360:-2", "-q:v", "4", str(out))
    return f"/uploads/videos/{video_id}.jpg" if out.exists() else None


def download_video(video_id: str, workdir: Path) -> Path | None:
    out = workdir / f"{video_id}.mp4"
    subprocess.run(
        [settings.YTDLP_PATH, "-q", "-f", "best[height<=1280]/best", "-o", str(out),
         f"https://www.tiktok.com/@{settings.TIKTOK_USERNAME}/video/{video_id}"],
        capture_output=True, text=True, timeout=600,
    )
    return out if out.exists() and out.stat().st_size > 0 else None


def _duration(path: Path) -> float:
    res = subprocess.run([settings.FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=60)
    try:
        return float(res.stdout.strip())
    except ValueError:
        return 30.0


def extract_frames(video: Path, workdir: Path) -> list[Path]:
    dur = _duration(video)
    frames = []
    for i, pct in enumerate(FRAME_POINTS):
        f = workdir / f"frame_{i}.jpg"
        _ffmpeg("-ss", f"{dur * pct / 100:.2f}", "-i", str(video), "-frames:v", "1",
                "-vf", "scale=540:-2", "-q:v", "4", str(f))
        if f.exists():
            frames.append(f)
    return frames


def save_hero(video: Path, frame_index: int, slug: str, workdir: Path) -> str | None:
    """Full-resolution frame, letterbox bars removed, cropped to 3:4 at 720x960."""
    dur = _duration(video)
    pct = FRAME_POINTS[max(0, min(frame_index, len(FRAME_POINTS) - 1))]
    raw = workdir / "hero.png"
    _ffmpeg("-ss", f"{dur * pct / 100:.2f}", "-i", str(video), "-frames:v", "1", str(raw))
    if not raw.exists():
        return None
    cd = subprocess.run([settings.FFMPEG_PATH, "-i", str(raw), "-vf", "cropdetect=limit=24:round=2",
                         "-f", "null", "-"], capture_output=True, text=True).stderr
    crops = re.findall(r"crop=(\d+:\d+:\d+:\d+)", cd)
    vf = (f"crop={crops[-1]}," if crops else "") + "crop='min(iw,ih*3/4)':'min(ih,iw*4/3)',scale=720:960"
    out = MEDIA_DIR / "recipes" / f"{slug}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg("-i", str(raw), "-vf", vf, "-q:v", "4", str(out))
    return f"/uploads/recipes/{slug}.jpg" if out.exists() else None


# ── Claude ──────────────────────────────────────────────────────────────────

STYLE_GUIDE = """You write recipes for DKCraftBBQ, a backyard BBQ site. Every recipe comes from one of the
pitmaster's TikTok videos. Write in his voice: first person, plain and friendly, no hype words, no
emojis, short paragraphs.

Rules:
- Facts in the caption or visible in the frames (temps, times, rubs, brands, gear, steps) are his.
  Keep them exactly.
- Where the caption leaves gaps (amounts, times, temps, servings), fill them with standard,
  food-safe values a careful pitmaster would use. Never present a filled-in value as something he
  said.
- review_notes must say plainly what came from the caption and what you filled in, for example:
  "Rub, 225°F and the butter rest are from your caption. Weight, cook time and servings are filled in."
- Don't invent stories, family history, quotes, opinions or results he didn't state.
- Food safety: poultry to 165°F, ground meat to 160°F, whole pork cuts to 145°F. For curing salt,
  tell readers to follow the package label. Don't repeat unsafe storage claims from a caption;
  soften them and add a note.
- Temperatures in °F. Article: 2 to 4 short paragraphs; a line starting with "## " is a subheading.
- Ingredients are grouped (e.g. "Rub", "Brine"); use an empty group name when there's only one group.
"""

DECIDE_TOOL = {
    "name": "decide",
    "description": "Decide what to do with a new playlist video.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["attach", "new", "skip"],
                       "description": "attach: another part of an existing recipe's cook. new: a recipe of its own. "
                                      "skip: no recipe content at all (e.g. a channel announcement)."},
            "slug": {"type": "string", "description": "For attach: the existing recipe's slug."},
            "reason": {"type": "string"},
        },
        "required": ["action", "reason"],
    },
}

RECIPE_TOOL = {
    "name": "save_recipe",
    "description": "Save the drafted recipe.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "summary": {"type": "string", "description": "One or two sentences for cards and search results."},
            "article": {"type": "string"},
            "ingredients": {"type": "array", "items": {
                "type": "object",
                "properties": {"group": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}}},
                "required": ["group", "items"]}},
            "steps": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "array", "items": {"type": "string"}},
            "prep_time": {"type": "string"},
            "cook_time": {"type": "string"},
            "total_time": {"type": "string"},
            "yield_text": {"type": "string"},
            "method": {"type": "string", "description": "e.g. Smoked, Grilled, Smoked & Braised, Cold Smoked"},
            "wood": {"type": "string", "description": "Wood or pellets used, or 'Your choice' if not stated."},
            "review_notes": {"type": "string"},
            "hero_frame": {"type": "integer", "description": "Index of the frame that makes the best photo: "
                           "finished, appetizing food, ideally sliced or plated; avoid raw meat and heavy text."},
        },
        "required": ["title", "summary", "article", "ingredients", "steps", "notes", "review_notes", "hero_frame"],
    },
}


def claude(tool: dict, content: list) -> dict:
    r = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": settings.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
        json={
            "model": settings.CLAUDE_MODEL,
            "max_tokens": 8000,
            "system": STYLE_GUIDE,
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": tool["name"]},
            "messages": [{"role": "user", "content": content}],
        },
        timeout=300,
    )
    r.raise_for_status()
    for block in r.json()["content"]:
        if block.get("type") == "tool_use":
            return block["input"]
    raise RuntimeError("Claude didn't return a result")


def _recipe_catalog(db) -> str:
    lines = []
    for r in db.query(models.Recipe).order_by(models.Recipe.published_at.desc()):
        lines.append(f"- {r.slug}: {r.title} ({r.category})")
    return "\n".join(lines)


def decide(db, post: dict, category: str) -> dict:
    return claude(DECIDE_TOOL, [{"type": "text", "text": (
        f"A new video was added to the {CATEGORIES[category][0]} playlist.\n\n"
        f"Caption:\n{post['caption']}\n\n"
        "Existing recipes (slug: title (category)):\n" + _recipe_catalog(db) + "\n\n"
        "Is this video another part of one of these cooks (prep, progress, results, slicing, a follow-up "
        "of the same dish), a new recipe, or not a recipe at all? Only attach when it's clearly the same dish."
    )}])


def _examples(db) -> str:
    rows = (db.query(models.Recipe)
            .filter(models.Recipe.needs_review == False, models.Recipe.in_playlist == True)
            .order_by(models.Recipe.views.desc()).limit(2).all())
    keep = ["title", "summary", "article", "ingredients", "steps", "notes", "prep_time", "cook_time",
            "total_time", "yield_text", "method", "wood", "review_notes"]
    out = []
    for r in rows:
        d = {k: getattr(r, k) for k in keep}
        for k in ("ingredients", "steps", "notes"):
            d[k] = json.loads(d[k] or "[]")
        out.append(json.dumps(d, ensure_ascii=False, indent=1))
    return "\n\n".join(out)


def draft_recipe(db, post: dict, category: str, frames: list[Path]) -> dict:
    content: list = [{"type": "text", "text": (
        f"Draft a recipe for this {CATEGORIES[category][0]} video.\n\n"
        f"Caption:\n{post['caption']}\n\n"
        f"{len(frames)} frames from the video follow, in order (index 0 to {len(frames) - 1}). "
        "Use any on-screen text or visible steps as facts, and pick the best one for the photo.\n\n"
        "Two published recipes, for voice and level of detail:\n" + _examples(db)
    )}]
    for i, f in enumerate(frames):
        content.append({"type": "text", "text": f"Frame {i}:"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(f.read_bytes()).decode()}})
    return claude(RECIPE_TOOL, content)


def _slugify(title: str, db) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80] or "recipe"
    slug, n = base, 2
    while db.query(models.Recipe).filter(models.Recipe.slug == slug).first():
        slug, n = f"{base}-{n}", n + 1
    return slug


# ── Sync ────────────────────────────────────────────────────────────────────

def _upsert_video(db, client, post: dict, playlist: str | None, dry: bool) -> bool:
    """Returns True when the video is new to the site."""
    row = db.get(models.Video, post["id"])
    new = row is None
    if dry:
        return new
    if new:
        row = models.Video(id=post["id"], caption=post["caption"], posted_at=post["posted_at"],
                           duration=post["duration"])
        db.add(row)
    row.views = int(post["views"] or row.views or 0)
    row.likes = int(post["likes"] or row.likes or 0)
    row.playlist = playlist
    if not row.thumb:
        row.thumb = save_thumb(client, post["id"], post["cover"])
    return new


def sync(dry: bool = False) -> dict:
    report = {"new_videos": 0, "attached": [], "new_recipes": [], "hidden": [], "shown": [], "errors": []}
    db = SessionLocal()
    client = httpx.Client(headers={"User-Agent": UA}, timeout=30, follow_redirects=True)
    try:
        playlists = fetch_playlists(client, _sec_uid(client, db))
        in_playlist: dict[str, str] = {}   # video id -> category
        playlist_posts: dict[str, dict] = {}
        for p in playlists:
            for item in p["items"]:
                post = _item_post(item)
                in_playlist.setdefault(post["id"], p["category"])
                playlist_posts[post["id"]] = post
        log.info("playlists: %s", ", ".join(f"{p['name']} ({len(p['items'])})" for p in playlists))

        # Videos page: latest posts plus everything in a playlist
        feed = {p["id"]: p for p in fetch_recent_posts() if _feed_worthy(p)}
        feed.update(playlist_posts)
        for post in feed.values():
            if _upsert_video(db, client, post, in_playlist.get(post["id"]), dry):
                report["new_videos"] += 1
        if not dry:
            db.query(models.Video).filter(models.Video.id.notin_(list(in_playlist))).update(
                {models.Video.playlist: None}, synchronize_session=False)
            db.commit()

        # Existing recipes: visibility and category follow the playlists
        recipes = db.query(models.Recipe).all()
        owner = {}  # video id -> recipe
        for r in recipes:
            for vid in json.loads(r.video_ids or "[]"):
                owner.setdefault(vid, r)
        for r in recipes:
            _refresh_recipe(db, r, in_playlist, report, dry)

        # Playlist videos that aren't part of any recipe yet, oldest first
        pending = sorted((v for v in in_playlist if v not in owner),
                         key=lambda v: playlist_posts[v]["posted_at"] or datetime.min)
        if pending and dry:
            report["pending"] = pending  # dry runs don't call Claude
            pending = []
        if pending and not settings.ANTHROPIC_API_KEY:
            log.warning("%d new playlist video(s) but ANTHROPIC_API_KEY isn't set; skipping recipe drafts",
                        len(pending))
            pending = []
        for vid in pending:
            try:
                _handle_new_video(db, client, playlist_posts[vid], in_playlist, report, dry)
            except Exception as e:  # one bad video shouldn't stop the rest
                db.rollback()
                log.exception("video %s failed", vid)
                report["errors"].append(f"{vid}: {e}")
        if not dry:
            db.commit()
    finally:
        client.close()
        db.close()
    return report


def _refresh_recipe(db, r, in_playlist: dict, report: dict, dry: bool):
    vids = json.loads(r.video_ids or "[]")
    cats = [in_playlist[v] for v in vids if v in in_playlist]
    visible = bool(cats)
    if visible != r.in_playlist:
        report["shown" if visible else "hidden"].append(r.slug)
    if dry:
        return
    r.in_playlist = visible
    if cats:
        r.category = Counter(cats).most_common(1)[0][0]
    views = sum(v.views for v in db.query(models.Video).filter(models.Video.id.in_(vids)))
    if views:
        r.views = views
    for v in db.query(models.Video).filter(models.Video.id.in_(vids)):
        v.recipe_slug = r.slug


def _handle_new_video(db, client, post: dict, in_playlist: dict, report: dict, dry: bool):
    category = in_playlist[post["id"]]
    choice = decide(db, post, category)
    log.info("video %s -> %s %s (%s)", post["id"], choice["action"], choice.get("slug", ""), choice["reason"])
    if choice["action"] == "skip":
        return
    if choice["action"] == "attach":
        r = db.query(models.Recipe).filter(models.Recipe.slug == choice.get("slug")).first()
        if r:
            report["attached"].append(f"{post['id']} -> {r.slug}")
            if not dry:
                r.video_ids = json.dumps(json.loads(r.video_ids or "[]") + [post["id"]])
                _refresh_recipe(db, r, in_playlist, report, dry)
                db.commit()
            return
        log.warning("Claude picked unknown recipe %r; drafting a new one instead", choice.get("slug"))

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        video = download_video(post["id"], work)
        frames = extract_frames(video, work) if video else []
        d = draft_recipe(db, post, category, frames)
        slug = _slugify(d["title"], db)
        report["new_recipes"].append(slug)
        if dry:
            return
        image = save_hero(video, d.get("hero_frame", 0), slug, work) if video else None
        if not image:
            v = db.get(models.Video, post["id"])
            image = v.thumb if v else None
    db.add(models.Recipe(
        slug=slug, title=d["title"], category=category,
        summary=d["summary"], article=d["article"],
        ingredients=json.dumps(d["ingredients"]), steps=json.dumps(d["steps"]), notes=json.dumps(d["notes"]),
        prep_time=d.get("prep_time"), cook_time=d.get("cook_time"), total_time=d.get("total_time"),
        yield_text=d.get("yield_text"), method=d.get("method"), wood=d.get("wood"),
        image=image, video_ids=json.dumps([post["id"]]), views=int(post["views"] or 0),
        needs_review=True, review_notes="Drafted automatically from the TikTok caption. " + d["review_notes"],
        is_published=True, is_featured=False, in_playlist=True,
        published_at=post["posted_at"] or datetime.now(),
    ))
    v = db.get(models.Video, post["id"])
    if v:
        v.recipe_slug = slug
    db.commit()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    for tool in (settings.YTDLP_PATH, settings.FFMPEG_PATH, settings.FFPROBE_PATH):
        if not shutil.which(tool):
            log.error("missing tool: %s", tool)
            sys.exit(1)

    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.info("another sync is running; exiting")
        return
    models.Base.metadata.create_all(bind=engine)  # same schema setup the web app does on start
    migrate_db()
    status = {"started_at": datetime.now().isoformat(timespec="seconds"), "dry_run": args.dry_run}
    try:
        status["report"] = sync(dry=args.dry_run)
        status["ok"] = not status["report"]["errors"]
        log.info("result: %s", json.dumps(status["report"]))
    except Exception as e:
        log.exception("sync failed")
        status.update(ok=False, error=str(e))
    status["finished_at"] = datetime.now().isoformat(timespec="seconds")
    if not args.dry_run:
        STATUS_FILE.write_text(json.dumps(status, indent=1))
    if not status["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
