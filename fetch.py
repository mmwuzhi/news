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

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Daily Brief</title>
<meta name="theme-color" content="#1a1a1a">
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='6' fill='%231a1a1a'/><text x='5' y='23' font-size='20' font-family='monospace' fill='%235fb3a1'>&#9658;</text></svg>">
<style>
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
  --accent:  #5fb3a1;
  --dim:     #575757;
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
  --dim:     #999999;
  --bd:      #dddddd;
  --tag-bd:  #cccccc;
  --cat-active-bg: #e4e4e0;
}

/* ── Page frame (desktop) ───────────────────────── */
body{
  background:var(--page-bg);
  font-family:'JetBrains Mono','Maple Mono','Fira Code','Menlo',monospace;
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
}
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
.chrome-dot-g{background:#28c840}
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

.item-title-en{color:var(--accent);font-size:13px;line-height:1.45;margin-bottom:4px}
.item-title-zh{color:var(--dim);font-size:11px;line-height:1.4;margin-bottom:7px}

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

.item-tags{margin-top:5px;font-size:9px;color:var(--dim);opacity:.5}

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
.summary-en{font-size:12px;line-height:1.8;color:var(--fg);margin-bottom:8px}
.summary-zh{font-size:11px;line-height:1.8;color:var(--dim)}
.summary-zh::before{content:'# '}

/* ── Load more ──────────────────────────────────── */
.load-more-btn{
  display:block;
  width:calc(100% - 44px);
  margin:14px 22px;
  background:none;
  border:1px solid var(--bd);
  color:var(--dim);
  padding:8px 0;
  font-size:12px;
  text-align:center;
}
.load-more-btn:hover{border-color:var(--accent);color:var(--fg)}

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
  .load-more-btn{width:calc(100% - 32px);margin:12px 16px}

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
</style>
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
      <button class="load-more-btn" id="load-more">
        <span class="c-accent">$</span> load --more
      </button>
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
(function() {
  'use strict';

  // ── State ───────────────────────────────────────────────────────
  var _saved = localStorage.getItem('theme');
  var dark = _saved ? _saved === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
  var activeCat = 'ALL';
  var visible = 8;
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

  // ── Category filter ─────────────────────────────────────────────
  function pickCat(cat) {
    activeCat = cat;
    visible = 8;
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
    var shown = filtered.slice(0, visible);
    shown.forEach(function(el) { el.hidden = false; });
    document.querySelectorAll('.active-label').forEach(function(el) {
      el.innerHTML = '<span class="c-accent">[' + activeCat + ']</span> ' + shown.length + ' items';
    });
    var btn = document.getElementById('load-more');
    if (btn) btn.hidden = filtered.length <= visible;
  }

  var loadMoreBtn = document.getElementById('load-more');
  if (loadMoreBtn) {
    loadMoreBtn.addEventListener('click', function() {
      visible += 4;
      updateItems();
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
    if (votes[id] === val) delete votes[id]; else votes[id] = val;
    localStorage.setItem('db-votes', JSON.stringify(votes));
    updateVoteUI(id);
    var cls = val === 1 ? 'vote-up' : 'vote-down';
    var key = id + '_' + val;
    btn.classList.remove(cls);
    clearTimeout(voteTimers[key]);
    void btn.offsetWidth; // force reflow to replay animation
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

  // Expand first visible item
  for (var i = 0; i < items.length; i++) {
    if (!items[i].hidden) { items[i].classList.add('expanded'); break; }
  }
})();
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
        item["titleCN"]  = s.get("titleCN", "")
        item["en"]       = s.get("en", "Summary unavailable.")
        item["zh"]       = s.get("zh", "摘要暂不可用。")
        item["category"] = s.get("category", "TECH")
        item["tags"]     = s.get("tags", [])

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
    return (
        f'\n<div class="item{expanded}" data-id="{idx}" data-cat="{cat}" data-tags="{attr_json(tags)}">'
        f'\n  <div class="item-row">'
        f'\n    <span class="item-idx">{idx:02d}</span>'
        f'\n    <div class="item-body">'
        f'\n      <div class="item-title-en">{escape(item["title"])}</div>'
        f'\n      <div class="item-title-zh">{escape(item.get("titleCN", ""))}</div>'
        f'\n      <div class="item-meta-row">'
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


def get_archive_dates() -> list[str]:
    if not os.path.isdir("archive"):
        return []
    return sorted(
        [f[:-5] for f in os.listdir("archive") if f.endswith(".html") and len(f) == 15],
        reverse=True,
    )[:14]


def apply_cat_limit(items: list[dict]) -> tuple[list[dict], dict[str, list]]:
    groups: dict[str, list] = {}
    for item in items:
        groups.setdefault(item["category"], []).append(item)
    capped: list[dict] = []
    capped_groups: dict[str, list] = {}
    for cat in CATEGORY_ORDER:
        subset = groups.get(cat, [])[:MAX_PER_CAT]
        capped.extend(subset)
        if subset:
            capped_groups[cat] = subset
    return capped, capped_groups


def generate_html(items: list[dict]) -> str:
    now = datetime.now(timezone.utc)
    items, groups = apply_cat_limit(items)
    total = len(items)

    items_html = "".join(build_item(item, i + 1, i == 0) for i, item in enumerate(items))
    archive_html = build_archive_list(get_archive_dates())

    html = HTML_TEMPLATE
    html = html.replace("__CAT_NAV__",      build_cat_nav(groups, total))
    html = html.replace("__CAT_TABS__",     build_cat_tabs(groups))
    html = html.replace("__PROMPT_DATE__",  now.strftime("%Y-%m-%d"))
    html = html.replace("__BRIEF_DATE__",   now.strftime("%a %b %d %Y"))
    html = html.replace("__BRIEF_TOTAL__",  str(total))
    html = html.replace("__BRIEF_MODEL__",  GEMINI_MODEL)
    html = html.replace("__ITEMS__",        items_html)
    html = html.replace("__ARCHIVE__",      archive_html)  # replaces both occurrences
    html = html.replace("__STATUS_TOTAL__", str(total))
    html = html.replace("__STATUS_TIME__",  now.strftime("%H:%M UTC"))
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

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✓ wrote index.html ({len(html)} bytes)", file=sys.stderr)

    os.makedirs("archive", exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    archive_path = f"archive/{date_str}.html"
    with open(archive_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✓ wrote {archive_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
