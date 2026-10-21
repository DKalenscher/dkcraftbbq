"""Write recipes for new TikTok playlist videos with Claude Code, on this PC.

Windows Task Scheduler runs tools/pc_sync.sh every few hours. Each run:
1. Asks the Pi (over SSH) to sync with TikTok and list playlist videos that
   don't have a recipe yet. If there are none, it stops without starting Claude.
2. For each one, runs Claude Code headless (`claude -p`) to decide whether the
   video continues an existing recipe or is a new one.
3. For a new recipe, downloads the video, pulls frames, has Claude Code read
   them and draft the recipe, and crops the photo Claude picked.
4. Sends the result and photo back to the Pi, which publishes it with
   "Needs review" on.

Usage: python3 tools/pc_sync.py [--dry-run]   (--dry-run: draft, but don't publish)
"""
import argparse
import fcntl
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.tiktok_sync import (  # noqa: E402
    DECIDE_SCHEMA, RECIPE_SCHEMA, STYLE_GUIDE,
    decide_prompt, download_video, draft_prompt, extract_frames, save_hero,
)

PI = "dkalenscher@192.168.1.152"
REMOTE = "dkcraftbbq-v2"           # relative to the Pi user's home
MAX_PER_RUN = 5                    # keeps one run's Claude usage bounded
MAX_FAILURES = 3                   # stop retrying a video after this many failed runs
MODEL = "opus"
DATA = ROOT / "data"
FAILURES_FILE = DATA / "pc_sync_failures.json"

log = logging.getLogger("pc_sync")


def ssh(command: str, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", PI, f"cd {REMOTE} && {command}"],
        capture_output=True, text=True, timeout=timeout,
    )


def scp(local: Path, remote: str):
    subprocess.run(["scp", "-q", "-o", "BatchMode=yes", str(local), f"{PI}:{REMOTE}/{remote}"],
                   check=True, timeout=120)


def claude_bin() -> str:
    return shutil.which("claude") or str(Path.home() / ".local/bin/claude")


def run_claude(prompt: str, schema: dict, cwd: Path, read_files: bool) -> dict:
    cmd = [
        claude_bin(), "-p", prompt,
        "--model", MODEL,
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--append-system-prompt", STYLE_GUIDE,
        "--no-session-persistence",
        "--allowedTools", "Read" if read_files else "",
    ]
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=1200)
    try:
        out = json.loads(res.stdout)
    except ValueError:
        raise RuntimeError(f"claude exited {res.returncode}: {(res.stderr or res.stdout)[-400:]}")
    if out.get("is_error") or not out.get("structured_output"):
        raise RuntimeError(f"claude returned no result: {str(out.get('result'))[:400]}")
    return out["structured_output"]


def handle(item: dict, ctx: dict, dry: bool) -> str:
    post, category = item["post"], item["category"]
    vid = post["id"]
    with tempfile.TemporaryDirectory(prefix="dkbbq-") as tmp:
        work = Path(tmp)
        decision = run_claude(decide_prompt(post, category, ctx["catalog"]), DECIDE_SCHEMA, work, False)
        log.info("video %s: %s %s (%s)", vid, decision["action"], decision.get("slug", ""), decision["reason"])
        known = {line.split(":")[0].lstrip("- ").strip() for line in ctx["catalog"].splitlines()}
        if decision["action"] == "attach" and decision.get("slug") not in known:
            log.warning("unknown recipe %r; drafting a new one instead", decision.get("slug"))
            decision["action"] = "new"

        result = {"post": post, "category": category, "decision": decision, "recipe": None, "image": None}
        if decision["action"] == "new":
            video = download_video(vid, work)
            if not video:
                raise RuntimeError("couldn't download the video")
            frames = extract_frames(video, work)
            names = [f.name for f in frames]
            recipe = run_claude(draft_prompt(post, category, ctx["examples"], names), RECIPE_SCHEMA, work, True)
            result["recipe"] = recipe
            log.info("video %s: drafted %r", vid, recipe["title"])
            hero = work / "hero.jpg"
            if save_hero(video, recipe.get("hero_frame", 0), hero, work) and not dry:
                ssh("mkdir -p data/incoming")
                scp(hero, f"data/incoming/{vid}.jpg")
                result["image"] = f"data/incoming/{vid}.jpg"

        if dry:
            out = DATA / f"pc_sync_dry_{vid}.json"
            out.write_text(json.dumps(result, indent=1, ensure_ascii=False))
            return f"dry run, saved {out.name}"

        payload = work / f"{vid}.json"
        payload.write_text(json.dumps(result, ensure_ascii=False))
        ssh("mkdir -p data/incoming")
        scp(payload, f"data/incoming/{vid}.json")
        res = ssh(f"python3 -m app.tiktok_sync --apply data/incoming/{vid}.json; "
                  f"rc=$?; rm -f data/incoming/{vid}.json data/incoming/{vid}.jpg; exit $rc")
        if res.returncode != 0:
            raise RuntimeError(f"apply failed on the Pi: {res.stderr.strip()[-400:]}")
        return res.stderr.strip().splitlines()[-1] if res.stderr.strip() else "applied"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="draft with Claude but don't publish anything")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    DATA.mkdir(exist_ok=True)

    lock = open(DATA / "pc_sync.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.info("another run is in progress; exiting")
        return

    failures = json.loads(FAILURES_FILE.read_text()) if FAILURES_FILE.exists() else {}
    tried: set[str] = set()
    for _ in range(MAX_PER_RUN):
        res = ssh("python3 -m app.tiktok_sync --export-pending")
        if res.returncode == 75:
            log.info("the Pi is already syncing; will try next run")
            return
        if res.returncode != 0:
            log.error("Pi sync failed: %s", res.stderr.strip()[-400:])
            sys.exit(1)
        data = json.loads(res.stdout)
        todo = [p for p in data["pending"]
                if p["post"]["id"] not in tried and failures.get(p["post"]["id"], 0) < MAX_FAILURES]
        if not todo:
            log.info("nothing new to write" if not tried else "done")
            break
        item = todo[0]
        vid = item["post"]["id"]
        tried.add(vid)
        try:
            log.info("video %s: %s", vid, handle(item, data, args.dry_run))
            failures.pop(vid, None)
        except Exception as e:
            failures[vid] = failures.get(vid, 0) + 1
            log.exception("video %s failed (%d/%d)", vid, failures[vid], MAX_FAILURES)
        FAILURES_FILE.write_text(json.dumps(failures))
        if args.dry_run:
            # Nothing was published, so the same video would come back; move on through the list
            for p in todo[1:]:
                if len(tried) >= MAX_PER_RUN:
                    break
                tried.add(p["post"]["id"])
                try:
                    log.info("video %s: %s", p["post"]["id"], handle(p, data, True))
                except Exception:
                    log.exception("video %s failed", p["post"]["id"])
            break


if __name__ == "__main__":
    main()
