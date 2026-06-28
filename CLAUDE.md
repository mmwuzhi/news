# news — contributor & agent guide

A daily, static, bilingual (EN/ZH) news brief. `fetch.py` pulls RSS feeds,
summarizes them with Gemini, and renders a terminal-styled page plus RSS/JSON
feeds. GitHub Actions runs it daily and Cloudflare Pages serves the result.

This file is the working guide for anyone (human or agent) changing the repo.
For the product-level overview, feed list, and design tokens, see `README.md`.
`AGENTS.md` is a symlink to this file.

## Pipeline (what `fetch.py main()` does)

1. Read recent votes from Supabase → `compute_tag_scores` (skipped if Supabase
   env vars are unset).
2. `fetch_all` — up to 5 entries per feed, sorted by recency, capped at 40.
3. `summarize` — one Gemini call for all articles, returning `titleCN`, `en`,
   `zh`, `category`, `tags` per article. Retries with backoff on 5xx and fails
   over to `GEMINI_FALLBACK_MODELS`; the model actually used is reported back.
4. `generate_html` — `apply_cat_limit` groups by category (order:
   AI, TECH, FINA, SCI, WORLD), ranks within a category by tag score when
   votes exist, and caps each category at 8. Placeholders in `template.html`
   are then filled.
5. Write `index.html`, `archive/YYYY-MM-DD.html`, and merge today's items into
   the rolling `feed.json` + `feed.xml` (last 7 days, max 200 items).

## Source of truth — read before editing the UI

Styles, client behavior, and the HTML shell live in **standalone files**:

- Styles → `assets/style.css`
- Behavior → `assets/app.js`
- HTML structure → `template.html`

`fetch.py` reads `template.html` once at import and only substitutes `__…__`
placeholders. **Never re-embed CSS, JS, or large HTML blocks back into
`fetch.py`** — that was deliberately extracted out. Edit the asset files
directly.

`template.html` placeholders (all filled in `generate_html`):
`__CAT_NAV__`, `__CAT_TABS__`, `__PROMPT_DATE__`, `__BRIEF_DATE__`,
`__BRIEF_TOTAL__`, `__BRIEF_MODEL__`, `__ITEMS__`, `__ARCHIVE__` (used twice),
`__STATUS_TOTAL__`, `__STATUS_TIME__`, `__SUPABASE_URL__`, `__SUPABASE_KEY__`,
`__TODAY__`.

## Generated files — do not hand-edit

`index.html`, everything under `archive/`, `feed.xml`, and `feed.json` are
produced by the daily job and committed. Hand edits get overwritten on the next
run. To change how they look, edit the assets/template; to change their
content, edit `fetch.py`.

`reskin.py` is a one-time migration that rewrites pre-refactor archive
snapshots to reference the external assets. The daily workflow runs it so old
snapshots stay consistent; it is idempotent (skips already-migrated files).

## Conventions

- Code, comments, and docs are in English. Summaries are bilingual by design.
- No build step, no framework, no CDN — keep it that way. `app.js` is plain ES5
  IIFE vanilla JS; `style.css` is hand-written.
- Model IDs are env-overridable (`GEMINI_MODEL`, `GEMINI_FALLBACK_MODELS`,
  `GEMINI_RETRIES`) so bumping a model needs no code change.
- Votes degrade gracefully: with Supabase they sync across devices and feed the
  next day's ranking; without it they stay in `localStorage`.
- When fixing a pattern, check every occurrence in the file, not just the first.

## Running locally

```sh
pip install -r requirements.txt          # feedparser, google-genai
GEMINI_API_KEY=your_key python fetch.py  # writes index.html, archive/, feed.*
```

Supabase is optional locally; leaving `SUPABASE_URL`/`SUPABASE_KEY` unset just
disables vote sync and vote-weighted ranking.

## Deployment

`.github/workflows/daily.yml` runs at 07:00 UTC (15:00 Beijing) and on manual
dispatch: install deps → `python fetch.py` → `python reskin.py` → commit and
push `index.html archive/ assets/ feed.xml feed.json`. Cloudflare Pages deploys
every push to `main`. Custom domain: `news.wuwuwu.cc`.

Secrets used by the workflow: `GEMINI_API_KEY` (required), `SUPABASE_URL`,
`SUPABASE_KEY` (optional).
