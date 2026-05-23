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
