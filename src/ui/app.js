/* 台割工房 — 画面の動き。ファイルには触らず、操作はすべて本体（Python）へ頼む */
'use strict';
(() => {
  const TOKEN = document.querySelector('meta[name="dk-token"]').content;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const pad = (n) => String(n).padStart(3, '0');
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const STATUS = [['todo', '未着手'], ['art', '画像のみ'], ['text', 'セリフ入'], ['done', '完成'], ['fix', '要修正']];
  const SCOLOR = { todo: 'var(--s-todo)', art: 'var(--s-art)', text: 'var(--s-text)', done: 'var(--s-done)', fix: 'var(--s-fix)' };
  const SLABEL = Object.fromEntries(STATUS);
  const FIELDS = ['scene', 'lines', 'memo', 'fix'];

  const S = {
    st: null, rev: 0, pages: [], byId: new Map(), idx: new Map(),
    sel: new Set(), primary: null, anchor: null, lastIdx: 0,
    view: 'board', readMode: 'single',
    filter: 'all', query: '', onlyMatch: false, match: null,
    detailWhich: null, detailSig: '', stripSig: '', layoutSig: '', noticesHtml: '',
    pendingText: new Map(), chain: Promise.resolve(),
    fetching: false, refetch: false, sseDown: 0, zoomLive: 0,
    modals: [], exportBox: null,
  };
  const cards = new Map();

  const thumbURL = (rel, v) => `/thumb?f=${encodeURIComponent(rel)}&v=${encodeURIComponent(v || '')}&t=${encodeURIComponent(TOKEN)}`;
  const imgURL = (rel, v) => `/img?f=${encodeURIComponent(rel)}&v=${encodeURIComponent(v || '')}&t=${encodeURIComponent(TOKEN)}`;

  // ───────────── 本体とのやり取り ─────────────

  async function request(path, opts = {}) {
    const headers = { 'X-Token': TOKEN };
    if (opts.body) headers['Content-Type'] = 'application/json';
    const r = await fetch(path, { ...opts, headers });
    try { return await r.json(); } catch (e) { return { ok: false, error: '応答を読めませんでした' }; }
  }

  function api(op, args = {}, opt = {}) {
    const job = S.chain.then(() => doApi(op, args, opt));
    S.chain = job.catch(() => null);
    return job;
  }

  async function doApi(op, args, opt) {
    let data;
    try {
      data = await request('/api/op', { method: 'POST', body: JSON.stringify({ op, args, state: opt.state !== false }) });
    } catch (e) {
      toast('台割工房の本体とつながりません', 'error');
      throw e;
    }
    if (!data.ok) {
      if (!opt.quiet) toast(data.error || 'うまくいきませんでした', 'error');
      const err = new Error(data.error || 'failed');
      err.handled = true;
      throw err;
    }
    if (data.state) applyState(data.state);
    if (data.select && data.select.length) setSelection(data.select);
    if (data.toast && !opt.noToast) {
      const canUndo = data.label && op !== 'undo' && op !== 'redo';
      toast(data.toast, 'info', canUndo ? { label: '元に戻す', fn: undo } : null);
    }
    return data;
  }

  const run = (op, args, opt) => api(op, args, opt).catch(() => null);

  async function fetchState() {
    if (S.fetching) { S.refetch = true; return; }
    S.fetching = true;
    try {
      const d = await request('/api/state');
      if (d.ok) applyState(d.state);
    } catch (e) { /* つながらないときは見張りに任せる */ }
    S.fetching = false;
    if (S.refetch) { S.refetch = false; fetchState(); }
  }
  let fetchTimer = 0;
  const scheduleFetch = () => { clearTimeout(fetchTimer); fetchTimer = setTimeout(fetchState, 50); };

  function connect() {
    const es = new EventSource('/api/events?t=' + encodeURIComponent(TOKEN));
    es.addEventListener('hello', (e) => {
      S.sseDown = 0; $('#offline').hidden = true;
      const d = JSON.parse(e.data);
      if (d.rev !== S.rev) fetchState();
    });
    es.addEventListener('rev', (e) => { if (JSON.parse(e.data) > S.rev) scheduleFetch(); });
    es.addEventListener('index', (e) => { if (S.st) { S.st.index = JSON.parse(e.data); renderStripInfo(); } });
    es.addEventListener('ident', (e) => { if (S.st) { S.st.ident = JSON.parse(e.data); renderStripInfo(); } });
    es.addEventListener('export', (e) => exportProgress(JSON.parse(e.data)));
    es.onopen = () => { S.sseDown = 0; $('#offline').hidden = true; };
    es.onerror = () => { if (!S.sseDown) { S.sseDown = Date.now(); checkAlive(); } };
  }

  async function checkAlive() {
    await sleep(4000);
    if (!S.sseDown) return;
    try { const d = await request('/api/ping'); if (d.ok) return; } catch (e) { /* 下で知らせる */ }
    $('#offline').hidden = false;
  }

  // ───────────── 状態 ─────────────

  function applyState(st) {
    if (S.st && st.rev < S.rev) return;
    const first = !S.st;
    S.st = st;
    S.rev = st.rev;
    S.pages = st.pages;
    S.byId = new Map(st.pages.map((p) => [p.id, p]));
    S.idx = new Map(st.pages.map((p, i) => [p.id, i]));
    // 入力中の文字は、手元の新しい方を残す
    for (const it of S.pendingText.values()) {
      const p = S.byId.get(it.id);
      if (p) p[it.field] = it.value;
    }
    for (const id of [...S.sel]) if (!S.byId.has(id)) S.sel.delete(id);
    if (S.primary && !S.byId.has(S.primary)) {
      S.primary = null;
      if (!S.sel.size && S.pages.length) {
        const p = S.pages[clamp(S.lastIdx, 0, S.pages.length - 1)];
        S.sel = new Set([p.id]);
        S.anchor = p.id;
      }
      S.primary = [...S.sel].pop() || null;
    }
    if (first && S.pages.length && !S.primary) {
      S.primary = S.pages[0].id; S.sel = new Set([S.primary]); S.anchor = S.primary;
    }
    if (S.primary) S.lastIdx = S.idx.get(S.primary);
    for (const f of ['new', 'missing', 'wait']) if (S.filter === f && !st.counts[f]) S.filter = 'all';
    document.title = st.window_title || '台割工房';
    computeMatches();
    render();
  }

  function selectedIds() { return S.pages.filter((p) => S.sel.has(p.id)).map((p) => p.id); }

  function pageMatches(p) {
    const f = S.filter;
    if (f !== 'all') {
      if (f === 'blank' || f === 'missing') { if (p.kind !== f) return false; }
      else if (f === 'new') { if (!p.new) return false; }
      else if (f === 'wait') { if (!p.wait) return false; }
      else if (p.status !== f) return false;
    }
    const q = S.query.trim().toLowerCase();
    if (q) {
      const hay = [p.raw, p.fin, p.scene, p.lines, p.memo, p.fix, 'p.' + pad(p.n)].join('\n').toLowerCase();
      if (!q.split(/\s+/).every((t) => hay.includes(t))) return false;
    }
    return true;
  }

  function computeMatches() {
    S.match = (S.filter === 'all' && !S.query.trim()) ? null : new Set(S.pages.filter(pageMatches).map((p) => p.id));
  }

  function shownFile(p, which) {
    const w = which || S.st.settings.view;
    const raw = p.raw_ok ? ['raw', p.raw, p.raw_v] : null;
    const fin = p.fin_ok ? ['fin', p.fin, p.fin_v] : null;
    return w === 'raw' ? (raw || fin) : (fin || raw);
  }

  function spreads() {
    const n = S.pages.length, out = [];
    let i = 0;
    if (S.st.settings.cover_single && n) { out.push([0]); i = 1; }
    for (; i < n; i += 2) out.push(i + 1 < n ? [i, i + 1] : [i]);
    return out;
  }
  const spreadStart = (i) => S.st.settings.cover_single ? (i === 0 || i % 2 === 1) : i % 2 === 0;
  function spreadOf(i) { return spreads().find((g) => g.includes(i)) || [i]; }

  function dialogueCount(text) {
    let n = 0;
    for (const ln of (text || '').split('\n')) {
      const m = ln.match(/「(.*?)(」|$)/);
      n += (m ? m[1] : ln).replace(/\s/g, '').length;
    }
    return n;
  }

  // ───────────── 描く ─────────────

  function render() {
    if (!S.st) return;
    renderTop();
    renderStrip();
    renderStripInfo();
    renderNotices();
    renderChips();
    renderToolbar();
    renderBoard();
    renderDetail();
    if (S.view === 'read') renderReader();
  }

  function renderTop() {
    const st = S.st;
    const t = $('#title');
    if (document.activeElement !== t && t.value !== st.title) t.value = st.title;
    t.placeholder = st.folder_name ? `作品タイトル（${st.folder_name}）` : '作品タイトル';
    $('#folder').textContent = st.folder_name;
    $('#folder').title = `${st.folder}\n（クリックでエクスプローラを開く）`;
    const c = st.counts, n = st.pages.length;
    const pct = n ? Math.round((c.done / n) * 100) : 0;
    $('#totals').innerHTML = `<b>${n}</b> ページ　完成 <b>${c.done}</b> <span class="pct">${pct}%</span>`;
    const u = $('#undo'), r = $('#redo');
    u.disabled = !st.undo.n; u.title = st.undo.n ? `元に戻す：${st.undo.label}（Ctrl+Z）` : '元に戻す（Ctrl+Z）';
    r.disabled = !st.redo.n; r.title = st.redo.n ? `やり直し：${st.redo.label}（Ctrl+Y）` : 'やり直し（Ctrl+Y）';
    $$('#tabs button').forEach((b) => b.classList.toggle('on', b.dataset.view === S.view));
  }

  function renderStrip() {
    const el = $('#strip');
    const spread = S.st.settings.spread;
    const sig = S.pages.map((p) => p.status + p.kind[0]).join(',') + '|' + spread + S.st.settings.cover_single;
    if (sig !== S.stripSig) {
      S.stripSig = sig;
      el.innerHTML = S.pages.map((p, i) =>
        `<i data-i="${i}" class="st-${p.status}${p.kind !== 'image' ? ' ' + p.kind : ''}${spread && i > 0 && spreadStart(i) ? ' gap' : ''}"></i>`).join('');
    }
    const ticks = el.children;
    for (let i = 0; i < ticks.length && i < S.pages.length; i++) {
      const p = S.pages[i];
      ticks[i].classList.toggle('sel', S.sel.has(p.id));
      ticks[i].classList.toggle('primary', p.id === S.primary);
      ticks[i].classList.toggle('dim', !!S.match && !S.match.has(p.id));
    }
  }

  function renderStripInfo() {
    const st = S.st, el = $('#strip-info');
    if (!st) return;
    if (st.ident) el.innerHTML = `<span class="busy">名前の変わった絵を照合中 ${st.ident.done}/${st.ident.total}</span>`;
    else if (st.index && st.index.total) el.innerHTML = `<span class="busy">絵を読み込み中 ${st.index.done}/${st.index.total}</span>`;
    else el.textContent = `残り ${st.pages.length - st.counts.done} ・ 要修正 ${st.counts.fix}`;
  }

  function renderNotices() {
    const st = S.st, out = [];
    for (const n of st.notices) {
      if (n.kind === 'ask') {
        out.push(`<div class="notice ask" data-id="${esc(n.id)}"><div class="txt">${esc(n.text)}</div><div class="acts">${
          n.actions.map(([a, l], i) => `<button class="btn small${i === 0 ? ' primary' : ''}" data-act="${esc(a)}">${esc(l)}</button>`).join('')}</div></div>`);
      } else {
        out.push(`<div class="notice ${esc(n.kind)}" data-id="${esc(n.id)}"><div class="txt">${esc(n.text)}</div><button class="x" data-act="dismiss" title="閉じる">×</button></div>`);
      }
    }
    for (const s of st.suggestions) {
      const page = S.byId.get(s.page);
      out.push(`<div class="notice ask" data-sugg="${esc(s.id)}"><img src="${thumbURL(s.cand, s.cand_v)}" alt=""><div class="txt">新しく来た絵「${esc(s.cand)}」は、${
        page ? 'P.' + pad(page.n) : '見つからないページ'}（${esc(s.missing)}・見つからない）の差し替えですか？</div><div class="acts"><button class="btn small primary" data-act="yes">差し替えとして入れる</button><button class="btn small" data-act="no">別のページのままにする</button></div></div>`);
    }
    const html = out.join('');
    if (html !== S.noticesHtml) { S.noticesHtml = html; $('#notices').innerHTML = html; }
  }

  function renderChips() {
    const c = S.st.counts, n = S.pages.length;
    const chips = [['all', 'すべて', n, '']];
    for (const [k, l] of STATUS) chips.push([k, l, c[k], `style="background:${SCOLOR[k]}"`]);
    chips.push(['blank', '空白', c.blank, 'class="blank"']);
    if (c.wait) chips.push(['wait', '待ち受け', c.wait, 'class="wait"']);
    if (c.new) chips.push(['new', '新着', c.new, 'class="new"']);
    if (c.missing) chips.push(['missing', '見つからない', c.missing, 'class="missing"']);
    let html = chips.map(([k, l, cnt, dot]) =>
      `<button class="chip${S.filter === k ? ' on' : ''}" data-f="${k}">${k === 'all' ? '' : `<i ${dot}></i>`}${l} <b>${cnt}</b></button>`).join('');
    if (c.new) html += '<button class="btn small ghost" data-seen-all title="新着の印をすべて消す">新着の印を消す</button>';
    const el = $('#chips');
    if (el.innerHTML !== html) el.innerHTML = html;
  }

  function renderToolbar() {
    const st = S.st;
    let cnt = '';
    if (S.match) {
      const list = S.pages.filter((p) => S.match.has(p.id));
      const pos = list.findIndex((p) => p.id === S.primary);
      cnt = pos >= 0 ? `${pos + 1}/${list.length}` : `${list.length}件`;
    }
    $('#search-count').textContent = cnt;
    $$('#view-seg button').forEach((b) => b.classList.toggle('on', b.dataset.v === st.settings.view));
    $('#spread').checked = st.settings.spread;
    $('#only-match').checked = S.onlyMatch;
    if (!S.zoomLive && document.activeElement !== $('#zoom')) $('#zoom').value = st.settings.thumb_size;
  }

  function cardSig(p) {
    return [p.n, p.kind, p.status, p.raw, p.fin, p.raw_ok, p.fin_ok, p.raw_v, p.fin_v, p.scene,
      p.lines.slice(0, 140), p.fix.slice(0, 80), p.wait, p.new, p.checking, S.st.settings.view].join('\u0001');
  }

  function cardHTML(p) {
    let face = '', pair = '';
    if (p.kind === 'image') {
      const f = shownFile(p);
      pair = `<span class="pair"><b class="${p.raw ? (p.raw_ok ? 'on' : 'lost') : ''}${f[0] === 'raw' ? ' show' : ''}" title="原画${p.raw ? '：' + esc(p.raw) : 'なし'}">原</b><b class="${p.fin ? (p.fin_ok ? 'on' : 'lost') : ''}${f[0] === 'fin' ? ' show' : ''}" title="完成${p.fin ? '：' + esc(p.fin) : 'なし'}">完</b></span>`;
      face = `<img loading="lazy" decoding="async" draggable="false" src="${thumbURL(f[1], f[2])}" alt="">`;
    } else if (p.kind === 'blank') {
      face = p.wait ? '<div class="face"><b>待ち受け</b>次にフォルダへ来た絵が<br>ここに入ります</div>'
        : '<div class="face"><b>空白</b>ここに1ページ入ります</div>';
    } else {
      face = `<div class="face"><b>見つかりません</b><small>${esc(p.raw || p.fin)}</small>戻せば元通りになります</div>`;
    }
    const text = p.lines.split('\n').filter((s) => s.trim()).join(' ／ ');
    const linesHtml = text ? esc(text) : (p.kind === 'blank' ? (p.memo.trim() ? esc(p.memo.split('\n')[0]) : '') : 'セリフ未記入');
    return `<div class="thumb">${face}<div class="badges"><span class="pno">P.${pad(p.n)}</span>${pair}</div>${
      p.scene ? `<div class="scene-tag">${esc(p.scene)}</div>` : ''}${p.new ? '<span class="newdot">新</span>' : ''}${
      p.checking ? '<div class="checking">照合中…</div>' : ''}</div><div class="meta"><div class="lines${text ? '' : ' empty'}">${linesHtml}</div>${
      p.fix.trim() ? `<div class="fixnote">✎ ${esc(p.fix.split('\n')[0])}</div>` : ''}</div><i class="ribbon"></i>`;
  }

  function renderBoard() {
    const board = $('#board');
    const st = S.st;
    board.style.setProperty('--tw', (S.zoomLive || st.settings.thumb_size) + 'px');
    const spread = st.settings.spread;
    board.classList.toggle('spread', spread);
    board.classList.toggle('only-match', S.onlyMatch && !!S.match);
    const keep = new Set(), els = [];
    for (const p of S.pages) {
      keep.add(p.id);
      let c = cards.get(p.id);
      if (!c) {
        const el = document.createElement('div');
        el.dataset.id = p.id;
        el.draggable = true;
        c = { el, sig: null };
        cards.set(p.id, c);
      }
      const sig = cardSig(p);
      if (c.sig !== sig) {
        c.el.innerHTML = cardHTML(p);
        c.el.className = `card ${p.kind} st-${p.status}${p.wait ? ' wait' : ''}`;
        c.sig = sig;
      }
      c.el.classList.toggle('sel', S.sel.has(p.id));
      c.el.classList.toggle('primary', p.id === S.primary);
      c.el.classList.toggle('dim', !!S.match && !S.match.has(p.id));
      els.push(c.el);
    }
    for (const id of [...cards.keys()]) if (!keep.has(id)) cards.delete(id);
    const layout = (spread ? 'S' + st.settings.cover_single : 'G') + S.pages.map((p) => p.id).join(',');
    if (layout !== S.layoutSig) {
      S.layoutSig = layout;
      if (!S.pages.length) {
        board.innerHTML = '<div class="empty-state"><b>このフォルダには、まだ絵がありません</b><br>画像を入れると、ここに並びます。空白を入れて先に台割を組むこともできます（I キー）。</div>';
        return;
      }
      if (spread) {
        board.replaceChildren(...spreads().map((g) => {
          const w = document.createElement('div');
          w.className = 'spr' + (g.length === 1 ? ' single' : '');
          g.forEach((i) => w.appendChild(els[i]));
          return w;
        }));
      } else {
        board.replaceChildren(...els);
      }
    }
  }

  function renderSelection() {
    for (const [id, c] of cards) {
      c.el.classList.toggle('sel', S.sel.has(id));
      c.el.classList.toggle('primary', id === S.primary);
    }
    renderStrip();
    renderToolbar();
    renderDetail();
    if (S.view === 'read') renderReader();
  }

  function kindLabel(p) {
    if (p.kind === 'blank') return p.wait ? '空白（待ち受け）' : '空白';
    if (p.kind === 'missing') return '絵が見つかりません';
    return p.raw_ok && p.fin_ok ? '原画と完成' : (p.fin_ok ? '完成のみ' : '原画のみ');
  }

  function statusButtons(cur, mixed) {
    return STATUS.map(([k, l], i) => `<button data-status="${k}" class="${cur === k ? 'on' : (mixed && mixed.has(k) ? 'mixed' : '')}" style="--c:${SCOLOR[k]}" title="${l}（${i + 1}）">${l}</button>`).join('');
  }

  function fileRow(kind, rel, ok, extra = '') {
    return `<div class="file${ok ? '' : ' lost'}"><span class="k">${kind}</span><span class="n" title="${esc(rel)}">${esc(rel)}</span><span class="acts">${
      ok ? `<button data-reveal="${esc(rel)}" title="エクスプローラで表示">表示</button><button data-ext="${esc(rel)}" title="外部アプリで開く">開く</button>` : '<span style="color:var(--danger);font-size:11px">なし</span>'}${extra}</span></div>`;
  }

  function detailHTML(p) {
    const n = S.pages.length;
    let h = `<div class="d-head"><div class="d-pno">P.${pad(p.n)}<small>/ ${n}</small></div><div class="d-kind">${kindLabel(p)}</div><div class="d-nav"><button data-nav="-1" title="前のページ（PageUp）">‹</button><button data-nav="1" title="次のページ（PageDown）">›</button></div></div>`;
    h += `<div class="statusbar">${statusButtons(p.status)}</div>`;
    if (p.kind === 'image') {
      const which = (S.detailWhich === 'raw' && p.raw_ok) || (S.detailWhich === 'fin' && p.fin_ok) ? S.detailWhich : null;
      const f = shownFile(p, which);
      h += `<div class="d-preview" data-act="read" title="クリックで原寸（Enter）"><img src="${thumbURL(f[1], f[2])}" alt=""></div>`;
      if (p.raw_ok && p.fin_ok) {
        h += `<div class="d-preview-bar"><div class="seg"><button data-which="raw" class="${f[0] === 'raw' ? 'on' : ''}">原画</button><button data-which="fin" class="${f[0] === 'fin' ? 'on' : ''}">完成</button></div></div>`;
      }
    } else if (p.kind === 'blank') {
      h += `<div class="d-preview" style="cursor:default"><div class="face"><b>${p.wait ? '待ち受け' : '空白'}</b>${p.wait ? '次にフォルダへ来た絵が<br>ここに入ります' : 'ここに1ページ入ります'}</div></div>`;
      h += `<div class="d-actions"><button class="btn small" data-act="fill">ここに絵を入れる…</button><button class="btn small" data-act="wait">${p.wait ? '待ち受けをやめる' : '待ち受けにする'}</button><button class="btn small ghost" data-act="delete">空白を消す</button></div>`;
    } else {
      h += `<div class="d-preview" style="cursor:default"><div class="face"><b>見つかりません</b><small>${esc(p.raw || p.fin)}</small></div></div>`;
      h += '<p class="d-note">絵のファイルがフォルダにありません。消したり外へ移したりしたなら、戻せば元通りになります。セリフとメモはそのまま残っています。</p>';
      h += `<div class="d-actions"><button class="btn small" data-act="fill">別の絵を入れる…</button><button class="btn small" data-act="wait">${p.wait ? '待ち受けをやめる' : '待ち受けにする'}</button><button class="btn small ghost" data-act="delete">このページを消す</button></div>`;
    }
    if (p.raw || p.fin || p.old.length) {
      h += '<div class="files">';
      if (p.raw) h += fileRow('原画', p.raw, p.raw_ok);
      if (p.fin) h += fileRow('完成', p.fin, p.fin_ok);
      for (const o of p.old) h += fileRow('旧版', o.rel, o.ok, o.ok ? `<button data-useold="${esc(o.rel)}" title="この旧版を完成に戻す">完成に</button>` : '');
      h += '</div>';
    }
    const warn = S.st.settings.chars_warn;
    h += `<div class="field"><label>シーン名</label><input data-field="scene" placeholder="例）屋上・告白" maxlength="200" spellcheck="false"></div>`;
    h += `<div class="field"><label>セリフ<span class="hint">1行に1台詞（話者「台詞」）</span><span class="cnt" id="cnt-lines" data-warn="${warn}"></span></label><textarea data-field="lines" class="lines" placeholder="例）サキ「あのさ」" spellcheck="false"></textarea></div>`;
    h += '<div class="field"><label>メモ<span class="hint">ト書き・演出・自分用の覚え書き</span></label><textarea data-field="memo" class="memo" spellcheck="false"></textarea></div>';
    h += `<div class="field fixf${p.status === 'fix' ? ' hot' : ''}"><label>修正指示</label><textarea data-field="fix" class="fix" placeholder="何を直すか（要修正のページで目立ちます）" spellcheck="false"></textarea></div>`;
    return h;
  }

  function multiHTML(ids) {
    const ps = ids.map((id) => S.byId.get(id));
    const st = new Set(ps.map((p) => p.status));
    const cur = st.size === 1 ? ps[0].status : null;
    const pairable = ps.length === 2 && ps.every((p) => p.kind === 'image' && (!!p.raw + !!p.fin) === 1);
    let h = `<div class="d-head"><div class="d-pno">${ids.length}<small>ページを選択中</small></div></div>`;
    h += `<div class="statusbar">${statusButtons(cur, cur ? null : st)}</div>`;
    h += '<div class="d-actions"><button class="btn small" data-move="top">先頭へ</button><button class="btn small" data-move="end">末尾へ</button><button class="btn small" data-move="up">1つ前へ</button><button class="btn small" data-move="down">1つ後ろへ</button><button class="btn small" data-move="number">番号を指定…</button></div>';
    h += `<div class="d-actions"><button class="btn small" data-act="blank-before">前に空白</button><button class="btn small" data-act="blank-after">後ろに空白</button>${pairable ? '<button class="btn small" data-act="pair">2枚を原画と完成の組にする</button>' : ''}</div>`;
    h += '<div class="d-actions"><button class="btn small warn" data-act="exclude" title="台割から外す。ファイルはそのまま（X）">除外</button><button class="btn small danger" data-act="quarantine" title="「未使用」フォルダへ移す（Shift+X）">隔離（ファイル移動）</button></div>';
    h += `<p class="d-note">Shift+クリックで範囲、Ctrl+クリックで1枚ずつ。ドラッグでまとめて動かせます。</p><div class="d-multi-list">${ps.map((p) => 'P.' + pad(p.n)).join('　')}</div>`;
    return h;
  }

  function renderDetail() {
    const el = $('#detail');
    const ids = selectedIds();
    if (!ids.length) {
      if (S.detailSig !== 'none') { S.detailSig = 'none'; el.innerHTML = '<div class="d-empty">ページを選ぶと、ここで<br>進行・セリフ・メモを編集できます</div>'; }
      return;
    }
    if (ids.length > 1) {
      const sig = 'multi|' + ids.join(',') + '|' + ids.map((id) => S.byId.get(id).status + S.byId.get(id).n).join(',');
      if (S.detailSig !== sig) { S.detailSig = sig; el.innerHTML = multiHTML(ids); }
      return;
    }
    const p = S.byId.get(S.primary) || S.byId.get(ids[0]);
    const sig = [p.id, p.n, S.pages.length, p.kind, p.raw, p.fin, p.raw_ok, p.fin_ok, p.raw_v, p.fin_v,
      JSON.stringify(p.old), p.status, p.wait, S.detailWhich, S.st.settings.view, S.st.settings.chars_warn].join('|');
    if (S.detailSig !== sig) {
      const act = document.activeElement;
      const keep = act && el.contains(act) && act.dataset.field && S.detailPage === p.id
        ? { f: act.dataset.field, s: act.selectionStart, e: act.selectionEnd } : null;
      S.detailSig = sig;
      S.detailPage = p.id;
      el.innerHTML = detailHTML(p);
      for (const f of FIELDS) { const inp = el.querySelector(`[data-field="${f}"]`); if (inp) inp.value = p[f]; }
      if (keep) {
        const inp = el.querySelector(`[data-field="${keep.f}"]`);
        if (inp) { inp.focus(); try { inp.setSelectionRange(keep.s, keep.e); } catch (e) { /* 入力欄の種類による */ } }
      }
    } else {
      for (const f of FIELDS) {
        const inp = el.querySelector(`[data-field="${f}"]`);
        if (inp && document.activeElement !== inp && !S.pendingText.has(p.id + '|' + f) && inp.value !== p[f]) inp.value = p[f];
      }
    }
    $$('textarea', el).forEach(autoGrow);
    updateCount();
  }

  function autoGrow(t) {
    t.style.height = 'auto';
    t.style.height = Math.min(t.scrollHeight + 2, 420) + 'px';
  }

  function updateCount() {
    const c = $('#cnt-lines'), t = $('#detail [data-field="lines"]');
    if (!c || !t) return;
    const n = dialogueCount(t.value), warn = +c.dataset.warn || 120;
    c.textContent = `${n}字`;
    c.classList.toggle('over', n > warn);
    c.title = `セリフの字数（目安 ${warn}字）`;
  }

  // ───────────── 選ぶ ─────────────

  function setSelection(ids) {
    const ok = ids.filter((id) => S.byId.has(id));
    if (!ok.length) return;
    S.sel = new Set(ok);
    S.primary = ok[ok.length - 1];
    S.anchor = ok[0];
    afterSelect({});
  }

  function selectOnly(id, opt = {}) {
    if (!S.byId.has(id)) return;
    flushAllText();
    S.sel = new Set([id]);
    S.primary = id;
    S.anchor = id;
    afterSelect(opt);
  }

  function toggleSel(id) {
    flushAllText();
    if (S.sel.has(id) && S.sel.size > 1) {
      S.sel.delete(id);
      if (S.primary === id) S.primary = selectedIds().pop();
    } else {
      S.sel.add(id);
      S.primary = id;
    }
    S.anchor = id;
    afterSelect({ noScroll: true });
  }

  function rangeSel(id, opt = {}) {
    flushAllText();
    const a = S.idx.get(S.anchor ?? id) ?? 0, b = S.idx.get(id);
    const [lo, hi] = a < b ? [a, b] : [b, a];
    S.sel = new Set(S.pages.slice(lo, hi + 1).map((p) => p.id));
    S.primary = id;
    afterSelect(opt);
  }

  function afterSelect(opt) {
    S.detailWhich = null;
    const p = S.byId.get(S.primary);
    if (p) S.lastIdx = S.idx.get(p.id);
    if (p && p.new && !opt.quiet) {
      p.new = false;
      const c = cards.get(p.id);
      if (c) c.sig = null;
      run('seen', { ids: [p.id] }, { state: false });
      renderBoard();
    }
    renderSelection();
    if (!opt.noScroll) scrollToPrimary();
  }

  function scrollToPrimary() {
    const c = cards.get(S.primary);
    if (c && S.view === 'board') c.el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  function step(delta, opt = {}) {
    if (!S.pages.length) return;
    let i = S.idx.get(S.primary);
    if (i == null) i = delta > 0 ? -1 : S.pages.length;
    let j;
    if (S.view === 'read' && S.readMode === 'spread') {
      const groups = spreads();
      const gi = groups.findIndex((g) => g.includes(i));
      const g = groups[clamp(gi + delta, 0, groups.length - 1)];
      j = g[0];
    } else {
      j = clamp(i + delta, 0, S.pages.length - 1);
      if (S.onlyMatch && S.match) {
        while (j >= 0 && j < S.pages.length && !S.match.has(S.pages[j].id)) j += Math.sign(delta);
        if (j < 0 || j >= S.pages.length) return;
      }
    }
    const field = opt.keepField ? document.activeElement?.dataset?.field : null;
    if (opt.extend) rangeSel(S.pages[j].id); else selectOnly(S.pages[j].id);
    if (field) {
      const inp = $(`#detail [data-field="${field}"]`);
      if (inp) { inp.focus(); const n = inp.value.length; try { inp.setSelectionRange(n, n); } catch (e) { /* なし */ } }
    }
  }

  function rowStep(dir, extend) {
    const c = cards.get(S.primary);
    if (!c) return step(dir);
    const r = c.el.getBoundingClientRect();
    const cx = r.left + r.width / 2;
    const i0 = S.idx.get(S.primary);
    const list = dir > 0 ? S.pages.slice(i0 + 1) : S.pages.slice(0, i0).reverse();
    let best = null, rowTop = null, bestDx = Infinity;
    for (const p of list) {
      const el = cards.get(p.id)?.el;
      if (!el || !el.offsetParent) continue;
      const rr = el.getBoundingClientRect();
      if (Math.abs(rr.top - r.top) < 4) continue;
      if (rowTop === null) rowTop = rr.top;
      if (Math.abs(rr.top - rowTop) > 4) break;
      const dx = Math.abs(rr.left + rr.width / 2 - cx);
      if (dx < bestDx) { bestDx = dx; best = p; }
    }
    if (best) { if (extend) rangeSel(best.id); else selectOnly(best.id); }
  }

  function rowSize() {
    const c = cards.get(S.primary);
    if (!c) return 1;
    const top = c.el.getBoundingClientRect().top;
    let n = 0;
    for (const p of S.pages) {
      const el = cards.get(p.id)?.el;
      if (el && Math.abs(el.getBoundingClientRect().top - top) < 4) n++;
    }
    return Math.max(1, n);
  }

  function gotoMatch(dir) {
    if (!S.match || !S.match.size) return;
    const list = S.pages.filter((p) => S.match.has(p.id));
    const cur = S.idx.get(S.primary) ?? -1;
    const target = dir > 0 ? (list.find((p) => p.n - 1 > cur) || list[0])
      : ([...list].reverse().find((p) => p.n - 1 < cur) || list[list.length - 1]);
    selectOnly(target.id);
  }

  function filterChanged() {
    computeMatches();
    if (S.match && S.match.size && !S.match.has(S.primary)) gotoMatch(1);
    renderStrip();
    renderChips();
    renderToolbar();
    renderBoard();
  }

  // ───────────── 文字の入力 ─────────────

  function queueText(id, field, value) {
    const k = id + '|' + field;
    const prev = S.pendingText.get(k);
    if (prev) clearTimeout(prev.t);
    const p = S.byId.get(id);
    if (p) p[field] = value;
    S.pendingText.set(k, { id, field, value, t: setTimeout(() => flushText(k), 320) });
  }

  function flushText(k) {
    const it = S.pendingText.get(k);
    if (!it) return null;
    clearTimeout(it.t);
    S.pendingText.delete(k);
    return run('text', { id: it.id, field: it.field, value: it.value });
  }

  function flushAllText() { for (const k of [...S.pendingText.keys()]) flushText(k); }

  // ───────────── 操作 ─────────────

  function undo() { flushAllText(); run('undo'); }
  function redo() { flushAllText(); run('redo'); }
  function setStatus(st) { const ids = selectedIds(); if (ids.length) run('status', { ids, status: st }); }
  function move(how, extra = {}) { const ids = selectedIds(); if (ids.length) run('move', { ids, how, ...extra }); }

  async function moveToNumber() {
    const ids = selectedIds();
    if (!ids.length) return;
    const v = await promptBox(`${ids.length}ページを何ページ目へ動かしますか？（1〜${S.pages.length}）`,
      { value: String((S.idx.get(ids[0]) ?? 0) + 1), type: 'number' });
    if (v == null || v === '') return;
    move('number', { n: parseInt(v, 10) });
  }

  function insertBlank(where, wait = false) {
    flushAllText();
    run('blank', { ids: selectedIds(), where, wait });
  }

  async function excludeSel() {
    const ids = selectedIds();
    if (!ids.length) return;
    if (ids.length >= 10 && !(await confirmBox(`${ids.length}ページを除外します。ファイルはフォルダに残ります。`, { ok: '除外する', warn: true }))) return;
    run('exclude', { ids });
  }

  async function quarantineSel() {
    const ids = selectedIds();
    if (!ids.length) return;
    const q = S.st.settings.quarantine_dir;
    if (ids.length >= 10 && !(await confirmBox(`${ids.length}ページの絵を「${q}」フォルダへ移します。元に戻す（Ctrl+Z）で戻せます。`, { ok: '隔離する', danger: true }))) return;
    run('quarantine', { ids });
  }

  function deletePages() {
    const ids = selectedIds().filter((id) => S.byId.get(id).kind !== 'image');
    if (!ids.length) { toast('消せるのは絵の無いページ（空白）だけです。絵のページは「除外」か「隔離」をお使いください', 'error'); return; }
    run('delete', { ids: selectedIds() });
  }

  function toggleWait() {
    const ps = selectedIds().map((id) => S.byId.get(id)).filter((p) => p.kind !== 'image');
    if (!ps.length) { toast('待ち受けにできるのは、絵の無いページ（空白）だけです', 'error'); return; }
    run('wait', { ids: ps.map((p) => p.id), on: !ps.every((p) => p.wait) });
  }

  function setView(v) {
    if (S.st.settings.view !== v) run('settings', { patch: { view: v } });
  }

  function toggleView() { setView(S.st.settings.view === 'fin' ? 'raw' : 'fin'); }

  function openReader() {
    if (!S.pages.length) return;
    flushAllText();
    if (!S.primary) selectOnly(S.pages[0].id);
    if (S.sel.size > 1) selectOnly(S.primary);
    if (S.readMode !== 'compare') S.readMode = S.st.settings.spread ? 'spread' : 'single';
    S.view = 'read';
    $('#reader').hidden = false;
    renderTop();
    renderReader();
  }

  function closeReader() {
    S.view = 'board';
    $('#reader').hidden = true;
    renderTop();
    scrollToPrimary();
  }

  // ───────────── 読む ─────────────

  function paneHTML(p, which, cur, side, cap) {
    if (!p) return '';
    if (p.kind !== 'image') {
      const t = p.kind === 'blank' ? (p.wait ? '<b>待ち受け</b>' : '<b>空白</b>ここに1ページ入ります') : `<b>見つかりません</b><small>${esc(p.raw || p.fin)}</small>`;
      return `<div class="r-pane ${side || ''}${cur ? ' cur' : ''}" data-id="${p.id}"><div class="face">${t}</div>${cap ? `<span class="cap">${cap}</span>` : ''}</div>`;
    }
    const f = shownFile(p, which);
    return `<div class="r-pane ${side || ''}${cur ? ' cur' : ''}" data-id="${p.id}"><img src="${imgURL(f[1], f[2])}" alt="" draggable="false">${cap ? `<span class="cap">${cap}</span>` : ''}</div>`;
  }

  function renderReader() {
    const p = S.byId.get(S.primary);
    if (!p) { closeReader(); return; }
    const i = S.idx.get(p.id);
    const which = S.st.settings.view;
    const stage = $('#r-stage');
    const mode = S.readMode;
    stage.className = 'r-stage ' + mode;
    let html = '';
    const rtl = mode === 'spread';
    html += `<button class="r-nav prev" data-step="${rtl ? 1 : -1}" title="${rtl ? '次へ（←）' : '前へ（←）'}">‹</button>`;
    html += `<button class="r-nav next" data-step="${rtl ? -1 : 1}" title="${rtl ? '前へ（→）' : '次へ（→）'}">›</button>`;
    if (mode === 'spread') {
      const g = spreadOf(i);
      const items = g.map((k) => S.pages[k]);
      const panes = g.length === 2
        ? paneHTML(items[1], which, items[1].id === p.id, 'left') + paneHTML(items[0], which, items[0].id === p.id, 'right')
        : paneHTML(items[0], which, true, '');
      html += panes;
    } else if (mode === 'compare' && p.kind === 'image') {
      html += paneHTML(p, 'raw', false, '', p.raw_ok ? '原画' : '原画なし') + paneHTML(p, 'fin', false, '', p.fin_ok ? '完成' : '完成なし');
    } else {
      html += paneHTML(p, which, false, '');
    }
    stage.innerHTML = html;
    $('#r-pno').innerHTML = `P.${pad(p.n)} <small>/ ${S.pages.length}</small>`;
    const f = p.kind === 'image' ? shownFile(p, which) : null;
    $('#r-name').textContent = f ? f[1] : (p.kind === 'blank' ? '空白' : (p.raw || p.fin || ''));
    $$('#r-which button').forEach((b) => {
      b.classList.toggle('on', !!f && b.dataset.v === f[0]);
      b.disabled = b.dataset.v === 'raw' ? !p.raw_ok : !p.fin_ok;
    });
    $$('#r-mode button').forEach((b) => b.classList.toggle('on', b.dataset.m === mode));
    $('#r-status').innerHTML = statusButtons(p.status);
    $('#r-hint').innerHTML = mode === 'spread'
      ? '見開き（右綴じ・右のページが先）　<kbd>←</kbd> 次　<kbd>→</kbd> 前　<kbd>1</kbd>〜<kbd>5</kbd> 進行　<kbd>X</kbd> 除外　<kbd>Shift</kbd>+<kbd>X</kbd> 隔離　<kbd>V</kbd> 原画/完成　<kbd>Esc</kbd> 台割へ'
      : '<kbd>→</kbd>／<kbd>PageDown</kbd> 次　<kbd>←</kbd>／<kbd>PageUp</kbd> 前　<kbd>1</kbd>〜<kbd>5</kbd> 進行　<kbd>X</kbd> 除外　<kbd>Shift</kbd>+<kbd>X</kbd> 隔離　<kbd>V</kbd> 原画/完成　<kbd>Esc</kbd> 台割へ';
    for (const k of [i - 2, i - 1, i + 1, i + 2, i + 3]) {
      const q = S.pages[k];
      if (q && q.kind === 'image') { const ff = shownFile(q, which); const im = new Image(); im.src = imgURL(ff[1], ff[2]); }
    }
  }

  // ───────────── 右クリック ─────────────

  let subMenu = null;

  function closeMenu() {
    $('#menu').hidden = true;
    if (subMenu) { subMenu.remove(); subMenu = null; }
  }

  function menuItems() {
    const ids = selectedIds();
    const ps = ids.map((id) => S.byId.get(id));
    const single = ps.length === 1 ? ps[0] : null;
    const spread = S.st.settings.spread;
    const fileless = ps.filter((p) => p.kind !== 'image');
    const q = S.st.settings.quarantine_dir;
    const items = [{ status: true }];
    items.push({ label: '動かす', sub: [
      { label: '先頭へ', act: () => move('top') },
      { label: '末尾へ', act: () => move('end') },
      { label: '1つ前へ', key: spread ? 'Ctrl+→' : 'Ctrl+←', act: () => move('up') },
      { label: '1つ後ろへ', key: spread ? 'Ctrl+←' : 'Ctrl+→', act: () => move('down') },
      { sep: true },
      { label: '番号を指定…', act: moveToNumber },
    ] });
    items.push({ label: '空白', sub: [
      { label: '前に挿入', key: 'Shift+I', act: () => insertBlank('before') },
      { label: '後ろに挿入', key: 'I', act: () => insertBlank('after') },
      { label: '後ろに待ち受けの空白を挿入', act: () => insertBlank('after', true) },
      { sep: true },
      { label: fileless.length && fileless.every((p) => p.wait) ? '待ち受けをやめる' : '待ち受けにする', disabled: !fileless.length || fileless.length !== ps.length, note: '空白だけ', act: toggleWait },
      { label: 'ここに絵を入れる…', disabled: !(single && single.kind !== 'image'), act: () => openPicker(single.id) },
      { label: '空白を消す', key: 'Del', disabled: !fileless.length, act: deletePages },
    ] });
    items.push({ label: '外す', sub: [
      { label: '除外（その場に残す）', cls: 'warn', key: 'X', note: 'ファイルはそのまま', act: excludeSel },
      { label: '別フォルダへ隔離', cls: 'danger', key: 'Shift+X', note: `「${q}」へファイル移動`, act: quarantineSel },
      { sep: true },
      { label: '原画だけ外す（別ページへ）', disabled: !(single && single.raw && single.fin), act: () => run('split', { id: single.id, role: 'raw' }) },
      { label: '完成だけ外す（別ページへ）', disabled: !(single && single.raw && single.fin), act: () => run('split', { id: single.id, role: 'fin' }) },
    ] });
    const f = single && single.kind === 'image' ? shownFile(single) : null;
    items.push({ label: '開く', sub: [
      { label: '原寸で見る', key: 'Enter', disabled: !single, act: openReader },
      { label: '外部アプリで開く', disabled: !f, act: () => run('external', { rel: f[1] }, { state: false }) },
      { label: 'フォルダで表示', disabled: !f, act: () => run('reveal', { rel: f[1] }, { state: false }) },
    ] });
    if (ps.length === 2 && ps.every((p) => p.kind === 'image' && (!!p.raw + !!p.fin) === 1)) {
      items.push({ sep: true });
      items.push({ label: '2枚を原画と完成の組にする', act: () => run('pair', { ids }) });
    }
    return { items, ps };
  }

  function buildMenu(el, items, ps) {
    el.innerHTML = '';
    for (const it of items) {
      if (it.sep) { el.insertAdjacentHTML('beforeend', '<div class="msep"></div>'); continue; }
      if (it.status) {
        const cur = new Set(ps.map((p) => p.status));
        const row = document.createElement('div');
        row.className = 'm-status';
        row.innerHTML = STATUS.map(([k, l], i) => `<button data-st="${k}" class="${cur.size === 1 && cur.has(k) ? 'on' : ''}" style="--c:${SCOLOR[k]}" title="${l}（${i + 1}）">${l}</button>`).join('');
        row.addEventListener('click', (e) => { const b = e.target.closest('[data-st]'); if (b) { closeMenu(); setStatus(b.dataset.st); } });
        el.appendChild(row);
        continue;
      }
      const b = document.createElement('button');
      b.className = 'mi' + (it.cls ? ' ' + it.cls : '');
      b.disabled = !!it.disabled;
      b.innerHTML = `<span>${esc(it.label)}${it.note ? `<span class="sub-note">${esc(it.note)}</span>` : ''}</span>${it.sub ? '<span class="arrow">▸</span>' : (it.key ? `<span class="k">${esc(it.key)}</span>` : '')}`;
      if (it.sub) {
        const open = () => openSub(b, it.sub, ps);
        b.addEventListener('mouseenter', open);
        b.addEventListener('click', open);
      } else {
        b.addEventListener('mouseenter', () => { if (el.id === 'menu' && subMenu) { subMenu.remove(); subMenu = null; $$('#menu .mi.open').forEach((x) => x.classList.remove('open')); } });
        b.addEventListener('click', () => { closeMenu(); it.act(); });
      }
      el.appendChild(b);
    }
  }

  function openSub(btn, sub, ps) {
    $$('#menu .mi.open').forEach((x) => x.classList.remove('open'));
    btn.classList.add('open');
    if (subMenu) subMenu.remove();
    subMenu = document.createElement('div');
    subMenu.className = 'menu sub';
    buildMenu(subMenu, sub, ps);
    document.body.appendChild(subMenu);
    const r = btn.getBoundingClientRect();
    const w = subMenu.offsetWidth, h = subMenu.offsetHeight;
    let x = r.right + 4, y = r.top - 6;
    if (x + w > innerWidth - 8) x = r.left - w - 4;
    if (y + h > innerHeight - 8) y = innerHeight - h - 8;
    subMenu.style.left = Math.max(8, x) + 'px';
    subMenu.style.top = Math.max(8, y) + 'px';
  }

  function openMenu(x, y) {
    const { items, ps } = menuItems();
    if (!ps.length) return;
    const el = $('#menu');
    buildMenu(el, items, ps);
    el.hidden = false;
    const w = el.offsetWidth, h = el.offsetHeight;
    el.style.left = Math.max(8, Math.min(x, innerWidth - w - 8)) + 'px';
    el.style.top = Math.max(8, Math.min(y, innerHeight - h - 8)) + 'px';
  }

  // ───────────── 窓（モーダル） ─────────────

  function modal({ title, cls = '', foot = true, onClose }) {
    const back = document.createElement('div');
    back.className = 'modal-back';
    back.innerHTML = `<div class="modal ${cls}"><div class="m-head"><h2>${esc(title)}</h2><button class="x" title="閉じる（Esc）">×</button></div><div class="m-body"></div>${foot ? '<div class="m-foot"></div>' : ''}</div>`;
    $('#modal-root').appendChild(back);
    const m = { back, body: $('.m-body', back), foot: $('.m-foot', back), head: $('.m-head', back) };
    m.close = (v) => {
      if (!back.isConnected) return;
      back.remove();
      S.modals = S.modals.filter((x) => x !== m);
      if (onClose) onClose(v);
    };
    $('.x', back).addEventListener('click', () => m.close(null));
    back.addEventListener('mousedown', (e) => { if (e.target === back) m.close(null); });
    S.modals.push(m);
    closeMenu();
    return m;
  }

  function confirmBox(text, opt = {}) {
    return new Promise((resolve) => {
      const m = modal({ title: opt.title || '確認', onClose: (v) => resolve(!!v) });
      m.body.innerHTML = `<div class="msg-text">${esc(text)}</div>`;
      m.foot.innerHTML = `<button class="btn" data-no>やめる</button><button class="btn ${opt.danger ? 'danger' : opt.warn ? 'warn' : 'primary'}" data-ok>${esc(opt.ok || 'OK')}</button>`;
      $('[data-no]', m.foot).onclick = () => m.close(false);
      $('[data-ok]', m.foot).onclick = () => m.close(true);
      m.enter = () => m.close(true);
      setTimeout(() => $('[data-ok]', m.foot).focus(), 0);
    });
  }

  function promptBox(text, opt = {}) {
    return new Promise((resolve) => {
      const m = modal({ title: opt.title || '入力', onClose: (v) => resolve(v ?? null) });
      m.body.innerHTML = `<div class="msg-text" style="margin-bottom:10px">${esc(text)}</div><input style="width:100%" type="${opt.type || 'text'}" ${opt.type === 'number' ? `min="1" max="${S.pages.length}"` : ''}>`;
      const inp = $('input', m.body);
      inp.value = opt.value || '';
      m.foot.innerHTML = '<button class="btn" data-no>やめる</button><button class="btn primary" data-ok>OK</button>';
      $('[data-no]', m.foot).onclick = () => m.close(null);
      $('[data-ok]', m.foot).onclick = () => m.close(inp.value);
      m.enter = () => m.close(inp.value);
      setTimeout(() => { inp.focus(); inp.select(); }, 0);
    });
  }

  function openPicker(blankId) {
    const b = S.byId.get(blankId);
    if (!b) return;
    const m = modal({ title: `P.${pad(b.n)} に入れる絵を選ぶ`, cls: 'wide', foot: true });
    const cands = S.pages.filter((p) => p.kind === 'image' && p.id !== blankId)
      .sort((a, c) => (c.new - a.new) || (a.n - c.n));
    const ex = S.st.excluded.filter((e) => e.ok.some(Boolean));
    m.body.innerHTML = `<div style="display:flex;gap:10px;align-items:center;margin-bottom:12px"><input id="pk-q" placeholder="名前・セリフで絞り込む" style="flex:1"><span style="color:var(--txt-faint);font-size:12px">新着が先頭。選んだ絵のページはここへ移ります</span></div><div class="tile-grid" id="pk-grid"></div>`;
    const grid = $('#pk-grid', m.body);
    const draw = () => {
      const q = $('#pk-q', m.body).value.trim().toLowerCase();
      const ok = (s) => !q || s.toLowerCase().includes(q);
      let html = '';
      for (const p of cands) {
        const f = shownFile(p);
        if (!ok([p.raw, p.fin, p.lines, p.scene].join(' '))) continue;
        html += `<button class="tile" data-src="${p.id}"><div class="tp"><img loading="lazy" src="${thumbURL(f[1], f[2])}" alt=""></div><div class="tc">${p.new ? '<span class="tag new">新着</span>' : ''}<b>P.${pad(p.n)}</b><span class="fn">${esc(f[1])}</span></div></button>`;
      }
      for (const e of ex) {
        const k = e.ok.findIndex(Boolean);
        if (!ok(e.files.join(' ') + ' ' + e.text)) continue;
        html += `<button class="tile" data-ex="${e.id}"><div class="tp"><img loading="lazy" src="${thumbURL(e.files[k], e.v[k])}" alt=""></div><div class="tc"><span class="tag">除外中</span><span class="fn">${esc(e.files[k])}</span></div></button>`;
      }
      grid.innerHTML = html || '<div class="list-empty">入れられる絵がありません</div>';
    };
    draw();
    $('#pk-q', m.body).addEventListener('input', draw);
    grid.addEventListener('click', (e) => {
      const t = e.target.closest('.tile');
      if (!t) return;
      m.close();
      if (t.dataset.src) run('fill', { blank: blankId, src: t.dataset.src });
      else run('fill', { blank: blankId, excluded: t.dataset.ex });
    });
    m.foot.innerHTML = '<span class="left">フォルダの外の絵は、先にエクスプローラで画像フォルダへコピーしてください（待ち受けにしておけば自動で入ります）</span><button class="btn" data-no>閉じる</button>';
    $('[data-no]', m.foot).onclick = () => m.close();
    setTimeout(() => $('#pk-q', m.body).focus(), 0);
  }

  function openHelp() {
    const m = modal({ title: 'キー操作', cls: 'mid' });
    const k = (s) => s.split('+').map((x) => `<kbd>${esc(x)}</kbd>`).join('+');
    const rows = [
      ['h', 'ページを送る'], ['PageDown／PageUp', '次／前のページ（入力中でも効く。同じ欄のまま次のページへ）'],
      ['→／←', '→ が次、← が前。見開き表示のときだけ、右綴じに合わせて ← が次'], ['↑／↓', '上下の段へ'], ['Home／End', '最初／最後のページ'],
      ['h', '選ぶ'], ['クリック', '1枚を選ぶ'], ['Shift+クリック', '範囲を選ぶ（Shift+矢印でも）'], ['Ctrl+クリック', '1枚ずつ足す・外す'], ['Ctrl+A', 'すべて選ぶ'],
      ['h', 'ページを変える'], ['1〜5', '進行（未着手・画像のみ・セリフ入・完成・要修正）'], ['Ctrl+矢印', 'ページを動かす'],
      ['I／Shift+I', '後ろ／前に空白'], ['Delete', '空白を消す'], ['X', '除外（ファイルはそのまま）'], ['Shift+X', '隔離（「未使用」へ移す）'],
      ['h', '見る・探す'], ['Enter', '原寸で開く（読む）'], ['V', '原画と完成の切り替え'], ['/', '検索'], ['Esc', '入力を抜ける・閉じる'],
      ['h', '取り消す'], ['Ctrl+Z', '元に戻す'], ['Ctrl+Y', 'やり直し'],
    ];
    m.body.innerHTML = `<div class="help">${rows.map(([a, b]) => a === 'h' ? `<div class="h">${esc(b)}</div>` : `<div>${a.split('／').map(k).join('／')}</div><div>${esc(b)}</div>`).join('')}</div>`;
    m.foot.innerHTML = '<span class="left">右クリックでも同じ操作ができます。ドラッグで並べ替え、空白の上に落とすとその空白へ入ります。</span><button class="btn primary" data-ok>閉じる</button>';
    $('[data-ok]', m.foot).onclick = () => m.close();
    m.enter = () => m.close();
  }

  // ───────────── 設定 ─────────────

  function openSettings(tab = 'view') {
    const m = modal({ title: '設定', cls: 'wide' });
    const tabs = [['view', '表示'], ['pair', '完成の見分け'], ['quar', '隔離した絵'], ['excl', '除外した絵'], ['backup', 'バックアップ'], ['other', 'そのほか']];
    const tabBar = document.createElement('div');
    tabBar.className = 'm-tabs';
    tabBar.innerHTML = tabs.map(([k, l]) => `<button data-tab="${k}">${l}</button>`).join('');
    m.head.after(tabBar);
    m.foot.innerHTML = '<span class="left">変えた設定はすぐに保存されます</span><button class="btn primary" data-ok>閉じる</button>';
    $('[data-ok]', m.foot).onclick = () => m.close();
    const set = (patch) => run('settings', { patch }).then(() => draw());
    const draw = () => {
      if (!m.back.isConnected) return;
      const s = S.st.settings;
      $$('button', tabBar).forEach((b) => b.classList.toggle('on', b.dataset.tab === tab));
      const B = m.body;
      if (tab === 'view') {
        B.innerHTML = `
          <div class="sect">一覧</div>
          <div class="form-row"><label>見せる絵</label><div class="inline"><label class="radio"><input type="radio" name="view" value="fin">完成を優先</label><label class="radio"><input type="radio" name="view" value="raw">原画を優先</label></div></div>
          <div class="form-row"><label>サムネイルの大きさ</label><div class="inline"><input type="range" min="100" max="360" step="4" id="st-zoom"><span class="mono" id="st-zoomv"></span></div></div>
          <div class="sect">見開き（確認用・右綴じ）</div>
          <div class="form-row"><label>見開きで並べる</label><div class="inline"><label class="check"><input type="checkbox" id="st-spread">右のページが先（右綴じ）</label></div></div>
          <div class="form-row"><label>1ページ目</label><div class="inline"><label class="check"><input type="checkbox" id="st-cover">単独にする（表紙）</label></div><div class="note">オフにすると、1・2ページ目から見開きになります。</div></div>`;
        $$('input[name="view"]', B).forEach((r) => { r.checked = r.value === s.view; r.onchange = () => set({ view: r.value }); });
        const z = $('#st-zoom', B); z.value = s.thumb_size; $('#st-zoomv', B).textContent = s.thumb_size + 'px';
        z.oninput = () => { $('#st-zoomv', B).textContent = z.value + 'px'; };
        z.onchange = () => set({ thumb_size: +z.value });
        $('#st-spread', B).checked = s.spread; $('#st-spread', B).onchange = (e) => set({ spread: e.target.checked });
        $('#st-cover', B).checked = s.cover_single; $('#st-cover', B).onchange = (e) => set({ cover_single: e.target.checked });
      } else if (tab === 'pair') {
        B.innerHTML = `
          <p class="d-note">次のどれかに当たる絵を、同じ名前のページの「完成」として組み込みます。名前の変わった絵は、絵の中身（画素の指紋）で追いかけます。</p>
          <div class="form-row"><label>台詞入りフォルダ</label><input id="st-findir" spellcheck="false"><div class="note">台詞工房のフォルダ作業の保存先。ここにある同じ名前の絵を完成にします。</div></div>
          <div class="form-row"><label>_V番号</label><div class="inline"><label class="check"><input type="checkbox" id="st-v">名前_V2・_V3 を完成にする（大きい番号が新しい。前の版は「旧版」へ）</label></div></div>
          <div class="form-row"><label>末尾の決まり</label><input id="st-suf" spellcheck="false"><div class="note">空白かカンマで区切って並べます。例：_s _serif _fin</div></div>
          <div class="form-row"><label>自動で組にする</label><div class="inline"><label class="check"><input type="checkbox" id="st-auto">フォルダに来た完成を、同じ名前のページへ入れる</label></div></div>
          <div class="form-row"><label>進行</label><div class="inline"><label class="check"><input type="checkbox" id="st-astat">完成が来たら「セリフ入」にする（未着手・画像のみのとき）</label></div></div>`;
        const fd = $('#st-findir', B); fd.value = s.fin_dir; fd.onchange = () => set({ fin_dir: fd.value.trim() });
        $('#st-v', B).checked = s.use_vsave; $('#st-v', B).onchange = (e) => set({ use_vsave: e.target.checked });
        const sf = $('#st-suf', B); sf.value = s.fin_suffixes.join(' '); sf.onchange = () => set({ fin_suffixes: sf.value.split(/[\s,、]+/).filter(Boolean) });
        $('#st-auto', B).checked = s.auto_pair; $('#st-auto', B).onchange = (e) => set({ auto_pair: e.target.checked });
        $('#st-astat', B).checked = s.auto_status; $('#st-astat', B).onchange = (e) => set({ auto_status: e.target.checked });
      } else if (tab === 'quar') {
        const items = S.st.quarantine;
        B.innerHTML = `<p class="d-note">「${esc(s.quarantine_dir)}」フォルダの絵です（台割工房で隔離した絵・番付工房や手で移した絵）。戻すと台割に入り、隔離したときのセリフとメモも戻ります。</p>
          <div class="form-row"><label>隔離フォルダの名前</label><input id="st-q" spellcheck="false"><div class="note">番付工房と同じ「未使用」にしておくと、どちらで外した絵も一か所に集まります。</div></div>
          <div style="display:flex;align-items:center;gap:8px;margin:6px 0 10px"><b style="font-size:12.5px">${items.length} 枚</b>${items.length ? '<button class="btn small" id="q-all">すべて戻す</button>' : ''}</div>
          <div class="tile-grid" id="q-grid">${items.map((q) => `<div class="tile static"><div class="tp"><img loading="lazy" src="${thumbURL(q.q, q.v)}" alt=""></div><div class="tc"><span class="fn" title="${esc(q.q)}">${esc(q.q.split('/').pop())}</span>${q.orig ? `<span class="fn" style="color:var(--txt-faint)">元：${esc(q.orig)}</span>` : (q.legacy ? '<span class="tag">以前の隔離</span>' : '<span class="tag">ほかで外した絵</span>')}${q.text ? `<span class="fn" style="color:var(--txt-faint)">${esc(q.text)}</span>` : ''}<div class="act"><button class="btn small" data-restore="${esc(q.q)}">戻す</button></div></div></div>`).join('') || '<div class="list-empty">隔離した絵はありません</div>'}</div>`;
        const qi = $('#st-q', B); qi.value = s.quarantine_dir; qi.onchange = () => set({ quarantine_dir: qi.value.trim() });
        B.onclick = (e) => {
          const r = e.target.closest('[data-restore]');
          if (r) run('unquarantine', { items: [r.dataset.restore], after: S.primary }).then(draw);
          if (e.target.id === 'q-all') run('unquarantine', { items: items.map((q) => q.q), after: S.primary }).then(draw);
        };
      } else if (tab === 'excl') {
        const items = S.st.excluded;
        B.onclick = null;
        B.innerHTML = `<p class="d-note">除外したページです。ファイルはフォルダに残っています。戻すと、いま選んでいるページの後ろに入ります（選んでいなければ元の場所）。</p>
          <div class="tile-grid">${items.map((e) => { const k = e.ok.findIndex(Boolean); return `<div class="tile static"><div class="tp">${k >= 0 ? `<img loading="lazy" src="${thumbURL(e.files[k], e.v[k])}" alt="">` : '<span style="color:var(--txt-faint);font-size:11px">見つかりません</span>'}</div><div class="tc"><span class="fn">${esc(e.files.join(' ／ '))}</span>${e.text ? `<span class="fn" style="color:var(--txt-faint)">${esc(e.text)}</span>` : ''}<div class="act"><button class="btn small" data-unex="${e.id}">戻す</button></div></div></div>`; }).join('') || '<div class="list-empty">除外したページはありません</div>'}</div>`;
        B.onclick = (e) => { const r = e.target.closest('[data-unex]'); if (r) run('unexclude', { ids: [r.dataset.unex], after: S.primary }).then(draw); };
      } else if (tab === 'backup') {
        B.onclick = null;
        B.innerHTML = '<p class="d-note">台割.daiwari の自動バックアップです（直近20回分と、日ごとの7日分）。戻しても、元に戻す（Ctrl+Z）で今の状態へ帰れます。</p><div class="bk-list" id="bk">読み込み中…</div>';
        request('/api/backups').then((d) => {
          const el = $('#bk', B);
          if (!el) return;
          if (!d.ok || !d.items.length) { el.innerHTML = '<div class="list-empty">まだバックアップはありません</div>'; return; }
          el.innerHTML = d.items.map((b) => `<div class="bk"><div><div class="when">${esc(b.label)}</div><div class="info">${b.pages == null ? '読めません' : `${b.pages}ページ・完成 ${b.done}${b.title ? '・' + esc(b.title) : ''}`}</div></div><span></span><button class="btn small" data-bk="${esc(b.name)}" ${b.pages == null ? 'disabled' : ''}>この時点に戻す</button></div>`).join('');
          el.onclick = async (e) => {
            const r = e.target.closest('[data-bk]');
            if (!r) return;
            if (await confirmBox('この時点の台割に戻しますか？（元に戻す で今の状態へ帰れます）', { ok: '戻す' })) run('restore_backup', { name: r.dataset.bk });
          };
        });
      } else {
        B.onclick = null;
        B.innerHTML = `
          <div class="form-row"><label>外部アプリ</label><input id="st-ext" spellcheck="false" placeholder="空なら Windows の既定のアプリ"><div class="note">右クリック →「開く」→「外部アプリで開く」で使います。例："C:\\Program Files\\App\\app.exe" "{file}"（{file} が絵の場所に置き換わります）</div></div>
          <div class="form-row"><label>字数の目安</label><div class="inline"><input type="number" id="st-warn" min="10" max="2000">字／ページ（超えると字数が赤くなります）</div></div>
          <div class="sect">非常用</div>
          <div class="form-row"><label>読み直し</label><div class="inline"><button class="btn small" id="st-rescan">フォルダを読み直す</button></div><div class="note">ふだんはフォルダを自動で見張っています。表示が合わないときだけ押してください。</div></div>
          <div class="form-row"><label>記録</label><div class="mono" style="font-size:12px;color:var(--txt-dim);user-select:text">${esc(S.st.folder)}\\.daiwari\\log.txt</div></div>
          <div class="form-row"><label>版</label><div class="mono" style="font-size:12px;color:var(--txt-dim)">台割工房 ${esc(S.st.version)}</div></div>`;
        const ex = $('#st-ext', B); ex.value = s.external_cmd; ex.onchange = () => set({ external_cmd: ex.value.trim() });
        const w = $('#st-warn', B); w.value = s.chars_warn; w.onchange = () => set({ chars_warn: +w.value || 120 });
        $('#st-rescan', B).onclick = () => run('rescan');
      }
    };
    tabBar.addEventListener('click', (e) => { const b = e.target.closest('[data-tab]'); if (b) { tab = b.dataset.tab; m.body.onclick = null; draw(); } });
    draw();
    m.redraw = draw;
  }

  // ───────────── 書き出し ─────────────

  function exportPreview(o) {
    const lines = [];
    let n = o.start, shown = 0;
    const total = S.pages.length;
    S.pages.forEach((p, i) => {
      let src = null;
      if (o.use === 'raw') src = p.raw_ok ? p.raw : (p.fin_ok ? p.fin : null);
      else if (o.use === 'finonly') src = p.fin_ok ? p.fin : null;
      else src = p.fin_ok ? p.fin : (p.raw_ok ? p.raw : null);
      const num = String(n).padStart(o.pad, '0');
      const show = shown < 6 || i >= total - 2;
      if (!src) {
        if (show) lines.push(`P.${pad(p.n)}  （${p.kind === 'image' ? '完成なし' : '空白'}${o.blank === 'skip' ? `・${num} を飛ばす` : '・詰める'}）`);
        if (o.blank === 'skip') n++;
      } else {
        const ext = src.slice(src.lastIndexOf('.')).toLowerCase();
        if (show) lines.push(`P.${pad(p.n)}  ${src}  →  ${o.prefix}${num}${ext}`);
        n++;
      }
      if (show) shown++;
      else if (shown === 6) { lines.push('　…'); shown++; }
    });
    return lines.join('\n');
  }

  function openExport() {
    flushAllText();
    const s = S.st.settings;
    const m = modal({ title: '書き出し', cls: 'mid' });
    const o = { use: s.export_use, prefix: s.export_prefix || '', pad: s.export_pad, start: s.export_start, blank: s.export_blank,
      strip: s.export_strip, script: s.export_script, memo: s.export_memo, dest: null };
    m.body.innerHTML = `
      <p class="d-note">台割の順に、別のフォルダへ連番でコピーします。元の画像には触りません。</p>
      <div class="form-row"><label>使う絵</label><div class="inline"><label class="radio"><input type="radio" name="use" value="fin">完成を優先（無ければ原画）</label><label class="radio"><input type="radio" name="use" value="raw">原画</label><label class="radio"><input type="radio" name="use" value="finonly">完成だけ</label></div></div>
      <div class="form-row"><label>名前</label><div class="inline"><input id="ex-prefix" placeholder="頭の文字（空でもよい）" style="width:200px" spellcheck="false">＋番号<input type="number" id="ex-pad" min="1" max="6" title="桁数">桁　開始<input type="number" id="ex-start" min="0" max="99999"></div></div>
      <div class="form-row"><label>空白・絵の無いページ</label><div class="inline"><label class="radio"><input type="radio" name="blank" value="skip">番号を飛ばす</label><label class="radio"><input type="radio" name="blank" value="close">詰める</label></div></div>
      <div class="form-row"><label>メタデータ</label><div class="inline"><label class="check"><input type="checkbox" id="ex-strip">生成情報（プロンプトなど）を消す</label></div><div class="note">絵のデータはそのまま、PNG・JPEG・WebP の文字情報だけを除きます。</div></div>
      <div class="form-row"><label>台本</label><div class="inline"><label class="check"><input type="checkbox" id="ex-script">台本.txt も書き出す</label><label class="check"><input type="checkbox" id="ex-memo">メモと修正指示も入れる</label></div></div>
      <div class="form-row"><label>書き出し先</label><div class="inline"><span id="ex-dest" class="mono" style="font-size:12px;color:var(--txt-dim)"></span><button class="btn small" id="ex-pick">フォルダを選ぶ…</button><button class="btn small ghost" id="ex-reset" hidden>元に戻す</button></div><div class="note">選ばなければ、画像フォルダの中の「書き出し」フォルダに、日時つきのフォルダを作ります。</div></div>
      <div class="sect">変わる名前（はじめの数枚）</div>
      <div class="preview-names" id="ex-prev"></div>
      <div id="ex-progress" hidden style="margin-top:12px"><div class="progress"><i></i></div><div style="font-size:12px;color:var(--txt-dim);margin-top:6px" id="ex-ptext"></div></div>`;
    const B = m.body;
    const sync = () => {
      o.use = $('input[name="use"]:checked', B).value;
      o.blank = $('input[name="blank"]:checked', B).value;
      o.prefix = $('#ex-prefix', B).value.replace(/[\\/:*?"<>|]/g, '');
      o.pad = clamp(parseInt($('#ex-pad', B).value, 10) || 3, 1, 6);
      o.start = clamp(parseInt($('#ex-start', B).value, 10) || 0, 0, 99999);
      o.strip = $('#ex-strip', B).checked;
      o.script = $('#ex-script', B).checked;
      o.memo = $('#ex-memo', B).checked;
      $('#ex-memo', B).disabled = !o.script;
      $('#ex-prev', B).textContent = exportPreview(o);
      $('#ex-dest', B).textContent = o.dest ? o.dest + '\\…' : `${S.st.folder_name}\\書き出し\\…`;
      $('#ex-reset', B).hidden = !o.dest;
    };
    $$('input[name="use"]', B).forEach((r) => { r.checked = r.value === o.use; });
    $$('input[name="blank"]', B).forEach((r) => { r.checked = r.value === o.blank; });
    $('#ex-prefix', B).value = o.prefix;
    $('#ex-pad', B).value = o.pad;
    $('#ex-start', B).value = o.start;
    $('#ex-strip', B).checked = o.strip;
    $('#ex-script', B).checked = o.script;
    $('#ex-memo', B).checked = o.memo;
    B.addEventListener('input', sync);
    B.addEventListener('change', sync);
    $('#ex-pick', B).onclick = async () => {
      const d = await api('pick_folder', {}, { state: false }).catch(() => null);
      if (d && d.result) { o.dest = d.result; sync(); }
    };
    $('#ex-reset', B).onclick = () => { o.dest = null; sync(); };
    sync();
    m.foot.innerHTML = '<span class="left" id="ex-left"></span><button class="btn" data-no>閉じる</button><button class="btn primary" data-go>書き出す</button>';
    $('[data-no]', m.foot).onclick = () => m.close();
    $('[data-go]', m.foot).onclick = async () => {
      sync();
      const go = $('[data-go]', m.foot);
      go.disabled = true;
      run('settings', { patch: { export_use: o.use, export_prefix: o.prefix, export_pad: o.pad, export_start: o.start, export_blank: o.blank, export_strip: o.strip, export_script: o.script, export_memo: o.memo } }, { state: false });
      $('#ex-progress', B).hidden = false;
      S.exportBox = B;
      try {
        const d = await request('/api/export', { method: 'POST', body: JSON.stringify({ opts: o }) });
        if (!d.ok) { toast(d.error || '書き出せませんでした', 'error'); go.disabled = false; return; }
        const r = d.result;
        $('#ex-ptext', B).textContent = `${r.count}枚を書き出しました${r.skipped ? `（飛ばしたページ ${r.skipped}）` : ''}${o.strip ? `。メタデータを消した絵 ${r.stripped}枚` : ''}。`;
        $('.progress i', B).style.width = '100%';
        $('#ex-left', m.foot).textContent = r.dest;
        go.textContent = 'フォルダを開く';
        go.disabled = false;
        go.onclick = () => run('open_dest', { path: r.dest }, { state: false });
      } catch (e) {
        toast('書き出せませんでした', 'error');
        go.disabled = false;
      } finally {
        S.exportBox = null;
      }
    };
  }

  function exportProgress(d) {
    const B = S.exportBox;
    if (!B) return;
    $('.progress i', B).style.width = Math.round((d.done / Math.max(1, d.total)) * 100) + '%';
    $('#ex-ptext', B).textContent = `書き出し中 ${d.done}/${d.total}`;
  }

  // ───────────── 知らせ ─────────────

  function toast(text, kind = 'info', action = null) {
    const box = $('#toasts');
    const el = document.createElement('div');
    el.className = 'toast ' + kind;
    el.innerHTML = `<span>${esc(text)}</span>`;
    if (action) {
      const b = document.createElement('button');
      b.textContent = action.label;
      b.onclick = () => { el.remove(); action.fn(); };
      el.appendChild(b);
    }
    box.appendChild(el);
    while (box.children.length > 4) box.firstChild.remove();
    setTimeout(() => el.remove(), action ? 7000 : kind === 'error' ? 6500 : 3400);
  }

  // ───────────── ドラッグで並べ替え ─────────────

  let drag = null;

  function nearestCard(x, y) {
    let best = null, bd = Infinity;
    const wr = $('#board-wrap').getBoundingClientRect();
    for (const c of cards.values()) {
      if (!c.el.offsetParent) continue;
      const r = c.el.getBoundingClientRect();
      if (r.bottom < wr.top || r.top > wr.bottom) continue;
      const dx = x < r.left ? r.left - x : x > r.right ? x - r.right : 0;
      const dy = y < r.top ? r.top - y : y > r.bottom ? y - r.bottom : 0;
      const d = dx * dx + dy * dy * 4;
      if (d < bd) { bd = d; best = c.el; }
    }
    return best;
  }

  function dropTarget(e) {
    let el = document.elementFromPoint(e.clientX, e.clientY)?.closest('.card');
    if (!el) el = nearestCard(e.clientX, e.clientY);
    if (!el) return null;
    const id = el.dataset.id;
    if (drag.ids.includes(id)) return null;
    const p = S.byId.get(id);
    const r = el.getBoundingClientRect();
    if (drag.single && p.kind !== 'image' && S.byId.get(drag.single)?.kind === 'image') {
      const inner = e.clientX > r.left + r.width * 0.2 && e.clientX < r.right - r.width * 0.2
        && e.clientY > r.top + r.height * 0.12 && e.clientY < r.bottom - r.height * 0.12;
      if (inner) return { fill: true, id, el };
    }
    const rtl = S.st.settings.spread;
    const left = e.clientX < r.left + r.width / 2;
    const after = rtl ? left : !left;
    const i = S.idx.get(id);
    let before = id;
    if (after) {
      let j = i + 1;
      while (j < S.pages.length && drag.ids.includes(S.pages[j].id)) j++;
      before = j < S.pages.length ? S.pages[j].id : null;
    }
    return { fill: false, before, el, after, r, rtl };
  }

  function showDrop(t) {
    $$('.card.fill-target').forEach((x) => x.classList.remove('fill-target'));
    const mark = $('#drop-mark');
    if (!t) { mark.style.display = 'none'; return; }
    if (t.fill) { t.el.classList.add('fill-target'); mark.style.display = 'none'; return; }
    const wrap = $('#board-wrap');
    const wr = wrap.getBoundingClientRect();
    const onLeft = t.rtl ? t.after : !t.after;
    const x = onLeft ? t.r.left - 8 : t.r.right + 4;
    mark.style.display = 'block';
    mark.style.left = (x - wr.left + wrap.scrollLeft) + 'px';
    mark.style.top = (t.r.top - wr.top + wrap.scrollTop) + 'px';
    mark.style.height = t.r.height + 'px';
  }

  function endDrag() {
    if (drag) drag.ids.forEach((id) => cards.get(id)?.el.classList.remove('dragging'));
    drag = null;
    showDrop(null);
  }

  // ───────────── つなぐ ─────────────

  function bind() {
    const board = $('#board'), wrap = $('#board-wrap');

    board.addEventListener('error', (e) => { if (e.target.tagName === 'IMG') e.target.classList.add('broken'); }, true);

    board.addEventListener('mousedown', (e) => {
      const c = e.target.closest('.card');
      if (!c || e.button !== 0) return;
      const id = c.dataset.id;
      if (e.shiftKey) { e.preventDefault(); rangeSel(id, { noScroll: true }); }
      else if (e.ctrlKey || e.metaKey) { e.preventDefault(); toggleSel(id); }
      else if (!S.sel.has(id) || S.sel.size === 1) selectOnly(id, { noScroll: true });
      else S.pendingClick = id;
    });
    board.addEventListener('click', (e) => {
      const c = e.target.closest('.card');
      if (c && S.pendingClick === c.dataset.id && !e.shiftKey && !e.ctrlKey) selectOnly(c.dataset.id, { noScroll: true });
      S.pendingClick = null;
    });
    board.addEventListener('dblclick', (e) => { if (e.target.closest('.card')) openReader(); });
    wrap.addEventListener('contextmenu', (e) => {
      const c = e.target.closest('.card');
      if (!c) return;
      e.preventDefault();
      if (!S.sel.has(c.dataset.id)) selectOnly(c.dataset.id, { noScroll: true });
      openMenu(e.clientX, e.clientY);
    });
    wrap.addEventListener('mousedown', (e) => {
      if (e.target === wrap || e.target === board) { if (!e.shiftKey && !e.ctrlKey && S.sel.size > 1) selectOnly(S.primary, { noScroll: true }); }
    });
    wrap.addEventListener('wheel', (e) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      const v = clamp((S.zoomLive || S.st.settings.thumb_size) + (e.deltaY < 0 ? 8 : -8), 100, 360);
      applyZoom(v);
    }, { passive: false });

    // ドラッグ
    board.addEventListener('dragstart', (e) => {
      const c = e.target.closest('.card');
      if (!c) return;
      const id = c.dataset.id;
      if (!S.sel.has(id)) selectOnly(id, { noScroll: true });
      const ids = selectedIds();
      drag = { ids, single: ids.length === 1 ? ids[0] : null };
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/x-daiwari', ids.join(','));
      const g = document.createElement('div');
      g.className = 'drag-ghost';
      g.textContent = ids.length > 1 ? `${ids.length}ページ` : 'P.' + pad(S.byId.get(id).n);
      document.body.appendChild(g);
      e.dataTransfer.setDragImage(g, 12, 12);
      setTimeout(() => g.remove(), 0);
      requestAnimationFrame(() => ids.forEach((i) => cards.get(i)?.el.classList.add('dragging')));
    });
    wrap.addEventListener('dragover', (e) => {
      if (!drag) {
        if ([...e.dataTransfer.types].includes('Files')) { e.preventDefault(); e.dataTransfer.dropEffect = 'none'; }
        return;
      }
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      const wr = wrap.getBoundingClientRect();
      if (e.clientY < wr.top + 56) wrap.scrollTop -= 16;
      else if (e.clientY > wr.bottom - 56) wrap.scrollTop += 16;
      S.dropT = dropTarget(e);
      showDrop(S.dropT);
    });
    wrap.addEventListener('dragleave', (e) => { if (e.target === wrap) showDrop(null); });
    wrap.addEventListener('drop', (e) => {
      if (!drag) {
        if ([...e.dataTransfer.types].includes('Files')) {
          e.preventDefault();
          toast('外の絵を落として取り込むのは次の版で入ります。いまはエクスプローラで画像フォルダへコピーしてください（待ち受けの空白があればそこへ入ります）');
        }
        return;
      }
      e.preventDefault();
      const t = S.dropT, d = drag;
      endDrag();
      if (!t) return;
      if (t.fill) run('fill', { blank: t.id, src: d.single });
      else run('move', { ids: d.ids, how: 'before', before: t.before });
    });
    board.addEventListener('dragend', endDrag);

    // ストリップ
    const strip = $('#strip');
    strip.addEventListener('click', (e) => {
      const t = e.target.closest('i');
      if (!t) return;
      const p = S.pages[+t.dataset.i];
      if (p) selectOnly(p.id);
    });
    strip.addEventListener('mousemove', (e) => {
      const t = e.target.closest('i');
      const p = t && S.pages[+t.dataset.i];
      strip.title = p ? `P.${pad(p.n)}　${SLABEL[p.status]}　${p.kind === 'blank' ? '空白' : (p.fin || p.raw || '')}` : '';
    });

    // お知らせ
    $('#notices').addEventListener('click', (e) => {
      const b = e.target.closest('[data-act]');
      if (!b) return;
      const n = b.closest('.notice');
      if (n.dataset.sugg) run('suggest', { id: n.dataset.sugg, accept: b.dataset.act === 'yes' });
      else run('notice', { id: n.dataset.id, action: b.dataset.act });
    });

    // 道具の列
    $('#chips').addEventListener('click', (e) => {
      if (e.target.closest('[data-seen-all]')) { run('seen', { all: true }); return; }
      const c = e.target.closest('[data-f]');
      if (!c) return;
      S.filter = (S.filter === c.dataset.f && c.dataset.f !== 'all') ? 'all' : c.dataset.f;
      filterChanged();
    });
    const search = $('#search');
    search.addEventListener('input', () => { S.query = search.value; filterChanged(); });
    search.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); gotoMatch(e.shiftKey ? -1 : 1); }
      if (e.key === 'Escape') { e.preventDefault(); if (search.value) { search.value = ''; S.query = ''; filterChanged(); } else search.blur(); }
    });
    $('#search-next').onclick = () => gotoMatch(1);
    $('#search-prev').onclick = () => gotoMatch(-1);
    $('#only-match').onchange = (e) => { S.onlyMatch = e.target.checked; renderBoard(); };
    $('#view-seg').addEventListener('click', (e) => { const b = e.target.closest('[data-v]'); if (b) setView(b.dataset.v); });
    $('#spread').onchange = (e) => run('settings', { patch: { spread: e.target.checked } });
    const zoom = $('#zoom');
    zoom.addEventListener('input', () => applyZoom(+zoom.value));

    // 上の帯
    let titleTimer = 0;
    const title = $('#title');
    title.addEventListener('input', () => { clearTimeout(titleTimer); titleTimer = setTimeout(() => run('title', { value: title.value }), 450); });
    title.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === 'Escape') { e.preventDefault(); title.blur(); } });
    title.addEventListener('blur', () => { clearTimeout(titleTimer); if (S.st && title.value !== S.st.title) run('title', { value: title.value }); });
    $('#folder').onclick = () => run('open_folder', {}, { state: false });
    $('#tabs').addEventListener('click', (e) => { const b = e.target.closest('[data-view]'); if (!b) return; if (b.dataset.view === 'read') openReader(); else closeReader(); });
    $('#undo').onclick = undo;
    $('#redo').onclick = redo;
    $('#btn-export').onclick = openExport;
    $('#btn-settings').onclick = () => openSettings();
    $('#btn-help').onclick = openHelp;
    $('#offline-retry').onclick = () => location.reload();

    // 右の編集欄
    const detail = $('#detail');
    detail.addEventListener('input', (e) => {
      const f = e.target.dataset.field;
      if (!f || !S.primary) return;
      if (e.target.tagName === 'TEXTAREA') autoGrow(e.target);
      if (f === 'lines') updateCount();
      queueText(S.primary, f, e.target.value);
    });
    detail.addEventListener('focusout', (e) => {
      const f = e.target.dataset?.field;
      if (f && S.primary) flushText(S.primary + '|' + f);
    });
    detail.addEventListener('click', (e) => {
      const t = e.target.closest('button, .d-preview');
      if (!t) return;
      const d = t.dataset;
      if (d.status) setStatus(d.status);
      else if (d.nav) step(+d.nav);
      else if (d.which) { S.detailWhich = d.which; renderDetail(); }
      else if (d.reveal) run('reveal', { rel: d.reveal }, { state: false });
      else if (d.ext) run('external', { rel: d.ext }, { state: false });
      else if (d.useold) run('use_old', { id: S.primary, rel: d.useold });
      else if (d.move) { if (d.move === 'number') moveToNumber(); else move(d.move); }
      else if (d.act === 'read') openReader();
      else if (d.act === 'fill') openPicker(S.primary);
      else if (d.act === 'wait') toggleWait();
      else if (d.act === 'delete') deletePages();
      else if (d.act === 'exclude') excludeSel();
      else if (d.act === 'quarantine') quarantineSel();
      else if (d.act === 'pair') run('pair', { ids: selectedIds() });
      else if (d.act === 'blank-before') insertBlank('before');
      else if (d.act === 'blank-after') insertBlank('after');
    });

    // 読む
    $('#r-back').onclick = closeReader;
    $('#r-stage').addEventListener('click', (e) => {
      const b = e.target.closest('[data-step]');
      if (b) { step(+b.dataset.step); return; }
      const pane = e.target.closest('.r-pane');
      if (pane && pane.dataset.id && pane.dataset.id !== S.primary) selectOnly(pane.dataset.id, { noScroll: true });
    });
    $('#r-stage').addEventListener('wheel', (e) => { e.preventDefault(); step(e.deltaY > 0 ? 1 : -1); }, { passive: false });
    $('#r-which').addEventListener('click', (e) => { const b = e.target.closest('[data-v]'); if (b && !b.disabled) setView(b.dataset.v); });
    $('#r-mode').addEventListener('click', (e) => { const b = e.target.closest('[data-m]'); if (b) { S.readMode = b.dataset.m; renderReader(); } });
    $('#r-status').addEventListener('click', (e) => { const b = e.target.closest('[data-status]'); if (b) setStatus(b.dataset.status); });
    $('#r-exclude').onclick = excludeSel;
    $('#r-quar').onclick = quarantineSel;

    // 閉じる・窓
    document.addEventListener('mousedown', (e) => {
      if (!$('#menu').hidden && !e.target.closest('.menu')) closeMenu();
    });
    window.addEventListener('blur', closeMenu);
    wrap.addEventListener('scroll', closeMenu, { passive: true });
    let geoTimer = 0;
    window.addEventListener('resize', () => {
      closeMenu();
      clearTimeout(geoTimer);
      geoTimer = setTimeout(() => run('window', { w: outerWidth, h: outerHeight, aw: screen.availWidth, ah: screen.availHeight }, { state: false }), 900);
    });
    window.addEventListener('pagehide', () => {
      flushAllText();
      try { navigator.sendBeacon('/api/bye?t=' + encodeURIComponent(TOKEN)); } catch (e) { /* 送れなくても問題ない */ }
    });
    document.addEventListener('keydown', onKey);
  }

  let zoomTimer = 0;
  function applyZoom(v) {
    S.zoomLive = v;
    $('#board').style.setProperty('--tw', v + 'px');
    $('#zoom').value = v;
    clearTimeout(zoomTimer);
    zoomTimer = setTimeout(() => {
      run('settings', { patch: { thumb_size: v } }).then(() => { S.zoomLive = 0; });
    }, 500);
  }

  // ───────────── キー ─────────────

  function onKey(e) {
    const t = e.target;
    const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
    const ctrl = e.ctrlKey || e.metaKey;
    if (S.modals.length) {
      const m = S.modals[S.modals.length - 1];
      if (e.key === 'Escape') { e.preventDefault(); m.close(null); }
      else if (e.key === 'Enter' && m.enter && !(t && t.tagName === 'TEXTAREA')) { e.preventDefault(); m.enter(); }
      return;
    }
    if (!$('#menu').hidden && e.key === 'Escape') { closeMenu(); return; }
    if (!S.st) return;
    if (e.key === 'PageDown' || e.key === 'PageUp') {
      e.preventDefault();
      step(e.key === 'PageDown' ? 1 : -1, { keepField: typing && t.closest('#detail') });
      return;
    }
    if (typing) {
      if (e.key === 'Escape') { e.preventDefault(); t.blur(); }
      return;
    }
    const k = e.key;
    const spread = S.view === 'read' ? S.readMode === 'spread' : S.st.settings.spread;
    if (ctrl) {
      const lk = k.toLowerCase();
      if (lk === 'z' && !e.shiftKey) { e.preventDefault(); undo(); return; }
      if (lk === 'y' || (lk === 'z' && e.shiftKey)) { e.preventDefault(); redo(); return; }
      if (lk === 'a') { e.preventDefault(); S.sel = new Set(S.pages.map((p) => p.id)); S.anchor = S.pages[0]?.id; renderSelection(); return; }
      if (k === 'ArrowLeft' || k === 'ArrowRight') { e.preventDefault(); const later = (k === 'ArrowRight') !== spread; move(later ? 'down' : 'up'); return; }
      if (k === 'ArrowUp' || k === 'ArrowDown') { e.preventDefault(); const n = spread ? rowSize() * 2 : rowSize(); move(k === 'ArrowDown' ? 'down' : 'up', { k: n }); return; }
      if (k === 'Home') { e.preventDefault(); move('top'); return; }
      if (k === 'End') { e.preventDefault(); move('end'); return; }
      return;
    }
    if (e.altKey) return;
    switch (k) {
      case 'ArrowRight': case 'ArrowLeft': {
        e.preventDefault();
        const next = (k === 'ArrowRight') !== spread;
        step(next ? 1 : -1, { extend: e.shiftKey && S.view === 'board' });
        return;
      }
      case 'ArrowDown': case 'ArrowUp':
        if (S.view === 'board') { e.preventDefault(); rowStep(k === 'ArrowDown' ? 1 : -1, e.shiftKey); }
        return;
      case 'Home': e.preventDefault(); if (S.pages.length) selectOnly(S.pages[0].id); return;
      case 'End': e.preventDefault(); if (S.pages.length) selectOnly(S.pages[S.pages.length - 1].id); return;
      case '1': case '2': case '3': case '4': case '5': e.preventDefault(); setStatus(STATUS[+k - 1][0]); return;
      case 'i': case 'I': e.preventDefault(); insertBlank(e.shiftKey ? 'before' : 'after'); return;
      case 'x': case 'X': e.preventDefault(); if (e.shiftKey) quarantineSel(); else excludeSel(); return;
      case 'v': case 'V': e.preventDefault(); toggleView(); return;
      case 'Enter': e.preventDefault(); if (S.view === 'board') openReader(); return;
      case 'Delete': case 'Backspace': if (S.view === 'board') { e.preventDefault(); deletePages(); } return;
      case '/': e.preventDefault(); if (S.view === 'read') closeReader(); $('#search').focus(); $('#search').select(); return;
      case '?': e.preventDefault(); openHelp(); return;
      case 'Escape':
        e.preventDefault();
        if (S.view === 'read') closeReader();
        else if (S.sel.size > 1) selectOnly(S.primary, { noScroll: true });
        else if (S.filter !== 'all' || S.query) { S.filter = 'all'; S.query = ''; $('#search').value = ''; filterChanged(); }
        return;
      default:
    }
  }

  // ───────────── はじめる ─────────────

  bind();
  fetchState().then(() => {
    connect();
    if (S.primary) scrollToPrimary();
  });
  window.__dk = { S, api, step, openMenu, cards };
})();
