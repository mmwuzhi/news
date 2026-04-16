#!/usr/bin/env python3
"""
Daily news fetcher and AI summarizer.
Fetches RSS feeds, summarizes with Gemini, generates index.html.
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from html import escape

import feedparser
from google import genai

# ── Configuration ─────────────────────────────────────────────────────────────

FEEDS = [
    {"url": "https://sspai.com/feed",                        "source": "少数派"},
    {"url": "https://www.v2ex.com/index.xml",                "source": "V2EX"},
    {"url": "https://news.ycombinator.com/rss",              "source": "Hacker News"},
    {"url": "https://openai.com/news/rss.xml",               "source": "OpenAI Blog"},
    {"url": "https://www.anthropic.com/rss.xml",             "source": "Anthropic Blog"},
    {"url": "https://artificialintelligence-news.com/feed/", "source": "AI News"},
    {"url": "http://feeds.bbci.co.uk/news/world/rss.xml",    "source": "BBC"},
    {"url": "https://www.theverge.com/rss/index.xml",        "source": "The Verge"},
    {"url": "https://www.technologyreview.com/feed/",        "source": "MIT Tech Review"},
    {"url": "https://36kr.com/feed",                         "source": "36氪"},
    {"url": "https://www.ruanyifeng.com/blog/atom.xml",      "source": "阮一峰"},
    {"url": "https://github.blog/feed/",                     "source": "GitHub Blog"},
]

MAX_PER_FEED = 3
MAX_TOTAL = 20
GEMINI_MODEL = "gemini-2.0-flash"
CATEGORY_ORDER = ["AI", "Technology", "World", "Science", "Finance"]

# ── HTML Template ─────────────────────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Daily Brief</title>
<meta name="theme-color" content="#0d0d0d">
<link rel="manifest" href="/manifest.json">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #0d0d0d;
    --surface: #141414;
    --border: #2a2a2a;
    --text: #d4d4d4;
    --text-dim: #666;
    --green: #4ec994;
    --yellow: #e5c07b;
    --blue: #61afef;
    --red: #e06c75;
    --purple: #c678dd;
    --cyan: #56b6c2;
  }

  body {
    font-family: 'Maple Mono', 'JetBrains Mono', 'Fira Code', 'Menlo', monospace;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    font-size: 14px;
    line-height: 1.6;
  }

  .window {
    max-width: 860px;
    margin: 30px auto;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
    box-shadow: 0 20px 60px rgba(0,0,0,0.6);
  }

  .titlebar {
    background: #1e1e1e;
    padding: 10px 16px;
    display: flex;
    align-items: center;
    gap: 12px;
    border-bottom: 1px solid var(--border);
    user-select: none;
  }

  .dots { display: flex; gap: 7px; }
  .dot { width: 12px; height: 12px; border-radius: 50%; }
  .dot-red    { background: #ff5f56; }
  .dot-yellow { background: #ffbd2e; }
  .dot-green  { background: #27c93f; }

  .tab-title {
    flex: 1;
    text-align: center;
    font-size: 0.75rem;
    color: var(--text-dim);
  }

  .content {
    padding: 20px 24px 40px;
  }

  .prompt-line {
    color: var(--text-dim);
    margin-bottom: 20px;
    font-size: 0.82rem;
  }
  .prompt-line .user { color: var(--green); }
  .prompt-line .host { color: var(--blue); }

  .header-block {
    border-left: 2px solid var(--green);
    padding-left: 16px;
    margin-bottom: 28px;
  }
  .header-block .title {
    color: var(--green);
    font-size: 1.1rem;
    font-weight: bold;
    letter-spacing: 0.05em;
  }
  .header-block .meta {
    color: var(--text-dim);
    font-size: 0.78rem;
    margin-top: 2px;
  }

  .section-header {
    margin: 28px 0 12px;
    display: flex;
    align-items: center;
    gap: 0;
  }
  .section-header .bracket { color: var(--text-dim); }
  .section-header .label   { color: var(--yellow); font-weight: bold; }
  .section-header .num     { color: var(--purple); margin-left: 6px; font-size: 0.78rem; }

  .item {
    padding: 14px 0;
    border-bottom: 1px solid #1e1e1e;
  }
  .item:last-child { border-bottom: none; }

  .item-header {
    display: flex;
    gap: 8px;
    align-items: baseline;
    margin-bottom: 6px;
    flex-wrap: wrap;
  }

  .item-idx { color: var(--text-dim); min-width: 24px; }

  .item-title {
    color: var(--blue);
    font-weight: bold;
    font-size: 0.95rem;
    text-decoration: none;
    line-height: 1.4;
  }
  .item-title:hover { color: var(--cyan); text-decoration: underline; }

  .item-source {
    color: var(--text-dim);
    font-size: 0.72rem;
    margin-left: auto;
  }

  .item-en {
    color: var(--text);
    font-size: 0.82rem;
    line-height: 1.65;
    margin-bottom: 4px;
    padding-left: 32px;
  }

  .item-zh {
    color: #888;
    font-size: 0.8rem;
    line-height: 1.65;
    padding-left: 32px;
  }
  .item-zh::before { content: '# '; color: var(--text-dim); }

  .tag-inline {
    font-size: 0.68rem;
    color: var(--text-dim);
    border: 1px solid #333;
    padding: 1px 6px;
    border-radius: 3px;
    margin-left: 4px;
    vertical-align: middle;
  }

  .footer-line {
    margin-top: 28px;
    color: var(--text-dim);
    font-size: 0.78rem;
    border-top: 1px solid var(--border);
    padding-top: 16px;
  }
  .footer-line .ok { color: var(--green); }

  .cursor {
    display: inline-block;
    width: 8px;
    height: 14px;
    background: var(--green);
    vertical-align: middle;
    animation: blink 1s step-end infinite;
    margin-left: 4px;
  }

  @keyframes blink {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0; }
  }

  @media (max-width: 640px) {
    .window { margin: 0; border-radius: 0; border-left: none; border-right: none; }
    .item-source { display: none; }
    .content { padding: 16px 16px 32px; }
  }
</style>
</head>
<body>

<div class="window">
  <div class="titlebar">
    <div class="dots">
      <div class="dot dot-red"></div>
      <div class="dot dot-yellow"></div>
      <div class="dot dot-green"></div>
    </div>
    <div class="tab-title">daily-brief — zsh</div>
  </div>

  <div class="content">

    <div class="prompt-line">
      <span class="user">you</span>@<span class="host">news</span> ~ $ brief --date __DATE_STR__ --lang bilingual
    </div>

    <div class="header-block">
      <div class="title">▶ DAILY BRIEF</div>
      <div class="meta">__DATE_FULL__ · __TOTAL__ stories · summarized by __MODEL__</div>
    </div>

__SECTIONS__

    <div class="footer-line">
      <span class="ok">✓</span>
      done · __TOTAL__ fetched · next update in ~24h · updated __UPDATE_TIME__
      <span class="cursor"></span>
    </div>

  </div>
</div>

<script>
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js');
  }
</script>
</body>
</html>"""

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
                getattr(entry, "summary", "")
                or getattr(entry, "description", "")
                or ""
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

def summarize(items: list[dict]) -> list[dict]:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    articles = "\n\n".join(
        f"Article {i + 1}:\nTitle: {item['title']}\nSource: {item['source']}\nContent: {item['content']}"
        for i, item in enumerate(items)
    )

    prompt = f"""Summarize the following {len(items)} news articles.
For each article, return:
- en: a clear 2-sentence English summary
- zh: a concise 2-sentence Chinese (Simplified) summary
- category: one of AI, Technology, World, Science, Finance

Return ONLY a valid JSON array — no markdown, no code fences.
Example: [{{"en":"...","zh":"...","category":"..."}}]

{articles}"""

    print("  calling Gemini...", file=sys.stderr)
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    text = response.text.strip()
    # Strip markdown code fences if Gemini wraps the response
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        summaries = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  ✗ JSON parse error: {e}\n  raw: {text[:200]}", file=sys.stderr)
        summaries = []

    for i, item in enumerate(items):
        s = summaries[i] if i < len(summaries) else {}
        item["en"]       = s.get("en", "Summary unavailable.")
        item["zh"]       = s.get("zh", "摘要暂不可用。")
        item["category"] = s.get("category", "Technology")

    return items

# ── Generate HTML ─────────────────────────────────────────────────────────────

def build_sections(items: list[dict]) -> str:
    groups: dict[str, list] = {}
    for item in items:
        groups.setdefault(item["category"], []).append(item)

    html = ""
    idx = 1
    for cat in CATEGORY_ORDER:
        if cat not in groups:
            continue
        cat_items = groups[cat]
        count = len(cat_items)
        html += (
            f'\n    <div class="section-header">'
            f'<span class="bracket">[</span>'
            f'<span class="label">{escape(cat.upper())}</span>'
            f'<span class="bracket">]</span>'
            f'<span class="num">{count} item{"s" if count != 1 else ""}</span>'
            f'</div>\n'
        )
        for item in cat_items:
            tag = escape(cat[:4].upper())
            html += (
                f'\n    <div class="item">'
                f'\n      <div class="item-header">'
                f'<span class="item-idx">{idx:02d}</span>'
                f'<a href="{escape(item["link"])}" class="item-title" target="_blank" rel="noopener">{escape(item["title"])}</a>'
                f'<span class="tag-inline">{tag}</span>'
                f'<span class="item-source">{escape(item["source"])} · {escape(item["time_ago"])}</span>'
                f'</div>'
                f'\n      <div class="item-en">{escape(item["en"])}</div>'
                f'\n      <div class="item-zh">{escape(item["zh"])}</div>'
                f'\n    </div>\n'
            )
            idx += 1
    return html


def generate_html(items: list[dict]) -> str:
    now = datetime.now(timezone.utc)
    html = HTML_TEMPLATE
    html = html.replace("__DATE_STR__",   now.strftime("%Y-%m-%d"))
    html = html.replace("__DATE_FULL__",  now.strftime("%a %b %d %Y"))
    html = html.replace("__TOTAL__",      str(len(items)))
    html = html.replace("__MODEL__",      GEMINI_MODEL)
    html = html.replace("__UPDATE_TIME__", now.strftime("%H:%M UTC"))
    html = html.replace("__SECTIONS__",   build_sections(items))
    return html

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("▶ fetching feeds...", file=sys.stderr)
    items = fetch_all()
    print(f"  {len(items)} items collected", file=sys.stderr)

    if not items:
        print("✗ no items fetched, aborting", file=sys.stderr)
        sys.exit(1)

    items = summarize(items)
    html  = generate_html(items)

    out = "index.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✓ wrote {out} ({len(html)} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()
