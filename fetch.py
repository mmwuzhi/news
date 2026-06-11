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

import feedparser
from google import genai

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
GEMINI_MODEL   = "gemini-2.5-flash-lite"
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

CSS = """\
/* ── Reset ─────────────────────────────────────── */
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
button{cursor:pointer;font-family:inherit}
a{text-decoration:none}
::-webkit-scrollbar{width:3px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:#444;border-radius:2px}

/* ── Animations ────────────────────────────────── */
@keyframes voteUp   {0%,100%{transform:scale(1)}35%{transform:scale(1.45) translateY(-2px)}}
@keyframes voteDown {0%,100%{transform:scale(1)}35%{transform:scale(1.45) translateY( 2px)}}

/* ── Design tokens (dark default) ──────────────── */
:root{
  --page-bg: #0d0d0d;
  --bg:      #1a1a1a;
  --chrome:  #242424;
  --sidebar: #1e1e1e;
  --fg:      #d4d4d4;
  --fg-soft: #a8a8a8;
  --accent:  #5fb3a1;
  --dim:     #8a8a8a;
  --measure: 640px;
  --bd:      #2c2c2c;
  --tag-bd:  #333333;
  --red:     #e05a4e;
  --cat-active-bg: #252525;
}
[data-theme="light"]{
  --page-bg: #e8e8e4;
  --bg:      #f5f4ef;
  --chrome:  #e5e4df;
  --sidebar: #ededea;
  --fg:      #2a2a2a;
  --fg-soft: #555555;
  --dim:     #6f6f6f;
  --bd:      #dddddd;
  --tag-bd:  #cccccc;
  --cat-active-bg: #e4e4e0;
}

/* ── Page frame (desktop) ───────────────────────── */
body{
  background:var(--page-bg);
  font-family:'JetBrains Mono','Maple Mono','Fira Code','Menlo','PingFang SC','Hiragino Sans GB','Microsoft YaHei',monospace;
  display:flex;
  align-items:center;
  justify-content:center;
  min-height:100vh;
  padding:32px;
}

/* ── App window (desktop) ───────────────────────── */
#app{
  width:960px;
  height:680px;
  background:var(--bg);
  color:var(--fg);
  border-radius:10px;
  overflow:hidden;
  border:1px solid var(--bd);
  box-shadow:0 28px 72px rgba(0,0,0,.65);
  display:flex;
  flex-direction:column;
  flex-shrink:0;
  transition:width .3s ease,height .3s ease,border-radius .3s ease;
}
#app.maximized{width:calc(100vw - 64px);height:calc(100vh - 64px);border-radius:4px}
[data-theme="light"] #app{box-shadow:0 8px 36px rgba(0,0,0,.12)}

/* ── Window chrome ──────────────────────────────── */
.window-chrome{
  background:var(--chrome);
  padding:11px 16px;
  display:flex;
  align-items:center;
  justify-content:space-between;
  border-bottom:1px solid var(--bd);
  flex-shrink:0;
}
.chrome-dots{display:flex;gap:8px}
.chrome-dot{width:12px;height:12px;border-radius:50%}
.chrome-dot-r{background:#ff5f57}
.chrome-dot-y{background:#febc2e}
.chrome-dot-g{background:#28c840;cursor:pointer}
.chrome-title{font-size:12px;color:var(--dim)}

/* ── Three-column layout ────────────────────────── */
.columns{
  display:flex;
  flex:1;
  overflow:hidden;
  min-height:0;
}

/* LEFT sidebar */
.col-left{
  width:138px;
  flex-shrink:0;
  background:var(--sidebar);
  border-right:1px solid var(--bd);
  display:flex;
  flex-direction:column;
}

/* MAIN column */
.col-main{
  flex:1;
  overflow-y:auto;
  display:flex;
  flex-direction:column;
  min-width:0;
}

/* RIGHT sidebar */
.col-right{
  width:162px;
  flex-shrink:0;
  background:var(--sidebar);
  border-left:1px solid var(--bd);
  display:flex;
  flex-direction:column;
}
.col-right-top{
  flex:1;
  display:flex;
  flex-direction:column;
  min-height:0;
}

/* ── Sidebar shared ─────────────────────────────── */
.sidebar-hdr{
  padding:14px 14px 10px;
  font-size:9px;
  color:var(--dim);
  letter-spacing:2px;
  text-transform:uppercase;
  border-bottom:1px solid var(--bd);
  flex-shrink:0;
}

/* ── Category nav (desktop) ─────────────────────── */
.cat-nav{padding-top:6px}
.cat-btn{
  width:100%;
  background:none;
  border:none;
  border-left:2px solid transparent;
  color:var(--dim);
  padding:8px 14px;
  font-size:12px;
  text-align:left;
  display:flex;
  justify-content:space-between;
  align-items:center;
}
.cat-btn:hover{color:var(--fg)}
.cat-btn.active{background:var(--cat-active-bg);border-left-color:var(--accent);color:var(--accent)}
.cat-count{font-size:10px;opacity:.7}

/* ── Brief header ───────────────────────────────── */
.brief-hdr{
  padding:18px 22px 14px;
  border-bottom:1px solid var(--bd);
  flex-shrink:0;
}
.prompt-line{font-size:12px;margin-bottom:10px;color:var(--fg)}
.c-accent{color:var(--accent)}
.c-dim{color:var(--dim)}
.brief-block{border-left:3px solid var(--accent);padding-left:12px}
.brief-title{color:var(--accent);font-weight:700;font-size:15px;letter-spacing:.5px}
.brief-meta{font-size:11px;color:var(--dim);margin-top:4px}

/* ── Active category label ──────────────────────── */
.active-label{
  padding:8px 22px;
  border-bottom:1px solid var(--bd);
  font-size:11px;
  color:var(--dim);
  flex-shrink:0;
}

/* ── News items ─────────────────────────────────── */
.item{border-bottom:1px solid var(--bd)}
.item:last-child{border-bottom:none}

.item-row{
  padding:13px 22px;
  cursor:pointer;
  display:flex;
  gap:11px;
  align-items:flex-start;
}
.item-row:hover{background:rgba(95,179,161,.04)}

.item-idx{color:var(--dim);font-size:11px;min-width:20px;flex-shrink:0;margin-top:3px}
.item-body{flex:1;min-width:0}

.item-title-en{color:var(--accent);font-size:14px;line-height:1.5;margin-bottom:4px;max-width:var(--measure)}
.item-title-en a{color:inherit;text-decoration:none}
.item-title-en a:hover{text-decoration:underline;text-underline-offset:3px}
.item-title-zh{color:var(--fg-soft);font-size:12px;line-height:1.6;margin-bottom:7px;max-width:var(--measure)}

.item-meta-row{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.cat-badge{border:1px solid var(--tag-bd);color:var(--dim);padding:1px 6px;font-size:10px;border-radius:2px;flex-shrink:0}
.source-time{color:var(--dim);font-size:11px}
.votes{margin-left:auto;display:flex;gap:5px}

.vote-btn{
  background:none;
  border:1px solid var(--tag-bd);
  color:var(--dim);
  padding:1px 7px;
  font-size:11px;
  border-radius:2px;
  transition:all .12s;
  user-select:none;
  display:inline-block;
}
.vote-btn:hover{border-color:var(--fg);color:var(--fg)}
.vote-btn.voted-up{border-color:var(--accent);background:#5fb3a118;color:var(--accent)}
.vote-btn.voted-dn{border-color:var(--red);background:#e05a4e18;color:var(--red)}
.vote-up  {animation:voteUp   .3s ease}
.vote-down{animation:voteDown .3s ease}

.item-tags{margin-top:5px;font-size:10px;color:var(--dim);opacity:.75}

.expand-arrow{
  color:var(--dim);
  font-size:11px;
  flex-shrink:0;
  margin-top:2px;
  transition:transform .2s;
  display:inline-block;
}
.item.expanded .expand-arrow{transform:rotate(90deg)}

/* ── Expand animation ───────────────────────────── */
.item-summary{
  max-height:0;
  overflow:hidden;
  opacity:0;
  transition:max-height .3s ease,opacity .2s ease;
}
.item.expanded .item-summary{max-height:400px;opacity:1}
.item-summary-inner{padding:0 22px 16px 53px}
.summary-en{font-size:13px;line-height:1.8;color:var(--fg);margin-bottom:8px;max-width:var(--measure)}
.summary-zh{font-size:12px;line-height:1.8;color:var(--fg-soft);max-width:var(--measure)}
.summary-zh::before{content:'# '}


/* ── Archive list ───────────────────────────────── */
.archive-list{flex:1;overflow-y:auto;padding:6px 0}
.archive-date{
  display:flex;
  align-items:center;
  gap:6px;
  padding:4px 10px 4px 14px;
  font-size:12px;
  color:var(--dim);
  transition:color .1s;
}
.archive-date:hover{color:var(--accent)}

/* ── Inferred prefs panel ───────────────────────── */
.prefs-panel{border-top:1px solid var(--bd);flex-shrink:0}
.prefs-body{padding:10px 14px 8px}
.prefs-hdr{font-size:9px;color:var(--dim);letter-spacing:2px;text-transform:uppercase;margin-bottom:7px}
.prefs-hint{font-size:10px;color:var(--dim);line-height:1.7}
.prefs-group{font-size:8px;color:var(--dim);opacity:.6;margin-bottom:4px;letter-spacing:.5px}
.prefs-group.mt{margin-top:6px}
.prefs-row{display:flex;align-items:center;gap:5px;margin-bottom:3px}
.prefs-tag{font-size:10px;color:var(--dim);width:64px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex-shrink:0}
.prefs-bar{font-size:9px;letter-spacing:-1px;line-height:1}
.prefs-bar.boost{color:var(--accent)}
.prefs-bar.filter{color:var(--red)}
.prefs-div{border-top:1px solid var(--bd);margin:8px 0 6px}
.prefs-explore{font-size:10px;color:var(--dim)}
.prefs-seren{font-size:9px;color:var(--dim);opacity:.5;margin-top:2px;line-height:1.5}
.prefs-next{font-size:9px;color:var(--dim);margin-top:7px}

/* ── Status bar ─────────────────────────────────── */
.status-bar{
  padding:10px 14px 12px;
  border-top:1px solid var(--bd);
  flex-shrink:0;
  font-size:10px;
  line-height:1.9;
}

/* ── Theme button ───────────────────────────────── */
.theme-btn{
  background:none;
  border:1px solid var(--bd);
  color:var(--dim);
  padding:2px 10px;
  border-radius:4px;
  font-size:11px;
}
.theme-btn:hover{color:var(--fg);border-color:var(--dim)}

/* ── Mobile-only elements (hidden on desktop) ───── */
.mobile-header,.cat-tabs,.mobile-nav{display:none}
.sheet,.sheet-backdrop{display:none}

/* ── Mobile (< 860px) ───────────────────────────── */
@media (max-width:860px){
  body{padding:0;align-items:stretch}

  #app{
    width:100%;
    height:100svh;
    border-radius:0;
    border:none;
    box-shadow:none;
  }

  /* hide desktop-only */
  .window-chrome,.col-left,.col-right{display:none}

  /* show mobile elements */
  .mobile-header{
    display:flex;
    background:var(--chrome);
    padding:11px 16px;
    align-items:center;
    justify-content:space-between;
    border-bottom:1px solid var(--bd);
    flex-shrink:0;
  }
  .mobile-title{font-size:13px;color:var(--dim)}

  .cat-tabs{
    display:flex;
    overflow-x:auto;
    scrollbar-width:none;
    -webkit-overflow-scrolling:touch;
    background:var(--sidebar);
    border-bottom:1px solid var(--bd);
    flex-shrink:0;
  }
  .cat-tabs::-webkit-scrollbar{display:none}

  .cat-tab{
    flex-shrink:0;
    background:none;
    border:none;
    border-bottom:2px solid transparent;
    color:var(--dim);
    padding:9px 14px;
    font-size:12px;
  }
  .cat-tab.active{border-bottom-color:var(--accent);color:var(--accent)}

  /* main column adjustments */
  .brief-hdr{padding:14px 16px 10px}
  .prompt-line{font-size:11px}
  .brief-title{font-size:14px}
  .brief-meta{font-size:10px}
  .active-label{padding:7px 16px}
  .item-row{padding:12px 16px}
  .item-summary-inner{padding:0 16px 14px 44px}

  /* mobile bottom nav */
  .mobile-nav{
    display:flex;
    border-top:1px solid var(--bd);
    background:var(--sidebar);
    flex-shrink:0;
  }
  .mobile-nav button{
    flex:1;
    background:none;
    border:none;
    color:var(--dim);
    padding:13px;
    font-size:11px;
  }
  .mobile-nav button:first-child{border-right:1px solid var(--bd)}

  /* bottom sheets */
  .sheet-backdrop{
    display:block;
    position:fixed;
    inset:0;
    background:rgba(0,0,0,.55);
    z-index:50;
    opacity:0;
    pointer-events:none;
    transition:opacity .25s;
  }
  .sheet-backdrop.visible{opacity:1;pointer-events:auto}

  .sheet{
    display:flex;
    flex-direction:column;
    position:fixed;
    bottom:0;left:0;right:0;
    z-index:51;
    background:var(--sidebar);
    border-top:1px solid var(--bd);
    border-radius:10px 10px 0 0;
    max-height:70vh;
    transform:translateY(100%);
    transition:transform .3s cubic-bezier(.4,0,.2,1);
  }
  .sheet.open{transform:translateY(0)}

  .sheet-hdr{
    padding:12px 18px 8px;
    display:flex;
    align-items:center;
    justify-content:space-between;
    flex-shrink:0;
    border-bottom:1px solid var(--bd);
  }
  .sheet-title{font-size:9px;color:var(--dim);letter-spacing:2px;text-transform:uppercase}
  .sheet-close{background:none;border:none;color:var(--dim);font-size:20px;line-height:1}
  .sheet-body{overflow-y:auto;flex:1}
}
"""

JS = """\
(function() {
  'use strict';

  // ── State ───────────────────────────────────────────────────────
  var _saved = localStorage.getItem('theme');
  var dark = _saved ? _saved === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
  var activeCat = 'ALL';
  var votes;
  try { votes = JSON.parse(localStorage.getItem('db-votes') || '{}'); } catch(e) { votes = {}; }
  var activeSheet = null;
  var voteTimers = {};

  var items = [].slice.call(document.querySelectorAll('.item'));

  // ── Theme ───────────────────────────────────────────────────────
  function applyTheme() {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    document.querySelectorAll('.theme-btn').forEach(function(b) {
      b.textContent = dark ? 'light' : 'dark';
    });
  }
  document.querySelectorAll('.theme-btn').forEach(function(b) {
    b.addEventListener('click', function() {
      dark = !dark;
      localStorage.setItem('theme', dark ? 'dark' : 'light');
      applyTheme();
    });
  });

  // ── Maximize (green dot + dblclick title) ───────────────────────
  var app = document.getElementById('app');
  function toggleMaximize() { app.classList.toggle('maximized'); }
  var greenDot = document.querySelector('.chrome-dot-g');
  if (greenDot) greenDot.addEventListener('click', function(e) { e.stopPropagation(); toggleMaximize(); });
  var winChrome = document.querySelector('.window-chrome');
  if (winChrome) winChrome.addEventListener('dblclick', function(e) {
    if (e.target.closest && e.target.closest('.theme-btn')) return;
    toggleMaximize();
  });

    // ── Category filter ─────────────────────────────────────────────
  function pickCat(cat) {
    activeCat = cat;
    document.querySelectorAll('.cat-btn, .cat-tab').forEach(function(b) {
      b.classList.toggle('active', b.dataset.cat === cat);
    });
    updateItems();
    closeSheet();
  }
  document.querySelectorAll('.cat-btn, .cat-tab').forEach(function(b) {
    b.addEventListener('click', function() { pickCat(b.dataset.cat); });
  });

  // ── Items visibility ────────────────────────────────────────────
  function updateItems() {
    var filtered = activeCat === 'ALL'
      ? items
      : items.filter(function(el) { return el.dataset.cat === activeCat; });
    items.forEach(function(el) { el.hidden = true; });
    filtered.forEach(function(el) { el.hidden = false; });
    document.querySelectorAll('.active-label').forEach(function(el) {
      el.innerHTML = '<span class="c-accent">[' + activeCat + ']</span> ' + filtered.length + ' items';
    });
  }

  // ── Expand / collapse ───────────────────────────────────────────
  items.forEach(function(el) {
    var row = el.querySelector('.item-row');
    if (!row) return;
    row.addEventListener('click', function(e) {
      if (e.target.closest && e.target.closest('.vote-btn')) return;
      el.classList.toggle('expanded');
    });
  });

  // ── Supabase ────────────────────────────────────────────────────
  function supaFetch(method, path, body, prefer) {
    if (!SUPA_URL || !SUPA_KEY) return;
    var headers = {'apikey': SUPA_KEY, 'Authorization': 'Bearer ' + SUPA_KEY, 'Content-Type': 'application/json'};
    if (prefer) headers['Prefer'] = prefer;
    fetch(SUPA_URL + '/rest/v1/' + path, {method: method, headers: headers, body: body ? JSON.stringify(body) : undefined}).catch(function() {});
  }

  function supaUpsert(articleKey, tags, vote) {
    supaFetch('POST', 'votes', {article_key: articleKey, date: TODAY, tags: tags, vote: vote}, 'resolution=merge-duplicates');
  }

  function supaDelete(articleKey) {
    supaFetch('DELETE', 'votes?article_key=eq.' + encodeURIComponent(articleKey));
  }

  function loadVotes() {
    if (!SUPA_URL || !SUPA_KEY) return;
    fetch(SUPA_URL + '/rest/v1/votes?select=article_key,vote&date=eq.' + TODAY, {
      headers: {'apikey': SUPA_KEY, 'Authorization': 'Bearer ' + SUPA_KEY}
    }).then(function(r) { return r.json(); }).then(function(data) {
      if (!Array.isArray(data)) return;
      data.forEach(function(row) {
        var id = row.article_key.split('-').pop();
        votes[id] = row.vote;
      });
      localStorage.setItem('db-votes', JSON.stringify(votes));
      items.forEach(function(el) { updateVoteUI(el.dataset.id); });
      renderPrefs();
    }).catch(function() {});
  }

    // ── Votes ───────────────────────────────────────────────────────
  function updateVoteUI(id) {
    var v = votes[id];
    var up = document.querySelector('.vote-btn[data-id="' + id + '"][data-val="1"]');
    var dn = document.querySelector('.vote-btn[data-id="' + id + '"][data-val="-1"]');
    if (up) up.className = 'vote-btn' + (v === 1  ? ' voted-up' : '');
    if (dn) dn.className = 'vote-btn' + (v === -1 ? ' voted-dn' : '');
  }

  items.forEach(function(el) { updateVoteUI(el.dataset.id); });

  document.addEventListener('click', function(e) {
    var btn = e.target.closest && e.target.closest('.vote-btn');
    if (!btn) return;
    e.stopPropagation();
    var id  = btn.dataset.id;
    var val = +btn.dataset.val;
    var articleKey = TODAY + '-' + id;
    if (votes[id] === val) {
      delete votes[id];
      supaDelete(articleKey);
    } else {
      votes[id] = val;
      var tags = [];
      try { var itemEl = document.querySelector('.item[data-id="' + id + '"]'); tags = JSON.parse((itemEl && itemEl.dataset.tags) || '[]'); } catch(ex) {}
      supaUpsert(articleKey, tags, val);
    }
    localStorage.setItem('db-votes', JSON.stringify(votes));
    updateVoteUI(id);
    var cls = val === 1 ? 'vote-up' : 'vote-down';
    var key = id + '_' + val;
    btn.classList.remove(cls);
    clearTimeout(voteTimers[key]);
    void btn.offsetWidth;
    btn.classList.add(cls);
    voteTimers[key] = setTimeout(function() { btn.classList.remove(cls); }, 350);
    renderPrefs();
  });

  // ── Inferred preferences ────────────────────────────────────────
  function computeTagPrefs() {
    var scores = {};
    items.forEach(function(el) {
      var v = votes[el.dataset.id];
      if (v === undefined) return;
      try {
        JSON.parse(el.dataset.tags || '[]').forEach(function(tag) {
          scores[tag] = (scores[tag] || 0) + v;
        });
      } catch(e) {}
    });
    return Object.keys(scores)
      .map(function(t) { return {tag: t, score: scores[t]}; })
      .filter(function(t) { return t.score !== 0; })
      .sort(function(a, b) { return Math.abs(b.score) - Math.abs(a.score); })
      .slice(0, 7);
  }

  function bar(n, filled) {
    var s = ''; var k = Math.min(Math.abs(n), 4);
    for (var i = 0; i < k; i++) s += filled;
    for (var i = k; i < 4; i++) s += '░';
    return s;
  }

  function renderPrefs() {
    var prefs   = computeTagPrefs();
    var boosts  = prefs.filter(function(t) { return t.score > 0; });
    var filters = prefs.filter(function(t) { return t.score < 0; });
    var h = '<div class="prefs-hdr">inferred</div>';
    if (!prefs.length) {
      h += '<div class="prefs-hint">↑↓ stories to<br>personalize feed</div>';
    } else {
      if (boosts.length) {
        h += '<div class="prefs-group">BOOST</div>';
        boosts.forEach(function(t) {
          h += '<div class="prefs-row"><span class="prefs-tag">' + t.tag + '</span>'
             + '<span class="prefs-bar boost">' + bar(t.score, '█') + '</span></div>';
        });
      }
      if (filters.length) {
        h += '<div class="prefs-group mt">FILTER</div>';
        filters.forEach(function(t) {
          h += '<div class="prefs-row"><span class="prefs-tag">' + t.tag + '</span>'
             + '<span class="prefs-bar filter">' + bar(t.score, '▒') + '</span></div>';
        });
      }
      h += '<div class="prefs-div"></div>';
      h += '<div class="prefs-group">EXPLORE</div>';
      h += '<div class="prefs-explore">~20% unrelated</div>';
      h += '<div class="prefs-seren">serendipity always on</div>';
      h += '<div class="prefs-next">→ next update</div>';
    }
    document.querySelectorAll('.prefs-body').forEach(function(el) {
      el.innerHTML = h;
    });
  }

  // ── Mobile bottom sheets ────────────────────────────────────────
  var backdrop = document.getElementById('backdrop');

  function openSheet(id) {
    activeSheet = id;
    document.getElementById('sheet-' + id).classList.add('open');
    if (backdrop) backdrop.classList.add('visible');
    var nb = document.getElementById('btn-' + id);
    if (nb) nb.style.color = '#5fb3a1';
  }

  function closeSheet() {
    if (!activeSheet) return;
    var s = document.getElementById('sheet-' + activeSheet);
    if (s) s.classList.remove('open');
    if (backdrop) backdrop.classList.remove('visible');
    var nb = document.getElementById('btn-' + activeSheet);
    if (nb) nb.style.color = '';
    activeSheet = null;
  }

  ['archive', 'prefs'].forEach(function(id) {
    var btn = document.getElementById('btn-' + id);
    if (!btn) return;
    btn.addEventListener('click', function() {
      if (activeSheet === id) closeSheet(); else { closeSheet(); openSheet(id); }
    });
  });

  document.querySelectorAll('.sheet-close').forEach(function(btn) {
    btn.addEventListener('click', closeSheet);
  });

  if (backdrop) backdrop.addEventListener('click', closeSheet);

  // ── Init ────────────────────────────────────────────────────────
  applyTheme();
  updateItems();
  renderPrefs();
  loadVotes();

  // Expand first visible item
  for (var i = 0; i < items.length; i++) {
    if (!items[i].hidden) { items[i].classList.add('expanded'); break; }
  }
})();
"""

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Daily Brief</title>
<meta name="theme-color" content="#1a1a1a">
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='6' fill='%231a1a1a'/><text x='5' y='23' font-size='20' font-family='monospace' fill='%235fb3a1'>&#9658;</text></svg>">
<link rel="stylesheet" href="/assets/style.css">
<link rel="alternate" type="application/rss+xml" title="Daily Brief RSS" href="/feed.xml">
<link rel="alternate" type="application/feed+json" title="Daily Brief JSON Feed" href="/feed.json">

</head>
<body>

<div id="app">
  <!-- Desktop window chrome -->
  <div class="window-chrome">
    <div class="chrome-dots">
      <div class="chrome-dot chrome-dot-r"></div>
      <div class="chrome-dot chrome-dot-y"></div>
      <div class="chrome-dot chrome-dot-g"></div>
    </div>
    <span class="chrome-title">daily-brief &#8212; zsh</span>
    <button class="theme-btn" id="theme-btn-d">light</button>
  </div>

  <!-- Mobile header -->
  <div class="mobile-header">
    <span class="mobile-title">daily-brief</span>
    <button class="theme-btn" id="theme-btn-m">light</button>
  </div>

  <!-- Mobile category tabs -->
  <div class="cat-tabs">
    __CAT_TABS__
  </div>

  <!-- Three columns -->
  <div class="columns">

    <!-- LEFT: category filter -->
    <aside class="col-left">
      <div class="sidebar-hdr">filter</div>
      <nav class="cat-nav">
        __CAT_NAV__
      </nav>
    </aside>

    <!-- MAIN: articles -->
    <main class="col-main">
      <div class="brief-hdr">
        <div class="prompt-line">
          <span class="c-accent">you@news</span><span class="c-dim"> ~ $ </span>brief --date __PROMPT_DATE__ --lang bilingual
        </div>
        <div class="brief-block">
          <div class="brief-title">&#9658; DAILY BRIEF</div>
          <div class="brief-meta">__BRIEF_DATE__ &middot; __BRIEF_TOTAL__ stories &middot; summarized by __BRIEF_MODEL__</div>
        </div>
      </div>
      <div class="active-label" id="active-label"></div>
      <div id="items-list">
        __ITEMS__
      </div>
    </main>

    <!-- RIGHT: archive + inferred prefs + status -->
    <aside class="col-right">
      <div class="col-right-top">
        <div class="sidebar-hdr">archive</div>
        <div class="archive-list">
          __ARCHIVE__
        </div>
      </div>
      <div class="prefs-panel">
        <div class="prefs-body" id="prefs-desktop"></div>
      </div>
      <div class="status-bar">
        <div class="c-accent">&#10003; done</div>
        <div class="c-dim">__STATUS_TOTAL__ fetched</div>
        <div class="c-dim">next ~24h</div>
        <div class="c-dim">__STATUS_TIME__</div>
      </div>
    </aside>

  </div><!-- /.columns -->

  <!-- Mobile bottom nav -->
  <nav class="mobile-nav">
    <button id="btn-archive">archive</button>
    <button id="btn-prefs">prefs</button>
  </nav>
</div><!-- /#app -->

<!-- Mobile bottom sheets (fixed, outside #app) -->
<div class="sheet-backdrop" id="backdrop"></div>
<div class="sheet" id="sheet-archive">
  <div class="sheet-hdr">
    <span class="sheet-title">archive</span>
    <button class="sheet-close" data-sheet="archive">&#215;</button>
  </div>
  <div class="sheet-body archive-list">
    __ARCHIVE__
  </div>
</div>
<div class="sheet" id="sheet-prefs">
  <div class="sheet-hdr">
    <span class="sheet-title">preferences</span>
    <button class="sheet-close" data-sheet="prefs">&#215;</button>
  </div>
  <div class="sheet-body prefs-body" id="prefs-mobile"></div>
</div>

<script>
var SUPA_URL='__SUPABASE_URL__';
var SUPA_KEY='__SUPABASE_KEY__';
var TODAY='__TODAY__';
</script>
<script src="/assets/app.js"></script>
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

def summarize(items: list[dict]) -> list[dict]:
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
    for attempt in range(1, 6):
        try:
            response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            break
        except Exception as e:
            if attempt == 5:
                raise
            wait = 30 * attempt
            print(f"  ✗ attempt {attempt} failed ({e}), retrying in {wait}s...", file=sys.stderr)
            time.sleep(wait)

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

    return items

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


def generate_html(items: list[dict], tag_scores: dict[str, int] | None = None) -> str:
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
    html = html.replace("__BRIEF_MODEL__",   GEMINI_MODEL)
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

    items = summarize(items)
    html  = generate_html(items, tag_scores or None)

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✓ wrote index.html ({len(html)} bytes)", file=sys.stderr)

    os.makedirs("assets", exist_ok=True)
    with open("assets/style.css", "w", encoding="utf-8") as f:
        f.write(CSS)
    with open("assets/app.js", "w", encoding="utf-8") as f:
        f.write(JS)
    print("\u2713 wrote assets/style.css and assets/app.js", file=sys.stderr)
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
