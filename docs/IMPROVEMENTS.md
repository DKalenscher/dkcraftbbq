# DKCraftBBQ — Future Improvements

Running list of things to build or fix on the site. Add new ideas at the bottom of
the right section; move finished items to **Done** with the date.

## Up next

### Ads (revisit at ~1,000 visits/month)
Not worth it at current traffic. At ~1,000 monthly visits from US/UK/CA/AU/NZ,
apply to Journey by Mediavine (you control placement, keep it to 1–2 ads per
recipe); Raptive at 25,000 pageviews/month.

### Curate the generated recipes
Only recipes in a TikTok playlist are on the site, so only those need curating
(24 as of 2026-10-21, 19 still marked Needs review). Review each in Admin →
Recipes (the "What was filled in" box lists exactly what was added) and untick
**Needs review** when approved.
- Highest priority (mostly written from scratch): Pastrami Rub, Galbi, Buffalo
  Chicken Lettuce Wraps, and the pastrami brine/cure amounts (Smoked Brisket
  Pastrami, Kosher Pastrami).
- New auto-drafted recipe: Yakitori wings and veggie skewers. Fill in the actual
  seasoning and marinade, and shorten the title.
- Rewrite the About page in your own words.
- Idea: a "Curated by me" checkbox and a source label (from caption / partly
  filled / written) on the admin Recipes tab.
- Idea: a way to un-skip a video Claude judged not to be a recipe (today it has
  to be removed from the `tiktok_skipped_videos` site setting by hand).

### Time on site (analytics)
Today analytics only logs page loads, so there's no way to tell how long anyone
stays (about 80% of visits are a single page). Plan:
- In `static/assets/js/site.js`, count seconds while the page is visible (pause on
  tab/app switch) and send them with `navigator.sendBeacon` on `pagehide`.
- Store on the page view (new `page_views.engaged_seconds`, plus optional
  `max_scroll_pct`) via `_ADDED_COLUMNS`. Same anonymity and home-IP exclusion as now.
- Admin → Analytics: average time per visit, average time per recipe, and
  optionally how many readers reach the recipe card / product links.
- Only counts visits after it ships; no backfill possible.

## Housekeeping
- **TikTok sync, Pi side** (`app/tiktok_sync.py`): cron every 3 hours
  (`15 */3 * * *`) and the "Sync from TikTok" button in Admin → Recipes. Reads the
  playlists, shows/hides recipes, refreshes videos, lists videos needing a recipe.
  Log: `~/dkcraftbbq-v2/data/tiktok_sync.log` on the Pi. yt-dlp 2026.08.19 and
  ffmpeg 7.0.2 static live in `~/.local/bin` on the Pi; update yt-dlp if TikTok
  downloads start failing.
- **TikTok sync, PC side** (`tools/pc_sync.py`): started by hand with
  "DKCraftBBQ Recipe Sync.bat" on the Windows desktop (runs
  `~/dkcraftbbq/tools/pc_sync_loop.sh` in WSL). Checks right away and every 3 hours while the
  window is open; Enter checks now; closing the window stops it, and it has to
  be started again after each login. Only starts Claude Code when there's a new
  playlist video, writes the recipe with `claude -p` on your subscription, and
  sends it to the Pi. Log: `~/dkcraftbbq/data/pc_sync.log` on the PC. At most
  5 videos per check; a video that fails 3 checks in a row is left alone
  (`data/pc_sync_failures.json`). Videos Claude judges not to be recipes are
  remembered on the Pi in the `tiktok_skipped_videos` site setting.
- **Old site fallback:** v1 still runs on Pi port 8001 (`~/switch-site.sh v1`
  to roll back). Retire it once v2 has been stable for a while. The old code is
  tagged `v1-final` in git; the Pi's `~/dkcraftbbq` checkout is pinned to it on
  branch `v1-fallback` so pushes never change it.

## Done
- 2026-10-21 — Analytics insights: Top Recipes (views per visit, prints), Top
  Searches (with a "0 results" flag for recipes people want but the site lacks)
  and Top Products (shop clicks), on the dashboard (30 days) and Analytics page
  (date range). Page views now store `path`, `search`, `search_results`.
- 2026-10-21 — SEO: Google Search Console (Domain property, sitemap submitted) and
  Bing Webmaster Tools (meta tag `msvalidate.01` in base.html) set up. Recipe
  structured data has ISO times; sitemap has lastmod; homepage has WebSite and
  Organization schema.
- 2026-10-21 — Print buttons open `/recipe/<slug>/print` (noindex); in-app
  browsers get an "Open in browser" note since they ignore window.print().
- 2026-10-21 — Shop rebuilt from the old Linktree and the Amazon Storefront lists:
  Storefront banner, Meat (Snake River Farms, Porter Road via Impact), Favorite
  Rubs & Sauces, Grilling Gear Essentials, Pastrami Essentials, Kitchen Tools
  (42 items). New Amazon items use `amazon.com/dp/ASIN?tag=dkcraftbbq-20`; the
  Linktree-wrapped `r.amzlink.to` links were replaced with your own tag. Edit in
  Admin → Shop Links. Pi DB backup: `data/dkcraftbbq.db.bak-before-shop-*`.
- 2026-10-21 — The Pi deploys `master` (GitHub webhook pulls on push to `master`).
  On the Pi the live code is still in `~/dkcraftbbq-v2` (service
  `dkcraftbbq-v2.service`, port 8000). The old `v2` branch is no longer used.
- 2026-10-21 — Decided TikTok is the single source for videos and recipes; Instagram
  isn't pulled in and its expired token (2026-09-21) won't be renewed.
- 2026-10-21 — v2 recipe site launched; merged into `master`.
- 2026-10-21 — Analytics: visits counted per session, sources grouped by site,
  real visitor IP behind Cloudflare, home network excluded, data reset.
- 2026-10-21 — Traffic sources recognise TikTok, Instagram, Facebook, Threads,
  Snapchat and Pinterest in-app browsers automatically, so bio links stay clean
  (no UTM tags needed).
- 2026-10-21 — Newsletter dropped: signup, endpoint, admin list and subscribers table removed.
- 2026-10-21 — Recipes follow the TikTok playlists (Beef, Pork, Poultry, And
  More!). New playlist videos are pulled in automatically and new recipes are
  drafted by Claude Code on the PC and published with "Needs review" on.
