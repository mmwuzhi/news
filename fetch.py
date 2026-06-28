#!/usr/bin/env python3
"""
Daily news fetcher and AI summarizer.
Fetches RSS feeds, summarizes with Gemini, generates index.html.
"""

import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from html import escape
from pathlib import Path

import feedparser
from google import genai
from google.genai import errors as genai_errors

# ── Configuration ─────────────────────────────────────────────────────────────

FEEDS = [
    {"url": "https://sspai.com/feed",                        "source": "少数派"},
    {"url": "https://www.v2ex.com/index.xml",                "source": "V2EX"},
    {"url": "https://news.ycombinator.com/rss",              "source": "Hacker News"},
    {"url": "https://openai.com/news/rss.xml",               "source": "OpenAI Blog"},
    {"url": "https://artificialintelligence-news.com/feed/", "source": "AI News"},
    {"url": "http://feeds.bbci.co.uk/news/world/rss.xml",    "source": "BBC"},
    {"url": "https://www.theverge.com/rss/index.xml",        "source": "The Verge"},
    {"url": "https://www.technologyreview.com/feed/",        "source": "MIT Tech Review"},
    {"url": "https://36kr.com/feed",                         "source": "36氪"},
    {"url": "https://feeds.marketwatch.com/marketwatch/topstories/", "source": "MarketWatch"},
    {"url": "https://seekingalpha.com/feed.xml",             "source": "Seeking Alpha"},
    {"url": "https://github.blog/feed/",                     "source": "GitHub Blog"},
    {"url": "https://www.newscientist.com/feed/home/",       "source": "New Scientist"},
]

MAX_PER_FEED   = 5
MAX_TOTAL      = 40
MAX_PER_CAT    = 8
# Model IDs are env-overridable so they can be bumped without a code change.
# GEMINI_FALLBACK_MODELS is a comma-separated list, tried in order when the primary is unavailable.
GEMINI_MODEL   = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
GEMINI_FALLBACK_MODELS = tuple(
    m.strip() for m in os.environ.get("GEMINI_FALLBACK_MODELS", "gemini-3.5-flash").split(",") if m.strip()
)
GEMINI_RETRIES = int(os.environ.get("GEMINI_RETRIES", "4"))  # attempts per model before failing over
CATEGORY_ORDER = ["AI", "TECH", "FINA", "SCI", "WORLD"]
SITE_URL       = "https://news.wuwuwu.cc"
FEED_WINDOW    = 7    # days of history kept in the rolling feed
FEED_MAX       = 200  # hard cap on feed item count

SUPA_URL = os.environ.get("SUPABASE_URL", "")
SUPA_KEY = os.environ.get("SUPABASE_KEY", "")

# ── HTML Template ─────────────────────────────────────────────────────────────
# Placeholders filled by generate_html():
#   __CAT_NAV__       desktop left-sidebar category buttons
#   __CAT_TABS__      mobile horizontal category tabs
#   __PROMPT_DATE__   YYYY-MM-DD
#   __BRIEF_DATE__    "Mon May 11 2026"
#   __BRIEF_TOTAL__   story count (integer string)
#   __BRIEF_MODEL__   Gemini model name
#   __ITEMS__         flat list of .item divs
#   __ARCHIVE__       archive date links (used twice: desktop + mobile sheet)
#   __STATUS_TOTAL__  same as BRIEF_TOTAL
#   __STATUS_TIME__   "HH:MM UTC"
#   __SUPABASE_URL__  Supabase project URL (empty string if not configured)
#   __SUPABASE_KEY__  Supabase anon key   (empty string if not configured)
#   __TODAY__         YYYY-MM-DD (used by app.js to key today's votes)

# CSS and JS now live in assets/style.css and assets/app.js (served as-is).
HTML_TEMPLATE = (Path(__file__).resolve().parent / "template.html").read_text(encoding="utf-8")

# ── Helpers ───────────────────────────────────────────────────────────────────

def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()[:600]


def time_ago(dt) -> str:
    if not dt:
        return "recent"
    now = datetime.now(timezone.utc)
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = now - dt
    hours = int(diff.total_seconds() / 3600)
    if hours < 1:
        return "just now"
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def fetch_feed(url: str, timeout: int = 15):
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (compatible; daily-brief/1.0)"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as f:
            return feedparser.parse(f.read())
    except Exception as e:
        print(f"  ✗ {url}: {e}", file=sys.stderr)
        return None

# ── Supabase ─────────────────────────────────────────────────────────────────

def read_votes() -> list[dict]:
    if not SUPA_URL or not SUPA_KEY:
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")
    url = f"{SUPA_URL}/rest/v1/votes?select=tags,vote&date=gte.{cutoff}"
    req = urllib.request.Request(
        url, headers={"apikey": SUPA_KEY, "Authorization": f"Bearer {SUPA_KEY}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as f:
            return json.loads(f.read())
    except Exception as e:
        print(f"  ✗ Supabase read_votes: {e}", file=sys.stderr)
        return []


def compute_tag_scores(votes: list[dict]) -> dict[str, int]:
    scores: dict[str, int] = {}
    for row in votes:
        v = row.get("vote", 0)
        for tag in (row.get("tags") or []):
            scores[tag] = scores.get(tag, 0) + v
    return scores


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_all() -> list[dict]:
    items = []
    for feed_cfg in FEEDS:
        print(f"  fetching {feed_cfg['source']}...", file=sys.stderr)
        feed = fetch_feed(feed_cfg["url"])
        if not feed:
            continue
        for entry in feed.entries[:MAX_PER_FEED]:
            published = None
            if getattr(entry, "published_parsed", None):
                try:
                    published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                except Exception:
                    pass
            raw_summary = (
                getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
            )
            items.append({
                "title":     entry.get("title", "Untitled").strip(),
                "link":      entry.get("link", "#"),
                "source":    feed_cfg["source"],
                "content":   strip_html(raw_summary),
                "published": published,
                "time_ago":  time_ago(published),
            })

    items.sort(
        key=lambda x: x["published"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return items[:MAX_TOTAL]

# ── Summarize ─────────────────────────────────────────────────────────────────

def summarize(items: list[dict]) -> tuple[list[dict], str]:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    articles = "\n\n".join(
        f"Article {i + 1}:\nTitle: {item['title']}\nSource: {item['source']}\nContent: {item['content']}"
        for i, item in enumerate(items)
    )

    prompt = f"""Summarize the following {len(items)} news articles.
For each article, return a JSON object with:
- titleCN: Chinese (Simplified) translation of the headline
- en: a clear 2-sentence English summary
- zh: a concise 2-sentence Chinese (Simplified) summary
- category: one of AI, TECH, FINA, SCI, WORLD
- tags: array of 3-5 lowercase keyword tags (e.g. ["llm","openai","api"])

Return ONLY a valid JSON array — no markdown, no code fences.
Example: [{{"titleCN":"...","en":"...","zh":"...","category":"TECH","tags":["rust","programming"]}}]

{articles}"""

    print("  calling Gemini...", file=sys.stderr)
    models = (GEMINI_MODEL, *GEMINI_FALLBACK_MODELS)
    response = None
    used_model: str | None = None
    last_error: Exception | None = None
    for model in models:
        for attempt in range(1, GEMINI_RETRIES + 1):
            try:
                response = client.models.generate_content(model=model, contents=prompt)
                break
            except genai_errors.ServerError as e:
                # 5xx (e.g. 503 "high demand") is transient: back off and retry, then
                # fail over to the next model when this one stays unavailable.
                last_error = e
                if attempt == GEMINI_RETRIES:
                    print(f"  ✗ {model} unavailable after {GEMINI_RETRIES} attempts ({e})", file=sys.stderr)
                    break
                wait = 30 * attempt
                print(f"  ✗ {model} attempt {attempt} failed ({e}), retrying in {wait}s...", file=sys.stderr)
                time.sleep(wait)
            except Exception as e:
                # Non-5xx (bad request, auth, quota): retrying the same model won't help,
                # so move straight to the next model.
                last_error = e
                print(f"  ✗ {model} error ({e}); failing over...", file=sys.stderr)
                break
        if response is not None:
            used_model = model
            if model != GEMINI_MODEL:
                print(f"  ✓ recovered with fallback model {model}", file=sys.stderr)
            break
    if response is None:
        raise last_error

    text = response.text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        summaries = json.loads(text)
        print(f"  parsed {len(summaries)} summaries", file=sys.stderr)
    except json.JSONDecodeError as e:
        print(f"  ✗ JSON parse error: {e}\n  raw: {text[:500]}", file=sys.stderr)
        summaries = []

    for i, item in enumerate(items):
        s = summaries[i] if i < len(summaries) else {}
        # `or` (not the get-default) because Gemini may return explicit nulls.
        item["titleCN"]  = s.get("titleCN") or ""
        item["en"]       = s.get("en")       or "Summary unavailable."
        item["zh"]       = s.get("zh")       or "摘要暂不可用。"
        item["category"] = s.get("category") or "TECH"
        item["tags"]     = [t for t in (s.get("tags") or []) if isinstance(t, str)]

    return items, used_model

# ── HTML generation ───────────────────────────────────────────────────────────

def attr_json(obj) -> str:
    """JSON-encode obj for use in a double-quoted HTML attribute."""
    return escape(json.dumps(obj, ensure_ascii=False))


def build_item(item: dict, idx: int, first: bool) -> str:
    cat      = escape(item["category"])
    tags     = item.get("tags", [])
    tags_str = escape(" · ".join(tags)) if tags else ""
    expanded = " expanded" if first else ""
    title_cn = (item.get("titleCN") or "").strip()
    if title_cn == item["title"].strip():
        title_cn = ""
    return (
        f'\n<div class="item{expanded}" data-id="{idx}" data-cat="{cat}" data-tags="{attr_json(tags)}">'
        f'\n  <div class="item-row">'
        f'\n    <span class="item-idx">{idx:02d}</span>'
        f'\n    <div class="item-body">'
        f'\n      <div class="item-title-en"><a href="{escape(item["link"])}" target="_blank" rel="noopener" onclick="event.stopPropagation()">{escape(item["title"])}</a></div>'
        + (f'\n      <div class="item-title-zh">{escape(title_cn)}</div>' if title_cn else "")
        + f'\n      <div class="item-meta-row">'
        f'\n        <span class="cat-badge">{cat}</span>'
        f'\n        <span class="source-time">{escape(item["source"])} &middot; {escape(item["time_ago"])}</span>'
        f'\n        <div class="votes">'
        f'\n          <button class="vote-btn" data-id="{idx}" data-val="1">&#8593;</button>'
        f'\n          <button class="vote-btn" data-id="{idx}" data-val="-1">&#8595;</button>'
        f'\n        </div>'
        f'\n      </div>'
        + (f'\n      <div class="item-tags">{tags_str}</div>' if tags_str else "")
        + f'\n    </div>'
        f'\n    <span class="expand-arrow">&#9658;</span>'
        f'\n  </div>'
        f'\n  <div class="item-summary">'
        f'\n    <div class="item-summary-inner">'
        f'\n      <p class="summary-en">{escape(item["en"])}</p>'
        f'\n      <p class="summary-zh">{escape(item["zh"])}</p>'
        f'\n    </div>'
        f'\n  </div>'
        f'\n</div>'
    )


def build_cat_nav(groups: dict, total: int) -> str:
    html = f'<button class="cat-btn active" data-cat="ALL"><span>ALL</span><span class="cat-count">{total}</span></button>\n'
    for cat in CATEGORY_ORDER:
        count = len(groups.get(cat, []))
        if count:
            html += f'<button class="cat-btn" data-cat="{escape(cat)}"><span>{escape(cat)}</span><span class="cat-count">{count}</span></button>\n'
    return html


def build_cat_tabs(groups: dict) -> str:
    html = '<button class="cat-tab active" data-cat="ALL">ALL</button>\n'
    for cat in CATEGORY_ORDER:
        if groups.get(cat):
            html += f'<button class="cat-tab" data-cat="{escape(cat)}">{escape(cat)}</button>\n'
    return html


def build_archive_list(dates: list) -> str:
    return "".join(
        f'<a class="archive-date" href="/archive/{d}.html">{d}</a>\n'
        for d in dates
    )


# ── Feeds (RSS 2.0 + JSON Feed 1.1) ────────────────────────────────────────────

def to_feed_item(item: dict, now: datetime) -> dict | None:
    link = item.get("link", "")
    if not link.startswith("http"):
        return None
    published = item.get("published") or now
    if not published.tzinfo:
        published = published.replace(tzinfo=timezone.utc)
    title_en = item.get("title", "")
    parts = [f"<p><strong>{escape(title_en)}</strong></p>"]
    if item.get("en"):
        parts.append(f"<p>{escape(item['en'])}</p>")
    if item.get("zh"):
        parts.append(f"<p>{escape(item['zh'])}</p>")
    if item.get("tags"):
        parts.append(f"<p>Tags: {escape(' · '.join(item['tags']))}</p>")
    return {
        "id":             link,
        "url":            link,
        "title":          item.get("titleCN") or title_en or "Untitled",
        "content_html":   "".join(parts),
        "date_published": published.isoformat(),
        "tags":           list(item.get("tags", [])),
        "_category":      item.get("category", ""),
    }


def load_feed_items() -> list[dict]:
    if not os.path.exists("feed.json"):
        return []
    try:
        with open("feed.json", "r", encoding="utf-8") as f:
            return json.load(f).get("items", [])
    except Exception as e:
        print(f"  ✗ load_feed_items: {e}", file=sys.stderr)
        return []


def _feed_date(value: str, fallback: datetime) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return fallback


def merge_feed_items(
    existing: list[dict], today: list[dict], now: datetime
) -> list[dict]:
    seen = {it["id"] for it in existing}
    merged = list(existing)
    for item in today:
        feed_item = to_feed_item(item, now)
        if feed_item and feed_item["id"] not in seen:
            seen.add(feed_item["id"])
            merged.append(feed_item)

    cutoff = now - timedelta(days=FEED_WINDOW)
    merged = [it for it in merged if _feed_date(it.get("date_published", ""), now) >= cutoff]
    merged.sort(key=lambda it: _feed_date(it.get("date_published", ""), now), reverse=True)
    return merged[:FEED_MAX]


def build_json_feed(items: list[dict], now: datetime) -> str:
    feed = {
        "version":       "https://jsonfeed.org/version/1.1",
        "title":         "Daily Brief",
        "home_page_url": SITE_URL,
        "feed_url":      f"{SITE_URL}/feed.json",
        "language":      "zh-cn",
        "items":         items,
    }
    return json.dumps(feed, ensure_ascii=False, indent=2)


def build_rss(items: list[dict], now: datetime) -> str:
    def cdata(text: str) -> str:
        return f"<![CDATA[{text.replace(']]>', ']]]]><![CDATA[>')}]]>"

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        "<title>Daily Brief</title>",
        f"<link>{SITE_URL}</link>",
        f'<atom:link href="{SITE_URL}/feed.xml" rel="self" type="application/rss+xml"/>',
        "<description>每日中英双语科技与世界新闻摘要 · summarized by Gemini</description>",
        "<language>zh-cn</language>",
        f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>",
    ]
    for it in items:
        pub = _feed_date(it.get("date_published", ""), now)
        lines.append("<item>")
        lines.append(f"<title>{escape(it.get('title', ''))}</title>")
        lines.append(f"<link>{escape(it['url'])}</link>")
        lines.append(f'<guid isPermaLink="true">{escape(it["id"])}</guid>')
        lines.append(f"<pubDate>{format_datetime(pub)}</pubDate>")
        if it.get("_category"):
            lines.append(f"<category>{escape(it['_category'])}</category>")
        lines.append(f"<description>{cdata(it.get('content_html', ''))}</description>")
        lines.append("</item>")
    lines.append("</channel>")
    lines.append("</rss>")
    return "\n".join(lines)


def get_archive_dates() -> list[str]:
    if not os.path.isdir("archive"):
        return []
    return sorted(
        [f[:-5] for f in os.listdir("archive") if f.endswith(".html") and len(f) == 15],
        reverse=True,
    )[:14]


def apply_cat_limit(
    items: list[dict], tag_scores: dict[str, int] | None = None
) -> tuple[list[dict], dict[str, list]]:
    groups: dict[str, list] = {}
    for item in items:
        groups.setdefault(item["category"], []).append(item)
    if tag_scores:
        for cat in groups:
            groups[cat].sort(
                key=lambda x: sum(tag_scores.get(t, 0) for t in x.get("tags", [])),
                reverse=True,
            )
    capped: list[dict] = []
    capped_groups: dict[str, list] = {}
    for cat in CATEGORY_ORDER:
        subset = groups.get(cat, [])[:MAX_PER_CAT]
        capped.extend(subset)
        if subset:
            capped_groups[cat] = subset
    return capped, capped_groups


def generate_html(items: list[dict], tag_scores: dict[str, int] | None = None,
                  model: str = GEMINI_MODEL) -> str:
    now = datetime.now(timezone.utc)
    items, groups = apply_cat_limit(items, tag_scores)
    total = len(items)

    items_html = "".join(build_item(item, i + 1, i == 0) for i, item in enumerate(items))
    archive_html = build_archive_list(get_archive_dates())

    html = HTML_TEMPLATE
    html = html.replace("__CAT_NAV__",       build_cat_nav(groups, total))
    html = html.replace("__CAT_TABS__",      build_cat_tabs(groups))
    html = html.replace("__PROMPT_DATE__",   now.strftime("%Y-%m-%d"))
    html = html.replace("__BRIEF_DATE__",    now.strftime("%a %b %d %Y"))
    html = html.replace("__BRIEF_TOTAL__",   str(total))
    html = html.replace("__BRIEF_MODEL__",   model)
    html = html.replace("__ITEMS__",         items_html)
    html = html.replace("__ARCHIVE__",       archive_html)  # replaces both occurrences
    html = html.replace("__STATUS_TOTAL__",  str(total))
    html = html.replace("__STATUS_TIME__",   now.strftime("%H:%M UTC"))
    html = html.replace("__SUPABASE_URL__",  SUPA_URL)
    html = html.replace("__SUPABASE_KEY__",  SUPA_KEY)
    html = html.replace("__TODAY__",         now.strftime("%Y-%m-%d"))
    return html

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("▶ reading votes from Supabase...", file=sys.stderr)
    vote_rows  = read_votes()
    tag_scores = compute_tag_scores(vote_rows)
    if tag_scores:
        top = sorted(tag_scores.items(), key=lambda x: abs(x[1]), reverse=True)[:5]
        print(f"  tag scores: {dict(top)}", file=sys.stderr)

    print("▶ fetching feeds...", file=sys.stderr)
    items = fetch_all()
    print(f"  {len(items)} items collected", file=sys.stderr)

    if not items:
        print("✗ no items fetched, aborting", file=sys.stderr)
        sys.exit(1)

    items, used_model = summarize(items)
    html  = generate_html(items, tag_scores or None, used_model)

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✓ wrote index.html ({len(html)} bytes)", file=sys.stderr)

    now = datetime.now(timezone.utc)
    os.makedirs("archive", exist_ok=True)
    date_str = now.strftime("%Y-%m-%d")
    archive_path = f"archive/{date_str}.html"
    with open(archive_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✓ wrote {archive_path}", file=sys.stderr)

    feed_source, _ = apply_cat_limit(items, tag_scores or None)
    feed_items = merge_feed_items(load_feed_items(), feed_source, now)
    with open("feed.json", "w", encoding="utf-8") as f:
        f.write(build_json_feed(feed_items, now))
    with open("feed.xml", "w", encoding="utf-8") as f:
        f.write(build_rss(feed_items, now))
    print(f"✓ wrote feed.xml and feed.json ({len(feed_items)} items)", file=sys.stderr)


if __name__ == "__main__":
    main()
