# DKCraftBBQ — Future Improvements

Running list of things to build or fix on the site. Add new ideas at the bottom of
the right section; move finished items to **Done** with the date.

## Up next

### Recipe newsletter (removed 2026-10-21, to bring back properly)
The signup band was taken off the site because nothing ever sent an email.
- Pick a free email service (Kit, MailerLite or Buttondown) and get an API key.
- Add an RSS feed of recipes (`/feed.xml`) so the service can email
  "New recipe: …" automatically whenever one is published.
- Point the signup form at the service; it handles unsubscribe links and
  CAN-SPAM requirements (don't send from Gmail or the Pi).
- Restore the signup band: the old markup, CSS and JS are in git history
  before the commit "Remove the newsletter signup". The `subscribers` table
  still exists in the database (empty) and can be dropped or migrated.

### Curate the generated recipes
33 recipes were written from TikTok captions; many have filled-in amounts and
temps. Review each in Admin → Recipes (the "What was filled in" box lists exactly
what was added) and untick **Needs review** when approved.
- Highest priority (mostly written from scratch): Pastrami Rub, Galbi, Skirt
  Steak Fajitas, Smoked Prime Rib Roast, Pork Belly Burnt Ends, Brisket Ramen,
  Homemade Beef Tallow, Buffalo Chicken Lettuce Wraps, Picanha Ciabatta.
- Then the pastrami brine/cure amounts (Smoked Brisket Pastrami, Kosher Pastrami).
- Idea: add a "source" label (from caption / partly filled / written) and a
  "Curated by me" checkbox to the admin Recipes tab.
- Rewrite the About page in your own words.

### Adding new recipes from new videos
New TikToks don't show up on the site automatically. Videos and recipes are
seeded from `content/videos.json` and `content/recipes.json` at startup.
- Idea: a scheduled job that pulls new TikTok posts (yt-dlp), adds them to the
  Videos page, and drafts a recipe (hidden, needs review) from the caption.

## Housekeeping
- **Instagram token expired (2026-09-21).** Generate a new long-lived token; it
  also fixes the Discord bot's Instagram sync. Then Instagram posts could be
  pulled in alongside TikTok.
- **Deploy branch:** the Pi's live checkout (`~/dkcraftbbq-v2`) follows `v2`.
  `master` now equals `v2`; consider switching the Pi to follow `master` and
  retiring the `v2` branch.
- **Old site fallback:** v1 still runs on Pi port 8001 (`~/switch-site.sh v1`
  to roll back). Retire it once v2 has been stable for a while. The old code is
  tagged `v1-final` in git.
- **Traffic source tracking:** add `?utm_source=tiktok` to the TikTok bio link
  (and `ig` for Instagram) so the dashboard can tell where visits come from.

## Done
- 2026-10-21 — v2 recipe site launched; merged into `master`.
- 2026-10-21 — Analytics: visits counted per session, sources grouped by site,
  real visitor IP behind Cloudflare, home network excluded, data reset.
- 2026-10-21 — Newsletter signup removed until an email service is connected.
