"""Keep the site in step with TikTok. Runs on the Pi from cron:

    python3 -m app.tiktok_sync                   # sync playlists and videos
    python3 -m app.tiktok_sync --dry-run         # report what would change, write nothing
    python3 -m app.tiktok_sync --export-pending  # sync, then print videos that need a recipe (JSON)
    python3 -m app.tiktok_sync --apply FILE      # save a recipe drafted by tools/pc_sync.py

Each sync reads the TikTok playlists (Beef, Pork, Poultry, and more!). Only
recipes with a video in a playlist show on the site, and a recipe's category
follows its playlist. It also refreshes the Videos page from the latest posts.

Playlist videos that aren't part of a recipe yet are left pending. Writing
recipes needs Claude, which runs on the PC (tools/pc_sync.py): it fetches the
pending list over SSH, drafts with Claude Code, and sends the result back
with --apply.
"""
import argparse
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


def save_hero(video: Path, frame_index: int, out: Path, workdir: Path) -> bool:
    """Full-resolution frame, letterbox bars removed, cropped to 3:4 at 720x960."""
    dur = _duration(video)
    pct = FRAME_POINTS[max(0, min(frame_index, len(FRAME_POINTS) - 1))]
    raw = workdir / "hero.png"
    _ffmpeg("-ss", f"{dur * pct / 100:.2f}", "-i", str(video), "-frames:v", "1", str(raw))
    if not raw.exists():
        return False
    cd = subprocess.run([settings.FFMPEG_PATH, "-i", str(raw), "-vf", "cropdetect=limit=24:round=2",
                         "-f", "null", "-"], capture_output=True, text=True).stderr
    crops = re.findall(r"crop=(\d+:\d+:\d+:\d+)", cd)
    vf = (f"crop={crops[-1]}," if crops else "") + "crop='min(iw,ih*3/4)':'min(ih,iw*4/3)',scale=720:960"
    out.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg("-i", str(raw), "-vf", vf, "-q:v", "4", str(out))
    return out.exists()


# ── Recipe drafting (prompts used by tools/pc_sync.py) ──────────────────────────────────────────────────────────────────

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

DECIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["attach", "new", "skip"],
                   "description": "attach: another part of an existing recipe's cook. new: a recipe of its own. "
                                  "skip: no recipe content at all (e.g. a channel announcement)."},
        "slug": {"type": "string", "description": "For attach: the existing recipe's slug."},
        "reason": {"type": "string"},
    },
    "required": ["action", "reason"],
}

RECIPE_SCHEMA = {
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
}


def decide_prompt(post: dict, category: str, catalog: str) -> str:
    return (
        f"A new video was added to the {CATEGORIES[category][0]} playlist.\n\n"
        f"Caption:\n{post['caption']}\n\n"
        "Existing recipes (slug: title (category)):\n" + catalog + "\n\n"
        "Is this video another part of one of these cooks (prep, progress, results, slicing, a follow-up "
        "of the same dish), a new recipe, or not a recipe at all? Only attach when it's clearly the same dish."
    )


def draft_prompt(post: dict, category: str, examples: str, frames: list[str]) -> str:
    listing = "\n".join(f"- Frame {i}: {name}" for i, name in enumerate(frames))
    return (
        f"Draft a recipe for this {CATEGORIES[category][0]} video.\n\n"
        f"Caption:\n{post['caption']}\n\n"
        f"Frames from the video, in order. Read every one: use on-screen text or visible steps as facts, "
        f"and pick the best one for the photo (hero_frame is its index).\n{listing}\n\n"
        "Two published recipes, for voice and level of detail:\n" + examples
    )


def _recipe_catalog(db) -> str:
    lines = []
    for r in db.query(models.Recipe).order_by(models.Recipe.published_at.desc()):
        lines.append(f"- {r.slug}: {r.title} ({r.category})")
    return "\n".join(lines)


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


def _skipped(db) -> set[str]:
    """Playlist videos Claude judged not to be recipes, so they aren't asked about again."""
    row = db.get(models.SiteSetting, "tiktok_skipped_videos")
    return set(json.loads(row.value)) if row and row.value else set()


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
    report = {"new_videos": 0, "attached": [], "new_recipes": [], "hidden": [], "shown": [],
              "errors": [], "pending": []}
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
        owned = {vid for r in recipes for vid in json.loads(r.video_ids or "[]")}
        for r in recipes:
            _refresh_recipe(db, r, in_playlist, report, dry)
        if not dry:
            db.commit()

        # Playlist videos that aren't part of any recipe yet, oldest first
        skipped = _skipped(db)
        pending = sorted((v for v in in_playlist if v not in owned and v not in skipped),
                         key=lambda v: playlist_posts[v]["posted_at"] or datetime.min)
        report["pending"] = [{"post": {**playlist_posts[v], "posted_at": _iso(playlist_posts[v]["posted_at"])},
                              "category": in_playlist[v]} for v in pending]
    finally:
        client.close()
        db.close()
    return report


def _iso(dt):
    return dt.isoformat() if dt else None


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
    videos = db.query(models.Video).filter(models.Video.id.in_(vids)).all()
    views = sum(v.views for v in videos)
    if views:
        r.views = views
    for v in videos:
        v.recipe_slug = r.slug


def apply(result: dict) -> dict:
    """Save one drafted result from the PC:
    {"post": {...}, "category": str, "decision": {...}, "recipe": {...} | null, "image": path | null}
    """
    report = {"attached": [], "new_recipes": [], "hidden": [], "shown": [], "errors": []}
    post, category, choice = result["post"], result["category"], result["decision"]
    db = SessionLocal()
    try:
        if any(post["id"] in json.loads(r.video_ids or "[]") for r in db.query(models.Recipe)):
            log.info("video %s already belongs to a recipe; nothing to do", post["id"])
            return report
        # Playlists as of the last sync, so attaching doesn't miscount a recipe's other videos
        in_playlist = {v.id: v.playlist for v in db.query(models.Video).filter(models.Video.playlist != None)}
        in_playlist.setdefault(post["id"], category)

        if choice["action"] == "skip":
            log.info("video %s skipped: %s", post["id"], choice.get("reason"))
            skipped = _skipped(db) | {post["id"]}
            db.merge(models.SiteSetting(key="tiktok_skipped_videos", value=json.dumps(sorted(skipped))))
            db.commit()
            return report
        if choice["action"] == "attach":
            r = db.query(models.Recipe).filter(models.Recipe.slug == choice.get("slug")).first()
            if not r:
                raise ValueError(f"unknown recipe {choice.get('slug')!r}")
            r.video_ids = json.dumps(json.loads(r.video_ids or "[]") + [post["id"]])
            _refresh_recipe(db, r, in_playlist, report, False)
            db.commit()
            report["attached"].append(f"{post['id']} -> {r.slug}")
            return report

        d = result["recipe"]
        slug = _slugify(d["title"], db)
        image = None
        if result.get("image") and Path(result["image"]).exists():
            dest = MEDIA_DIR / "recipes" / f"{slug}.jpg"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(result["image"], dest)
            image = f"/uploads/recipes/{slug}.jpg"
        v = db.get(models.Video, post["id"])
        if not image and v:
            image = v.thumb
        db.add(models.Recipe(
            slug=slug, title=d["title"], category=category,
            summary=d["summary"], article=d["article"],
            ingredients=json.dumps(d["ingredients"]), steps=json.dumps(d["steps"]), notes=json.dumps(d["notes"]),
            prep_time=d.get("prep_time"), cook_time=d.get("cook_time"), total_time=d.get("total_time"),
            yield_text=d.get("yield_text"), method=d.get("method"), wood=d.get("wood"),
            image=image, video_ids=json.dumps([post["id"]]), views=int(post.get("views") or 0),
            needs_review=True, review_notes="Drafted automatically from the TikTok caption. " + d["review_notes"],
            is_published=True, is_featured=False, in_playlist=True,
            published_at=datetime.fromisoformat(post["posted_at"]) if post.get("posted_at") else datetime.now(),
        ))
        if v:
            v.recipe_slug = slug
        db.commit()
        report["new_recipes"].append(slug)
        return report
    finally:
        db.close()


def context() -> dict:
    """What the PC needs to draft: existing recipes and two examples of the house style."""
    db = SessionLocal()
    try:
        return {"catalog": _recipe_catalog(db), "examples": _examples(db)}
    finally:
        db.close()


def _write_status(status: dict):
    status["finished_at"] = datetime.now().isoformat(timespec="seconds")
    STATUS_FILE.write_text(json.dumps(status, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    mode.add_argument("--export-pending", action="store_true",
                      help="sync, then print pending videos and drafting context as JSON on stdout")
    mode.add_argument("--apply", metavar="FILE", help="save a drafted result (JSON) from tools/pc_sync.py")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        stream=sys.stderr)
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
        sys.exit(75)  # EX_TEMPFAIL: the PC script retries next time
    models.Base.metadata.create_all(bind=engine)  # same schema setup the web app does on start
    migrate_db()

    status = {"started_at": datetime.now().isoformat(timespec="seconds")}
    if args.apply:
        try:
            report = apply(json.loads(Path(args.apply).read_text()))
        except Exception as e:
            log.exception("apply failed")
            report = {"attached": [], "new_recipes": [], "hidden": [], "shown": [], "errors": [str(e)]}
        log.info("applied: %s", json.dumps(report))
        status.update(ok=not report["errors"], report=report, source="pc")
        _write_status(status)
        sys.exit(0 if status["ok"] else 1)

    try:
        report = sync(dry=args.dry_run)
        status.update(ok=True, report=report)
        log.info("result: %s", json.dumps({**report, "pending": [p["post"]["id"] for p in report["pending"]]}))
    except Exception as e:
        log.exception("sync failed")
        status.update(ok=False, error=str(e))
    if not args.dry_run:
        _write_status(status)
    if args.export_pending and status["ok"]:
        print(json.dumps({"pending": status["report"]["pending"], **context()}))
    if not status["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
