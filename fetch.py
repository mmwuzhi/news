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

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
  <title>Daily Brief</title>
  <meta name="theme-color" content="#1a1a1a">
  <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='6' fill='%231a1a1a'/><text x='5' y='23' font-size='20' font-family='monospace' fill='%235fb3a1'>▶</text></svg>">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
  <script src="https://unpkg.com/react@18.3.1/umd/react.production.min.js" crossorigin="anonymous"></script>
  <script src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js" crossorigin="anonymous"></script>
  <script src="https://unpkg.com/@babel/standalone@7.29.0/babel.min.js" crossorigin="anonymous"></script>
  <style>
    @keyframes voteUp   { 0%,100%{transform:scale(1)} 35%{transform:scale(1.45) translateY(-2px)} }
    @keyframes voteDown { 0%,100%{transform:scale(1)} 35%{transform:scale(1.45) translateY( 2px)} }
    *, *::before, *::after { box-sizing:border-box; margin:0; padding:0; }
    html, body { height:100%; background:#0d0d0d; display:flex; align-items:center; justify-content:center; font-family:'JetBrains Mono',monospace; }
    #root { width:100%; height:100%; display:flex; align-items:center; justify-content:center; padding:32px; }
    button { cursor:pointer; font-family:'JetBrains Mono',monospace; }
    a { text-decoration:none; }
    ::-webkit-scrollbar { width:3px; }
    ::-webkit-scrollbar-track { background:transparent; }
    ::-webkit-scrollbar-thumb { background:#333; border-radius:2px; }
    .vote-up   { animation:voteUp   .3s ease; display:inline-block; }
    .vote-down { animation:voteDown .3s ease; display:inline-block; }
    @media (max-width:860px) { #root { padding:0; align-items:stretch; } }
  </style>
</head>
<body>
<div id="root"></div>
<script>
  window.__DATA__    = __DATA_JSON__;
  window.__META__    = __META_JSON__;
  window.__ARCHIVE__ = __ARCHIVE_JSON__;
</script>
<script type="text/babel">
  const { useState, useMemo, useEffect } = React;

  const ALL_NEWS      = window.__DATA__;
  const META          = window.__META__;
  const ARCHIVE_DATES = window.__ARCHIVE__;
  const CATS = ['ALL','AI','TECH','FINA','SCI','WORLD'];

  function BottomSheet({ id, title, sheet, onClose, C, children }) {
    const open = sheet === id;
    return (
      <>
        <div onClick={onClose} style={{position:'fixed',inset:0,background:'rgba(0,0,0,.55)',zIndex:50,opacity:open?1:0,pointerEvents:open?'auto':'none',transition:'opacity .25s'}} />
        <div style={{position:'fixed',bottom:0,left:0,right:0,zIndex:51,background:C.sidebar,borderTop:`1px solid ${C.bd}`,borderRadius:'10px 10px 0 0',maxHeight:'70vh',overflow:'hidden',display:'flex',flexDirection:'column',transform:open?'translateY(0)':'translateY(100%)',transition:'transform .3s cubic-bezier(.4,0,.2,1)'}}>
          <div style={{padding:'12px 18px 8px',display:'flex',alignItems:'center',justifyContent:'space-between',flexShrink:0,borderBottom:`1px solid ${C.bd}`}}>
            <span style={{fontSize:9,color:C.dim,letterSpacing:'2px',textTransform:'uppercase'}}>{title}</span>
            <button onClick={onClose} style={{background:'none',border:'none',color:C.dim,fontSize:20,lineHeight:1}}>×</button>
          </div>
          <div style={{overflowY:'auto',flex:1}}>{children}</div>
        </div>
      </>
    );
  }

  function App() {
    const [dark, setDark]           = useState(() => localStorage.getItem('theme') !== 'light');
    const [activeCat, setActiveCat] = useState('ALL');
    const [expanded, setExpanded]   = useState(new Set([1]));
    const [visible, setVisible]     = useState(8);
    const [hoverDate, setHoverDate] = useState(null);
    const [votes, setVotes] = useState(() => {
      try { return JSON.parse(localStorage.getItem('db-votes')||'{}'); } catch { return {}; }
    });
    const [voteAnim, setVoteAnim] = useState({});
    const [sheet, setSheet]       = useState(null);
    const [isMobile, setIsMobile] = useState(() => window.innerWidth < 860);

    useEffect(() => {
      localStorage.setItem('theme', dark ? 'dark' : 'light');
    }, [dark]);

    useEffect(() => {
      const h = () => setIsMobile(window.innerWidth < 860);
      window.addEventListener('resize', h);
      return () => window.removeEventListener('resize', h);
    }, []);

    const filtered  = useMemo(() => ALL_NEWS.filter(n => activeCat==='ALL'||n.cat===activeCat), [activeCat]);
    const shown     = filtered.slice(0, visible);
    const hasMore   = visible < filtered.length;
    const catCount  = cat => cat==='ALL' ? ALL_NEWS.length : ALL_NEWS.filter(n=>n.cat===cat).length;

    const toggle     = id  => setExpanded(p => { const s=new Set(p); s.has(id)?s.delete(id):s.add(id); return s; });
    const pickCat    = cat => { setActiveCat(cat); setVisible(8); setSheet(null); };
    const loadMore   = ()  => setVisible(v => Math.min(v+4, filtered.length));
    const handleVote = (id, val) => {
      setVotes(p => { const n={...p}; n[id]===val?delete n[id]:(n[id]=val); localStorage.setItem('db-votes',JSON.stringify(n)); return n; });
      setVoteAnim(p => ({ ...p, [id]: { val, nonce: Math.random() } }));
    };

    const tagPrefs = useMemo(() => {
      const sc = {};
      ALL_NEWS.forEach(item => {
        const v = votes[item.id];
        if (v !== undefined) item.tags.forEach(t => { sc[t]=(sc[t]||0)+v; });
      });
      return Object.entries(sc).map(([tag,score])=>({tag,score})).filter(t=>t.score!==0).sort((a,b)=>Math.abs(b.score)-Math.abs(a.score)).slice(0,7);
    }, [votes]);

    const C = {
      bg:      dark?'#1a1a1a':'#f5f4ef',
      chrome:  dark?'#242424':'#e5e4df',
      sidebar: dark?'#1e1e1e':'#ededea',
      fg:      dark?'#d4d4d4':'#2a2a2a',
      accent:  '#5fb3a1',
      dim:     dark?'#575757':'#999',
      bd:      dark?'#2c2c2c':'#ddd',
      tagBd:   dark?'#333':'#ccc',
      red:     '#e05a4e',
    };

    const catList = CATS.map(cat => {
      const active = activeCat===cat;
      return (
        <button key={cat} onClick={()=>pickCat(cat)} style={{width:'100%',background:active?(dark?'#252525':'#e4e4e0'):'none',border:'none',borderLeft:active?`2px solid ${C.accent}`:'2px solid transparent',color:active?C.accent:C.dim,padding:'8px 14px',fontSize:12,textAlign:'left',display:'flex',justifyContent:'space-between',alignItems:'center'}}>
          <span>{cat}</span>
          <span style={{fontSize:10,opacity:.7}}>{catCount(cat)}</span>
        </button>
      );
    });

    const archiveList = (
      <div style={{padding:'6px 0'}}>
        {ARCHIVE_DATES.map(date => (
          <div key={date} style={{padding:'4px 10px 4px 14px'}}>
            <a href={`/archive/${date}.html`}
              onMouseEnter={()=>setHoverDate(date)} onMouseLeave={()=>setHoverDate(null)}
              style={{fontSize:12,display:'flex',alignItems:'center',gap:6,color:hoverDate===date?C.accent:C.dim,transition:'color .1s'}}>
              <span style={{fontSize:8,color:C.accent,opacity:hoverDate===date?1:0}}>▶</span>
              <span>{date}</span>
            </a>
          </div>
        ))}
      </div>
    );

    const bar = (n, max, col) => (
      <span style={{fontSize:9,color:col,letterSpacing:'-1px',lineHeight:1}}>
        {'█'.repeat(Math.min(n,max))}{'░'.repeat(Math.max(0,max-Math.min(n,max)))}
      </span>
    );

    const inferredPanel = (
      <div style={{padding:'10px 14px 8px'}}>
        <div style={{fontSize:9,color:C.dim,letterSpacing:'2px',textTransform:'uppercase',marginBottom:7}}>inferred</div>
        {tagPrefs.length===0 && <div style={{fontSize:10,color:C.dim,lineHeight:1.7}}>↑↓ stories to<br/>personalize feed</div>}
        {tagPrefs.filter(t=>t.score>0).length>0 && <>
          <div style={{fontSize:8,color:C.dim,opacity:.6,marginBottom:4,letterSpacing:'.5px'}}>BOOST</div>
          {tagPrefs.filter(t=>t.score>0).map(({tag,score})=>(
            <div key={tag} style={{display:'flex',alignItems:'center',gap:5,marginBottom:3}}>
              <span style={{fontSize:10,color:C.dim,width:64,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flexShrink:0}}>{tag}</span>
              {bar(score,4,C.accent)}
            </div>
          ))}
        </>}
        {tagPrefs.filter(t=>t.score<0).length>0 && <>
          <div style={{fontSize:8,color:C.dim,opacity:.6,marginTop:6,marginBottom:4,letterSpacing:'.5px'}}>FILTER</div>
          {tagPrefs.filter(t=>t.score<0).map(({tag,score})=>(
            <div key={tag} style={{display:'flex',alignItems:'center',gap:5,marginBottom:3}}>
              <span style={{fontSize:10,color:C.dim,width:64,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flexShrink:0}}>{tag}</span>
              {bar(Math.abs(score),4,C.red)}
            </div>
          ))}
        </>}
        {tagPrefs.length>0 && <>
          <div style={{borderTop:`1px solid ${C.bd}`,margin:'8px 0 6px'}}></div>
          <div style={{fontSize:8,color:C.dim,opacity:.6,marginBottom:3,letterSpacing:'.5px'}}>EXPLORE</div>
          <div style={{fontSize:10,color:C.dim}}>~20% unrelated</div>
          <div style={{fontSize:9,color:C.dim,opacity:.5,marginTop:2,lineHeight:1.5}}>serendipity always on</div>
          <div style={{fontSize:9,color:C.dim,marginTop:7}}>→ next update</div>
        </>}
      </div>
    );

    const renderItems = (pad, expandPad) => {
      pad = pad || '13px 22px';
      expandPad = expandPad || '0 22px 16px 53px';
      return shown.map((item,i) => {
        const anim  = voteAnim[item.id];
        const upKey = `u${item.id}-${anim&&anim.val===1  ? anim.nonce : 0}`;
        const dnKey = `d${item.id}-${anim&&anim.val===-1 ? anim.nonce : 0}`;
        const voteBtn = (val, key, cls) => (
          <span key={key} className={cls} onClick={e=>{e.stopPropagation();handleVote(item.id,val);}}
            style={{display:'inline-block',cursor:'pointer',background:votes[item.id]===val?(val===1?`${C.accent}18`:`${C.red}18`):'none',border:`1px solid ${votes[item.id]===val?(val===1?C.accent:C.red):C.tagBd}`,color:votes[item.id]===val?(val===1?C.accent:C.red):C.dim,padding:'1px 7px',fontSize:11,borderRadius:2,transition:'all .12s',userSelect:'none'}}>
            {val===1?'↑':'↓'}
          </span>
        );
        return (
          <div key={item.id} style={{borderBottom:`1px solid ${C.bd}`}}>
            <div onClick={()=>toggle(item.id)} style={{padding:pad,cursor:'pointer',display:'flex',gap:11,alignItems:'flex-start'}}>
              <span style={{color:C.dim,fontSize:11,minWidth:20,flexShrink:0,marginTop:3}}>{String(i+1).padStart(2,'0')}</span>
              <div style={{flex:1,minWidth:0}}>
                <div style={{color:C.accent,fontSize:13,lineHeight:1.45,marginBottom:4}}>{item.title}</div>
                <div style={{color:C.dim,fontSize:11,lineHeight:1.4,marginBottom:7}}>{item.titleCN}</div>
                <div style={{display:'flex',gap:6,alignItems:'center',flexWrap:'wrap'}}>
                  <span style={{border:`1px solid ${C.tagBd}`,color:C.dim,padding:'1px 6px',fontSize:10,borderRadius:2,flexShrink:0}}>{item.cat}</span>
                  <span style={{color:C.dim,fontSize:11}}>{item.source} · {item.time}</span>
                  <span style={{flex:1}}></span>
                  {voteBtn( 1, upKey, anim&&anim.val=== 1 ? 'vote-up'   : '')}
                  {voteBtn(-1, dnKey, anim&&anim.val===-1 ? 'vote-down' : '')}
                </div>
                {item.tags.length>0 && <div style={{marginTop:5,fontSize:9,color:C.dim,opacity:.5}}>{item.tags.join(' · ')}</div>}
              </div>
              <span style={{color:C.dim,fontSize:11,flexShrink:0,marginTop:2,transition:'transform .2s',display:'inline-block',transform:expanded.has(item.id)?'rotate(90deg)':'none'}}>▸</span>
            </div>
            <div style={{maxHeight:expanded.has(item.id)?'400px':'0',overflow:'hidden',opacity:expanded.has(item.id)?1:0,transition:'max-height .3s ease, opacity .2s ease'}}>
              <div style={{padding:expandPad}}>
                <p style={{fontSize:12,lineHeight:1.8,color:C.fg,marginBottom:8}}>{item.summary}</p>
                <p style={{fontSize:11,lineHeight:1.8,color:C.dim}}>{'# '}{item.summaryCN}</p>
              </div>
            </div>
          </div>
        );
      });
    };

    const loadMoreBtn = hasMore && (
      <div style={{padding:'14px 22px'}}>
        <button onClick={loadMore} style={{background:'none',border:`1px solid ${C.bd}`,color:C.dim,padding:'8px 0',fontSize:12,width:'100%'}}>
          <span style={{color:C.accent}}>$</span> load --more
        </button>
      </div>
    );

    if (isMobile) return (
      <div style={{width:'100%',minHeight:'100svh',background:C.bg,color:C.fg,display:'flex',flexDirection:'column'}}>
        <div style={{background:C.chrome,padding:'11px 16px',display:'flex',alignItems:'center',justifyContent:'space-between',borderBottom:`1px solid ${C.bd}`,position:'sticky',top:0,zIndex:10,flexShrink:0}}>
          <span style={{fontSize:13,color:C.dim}}>daily-brief</span>
          <button onClick={()=>setDark(d=>!d)} style={{background:'none',border:`1px solid ${C.bd}`,color:C.dim,padding:'2px 10px',borderRadius:4,fontSize:11}}>{dark?'light':'dark'}</button>
        </div>
        <div style={{display:'flex',overflowX:'auto',borderBottom:`1px solid ${C.bd}`,background:C.sidebar,flexShrink:0,scrollbarWidth:'none',WebkitOverflowScrolling:'touch'}}>
          {CATS.map(cat => {
            const active=activeCat===cat;
            return <button key={cat} onClick={()=>pickCat(cat)} style={{flexShrink:0,background:'none',border:'none',borderBottom:active?`2px solid ${C.accent}`:'2px solid transparent',color:active?C.accent:C.dim,padding:'9px 14px',fontSize:12}}>{cat}</button>;
          })}
        </div>
        <div style={{padding:'14px 16px 10px',borderBottom:`1px solid ${C.bd}`,flexShrink:0}}>
          <div style={{fontSize:11,marginBottom:8}}><span style={{color:C.accent}}>you@news</span><span style={{color:C.dim}}> ~ $ </span><span>brief --date {META.dateStr}</span></div>
          <div style={{borderLeft:`3px solid ${C.accent}`,paddingLeft:10}}>
            <div style={{color:C.accent,fontWeight:700,fontSize:14}}>► DAILY BRIEF</div>
            <div style={{fontSize:10,color:C.dim,marginTop:3}}>{META.date} · {META.total} stories</div>
          </div>
        </div>
        <div style={{padding:'7px 16px',borderBottom:`1px solid ${C.bd}`,fontSize:11,color:C.dim,flexShrink:0}}>
          <span style={{color:C.accent}}>[{activeCat}]</span>{' '}{shown.length} items
        </div>
        <div style={{flex:1}}>
          {renderItems('12px 16px','0 16px 14px 44px')}
          {hasMore && <div style={{padding:'12px 16px'}}><button onClick={loadMore} style={{background:'none',border:`1px solid ${C.bd}`,color:C.dim,padding:'8px 0',fontSize:12,width:'100%'}}><span style={{color:C.accent}}>$</span> load --more</button></div>}
        </div>
        <div style={{display:'flex',borderTop:`1px solid ${C.bd}`,background:C.sidebar,flexShrink:0,position:'sticky',bottom:0,zIndex:10}}>
          <button onClick={()=>setSheet(s=>s==='archive'?null:'archive')} style={{flex:1,background:'none',border:'none',color:sheet==='archive'?C.accent:C.dim,padding:'13px',fontSize:11,borderRight:`1px solid ${C.bd}`}}>archive</button>
          <button onClick={()=>setSheet(s=>s==='prefs'?null:'prefs')}   style={{flex:1,background:'none',border:'none',color:sheet==='prefs'  ?C.accent:C.dim,padding:'13px',fontSize:11}}>prefs</button>
        </div>
        <BottomSheet id="archive" title="archive"     sheet={sheet} onClose={()=>setSheet(null)} C={C}>{archiveList}</BottomSheet>
        <BottomSheet id="prefs"   title="preferences" sheet={sheet} onClose={()=>setSheet(null)} C={C}>{inferredPanel}</BottomSheet>
      </div>
    );

    return (
      <div style={{width:960,maxHeight:680,background:C.bg,color:C.fg,borderRadius:10,overflow:'hidden',border:`1px solid ${C.bd}`,boxShadow:dark?'0 28px 72px rgba(0,0,0,.65)':'0 8px 36px rgba(0,0,0,.12)',display:'flex',flexDirection:'column'}}>
        <div style={{background:C.chrome,padding:'11px 16px',display:'flex',alignItems:'center',justifyContent:'space-between',borderBottom:`1px solid ${C.bd}`,flexShrink:0}}>
          <div style={{display:'flex',gap:8}}>
            <div style={{width:12,height:12,borderRadius:'50%',background:'#ff5f57'}}></div>
            <div style={{width:12,height:12,borderRadius:'50%',background:'#febc2e'}}></div>
            <div style={{width:12,height:12,borderRadius:'50%',background:'#28c840'}}></div>
          </div>
          <span style={{fontSize:12,color:C.dim}}>daily-brief — zsh</span>
          <button onClick={()=>setDark(d=>!d)} style={{background:'none',border:`1px solid ${C.bd}`,color:C.dim,padding:'2px 10px',borderRadius:4,fontSize:11}}>{dark?'light':'dark'}</button>
        </div>
        <div style={{display:'flex',flex:1,overflow:'hidden'}}>
          <div style={{width:138,background:C.sidebar,borderRight:`1px solid ${C.bd}`,flexShrink:0,display:'flex',flexDirection:'column'}}>
            <div style={{padding:'14px 14px 10px',fontSize:9,color:C.dim,letterSpacing:'2px',textTransform:'uppercase',borderBottom:`1px solid ${C.bd}`}}>filter</div>
            <div style={{flex:1,paddingTop:6}}>{catList}</div>
          </div>
          <div style={{flex:1,overflowY:'auto',display:'flex',flexDirection:'column'}}>
            <div style={{padding:'18px 22px 14px',borderBottom:`1px solid ${C.bd}`,flexShrink:0}}>
              <div style={{fontSize:12,marginBottom:10}}><span style={{color:C.accent}}>you@news</span><span style={{color:C.dim}}> ~ $ </span><span>brief --date {META.dateStr} --lang bilingual</span></div>
              <div style={{borderLeft:`3px solid ${C.accent}`,paddingLeft:12}}>
                <div style={{color:C.accent,fontWeight:700,fontSize:15,letterSpacing:'.5px'}}>► DAILY BRIEF</div>
                <div style={{fontSize:11,color:C.dim,marginTop:4}}>{META.date} · {META.total} stories · summarized by {META.model}</div>
              </div>
            </div>
            <div style={{padding:'8px 22px',borderBottom:`1px solid ${C.bd}`,fontSize:11,color:C.dim,flexShrink:0}}>
              <span style={{color:C.accent}}>[{activeCat}]</span>{' '}{shown.length} items
            </div>
            <div style={{flex:1}}>
              {renderItems()}
              {loadMoreBtn}
            </div>
          </div>
          <div style={{width:162,background:C.sidebar,borderLeft:`1px solid ${C.bd}`,flexShrink:0,display:'flex',flexDirection:'column'}}>
            <div style={{padding:'14px 14px 10px',fontSize:9,color:C.dim,letterSpacing:'2px',textTransform:'uppercase',borderBottom:`1px solid ${C.bd}`,flexShrink:0}}>archive</div>
            <div style={{flex:1,overflowY:'auto'}}>{archiveList}</div>
            <div style={{borderTop:`1px solid ${C.bd}`,flexShrink:0}}>{inferredPanel}</div>
            <div style={{padding:'10px 14px 12px',borderTop:`1px solid ${C.bd}`,flexShrink:0,fontSize:10,lineHeight:1.9}}>
              <div style={{color:C.accent}}>✓ done</div>
              <div style={{color:C.dim}}>{META.total} fetched</div>
              <div style={{color:C.dim}}>next ~24h</div>
              <div style={{color:C.dim}}>{META.updateTime}</div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  ReactDOM.createRoot(document.getElementById('root')).render(<App />);
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

# ── Generate HTML ─────────────────────────────────────────────────────────────

def get_archive_dates() -> list[str]:
    if not os.path.isdir("archive"):
        return []
    return sorted(
        [f[:-5] for f in os.listdir("archive") if f.endswith(".html") and len(f) == 15],
        reverse=True,
    )[:14]


def safe_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def apply_cat_limit(items: list[dict]) -> list[dict]:
    groups: dict[str, list] = {}
    for item in items:
        groups.setdefault(item["category"], []).append(item)
    result = []
    for cat in CATEGORY_ORDER:
        result.extend(groups.get(cat, [])[:MAX_PER_CAT])
    return result


def generate_html(items: list[dict]) -> str:
    now = datetime.now(timezone.utc)

    items = apply_cat_limit(items)

    data = [
        {
            "id":        i + 1,
            "cat":       item["category"],
            "tags":      item.get("tags", []),
            "title":     item["title"],
            "titleCN":   item.get("titleCN", ""),
            "source":    item["source"],
            "time":      item["time_ago"],
            "summary":   item["en"],
            "summaryCN": item["zh"],
        }
        for i, item in enumerate(items)
    ]

    meta = {
        "date":       now.strftime("%a %b %d %Y"),
        "dateStr":    now.strftime("%Y-%m-%d"),
        "model":      GEMINI_MODEL,
        "total":      len(data),
        "updateTime": now.strftime("%H:%M UTC"),
    }

    html = HTML_TEMPLATE
    html = html.replace("__DATA_JSON__",    safe_json(data))
    html = html.replace("__META_JSON__",    safe_json(meta))
    html = html.replace("__ARCHIVE_JSON__", safe_json(get_archive_dates()))
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
