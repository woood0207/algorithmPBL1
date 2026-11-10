/* 경로 탐색 탭. 알고리즘은 서버의 Python 엔진이 돌리고, 여기서는 Trace JSON을 그리기만 한다. */
const $ = id => document.getElementById(id);
const api = async (path, body) => {
  const res = await fetch(path, body === undefined ? undefined : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await res.json();
  if (!res.ok) throw Error(data.error || res.statusText);
  return data;
};
const STOPS = ['#440154', '#482878', '#3e4989', '#31688e', '#26828e', '#1f9e89', '#35b779', '#6ece58', '#b5de2b', '#fde725'].map(h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16)));
const VIRIDIS = Array.from({ length: 128 }, (_, i) => {
  const p = i / 127 * 9, a = Math.floor(p), b = Math.min(9, a + 1), t = p - a;
  return `rgb(${STOPS[a].map((x, j) => Math.round(x + (STOPS[b][j] - x) * t))})`;
});
const SEGMENT = { walk1: '#d62728', drive: '#0a35f3', walk2: '#d62728', to_via: '#1f77b4', from_via: '#d62728', uphill: '#d6336c', flat: '#1971c2' };
const SEGMENT_NAME = { walk1: '도보', drive: '차량', walk2: '도보', to_via: '출발 → 경유', from_via: '경유 → 도착', uphill: '오르막', flat: '평지·내리막' };
const FAIL = { budget: '예산 내 미도달', beam_exhausted: '후보 소진', dead_end: '이동 불가', unreachable: '도달 불가능' };
const PRESETS = {
  weights: () => [0, 1, 3].map(w => ({ algorithm: 'weighted', heuristic: 'haversine', weight: w })),
  alt: () => [{ algorithm: 'dijkstra' }, { algorithm: 'astar', heuristic: 'haversine' }, { algorithm: 'astar', heuristic: 'alt' }],
  greedy: () => ['greedy', 'beam', 'local', 'random'].map(a => ({ algorithm: a, heuristic: 'geo' })),
  bfs: () => [{ algorithm: 'bfs' }, { algorithm: 'bidirectional' }],
  wdw: () => [{ algorithm: 'walk_drive_walk' }],
  via: () => [{ algorithm: 'via' }],
  uphill: () => [{ algorithm: 'astar', heuristic: 'alt' }, { algorithm: 'alt_uphill', uphill: 8 }, { algorithm: 'alt_uphill', uphill: 30 }],
};
const S = {
  start: null, goal: null, landmarks: [], waypoints: [], zone: null, mode: 's', panels: PRESETS.weights(),
  traces: [], views: [], k: 0, maxK: 0, playing: false, credit: 0, last: 0, token: 0, hTable: null, hover: null,
  snap: { start: null, goal: null }, compare: null, fromFile: false,
};
const G = new ViewGroup(); G.needsFit = true;
const algo = id => World.meta.algorithms.find(a => a.id === id);
const fmt = (v, d = 1) => v == null ? '—' : v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const setStatus = t => { $('status').textContent = t; };
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

function options() {
  return {
    beam_width: Math.max(1, Math.min(100, +$('beamWidth').value || 5)), budget: Math.max(1, Math.min(50000, +$('budget').value || 2000)),
    seed: Number.isFinite(+$('seed').value) ? +$('seed').value : 42, no_revisit: $('noRevisit').checked, waypoints: S.waypoints,
    drive_center: [S.zone.lat, S.zone.lon], drive_radius: +$('driveRadius').value || 200, walk_kmh: +$('walkKmh').value || 5,
    drive_kmh: +$('driveKmh').value || 50, drive_rule: $('driveRule').value,
  };
}

/* ---- 실행 ---- */
async function runPanels({ jumpEnd = false } = {}) {
  const token = ++S.token;
  if (S.panels.some(p => p.algorithm === 'via') && !S.waypoints.length) defaultWaypoints();
  setStatus('탐색 중…');
  try {
    const data = await api('/api/run', { start: S.start, goal: S.goal, landmarks: S.landmarks, options: options(), panels: S.panels });
    if (token !== S.token) return;
    S.fromFile = false;
    showTraces(data.traces, jumpEnd);
    setStatus('');
    refreshHeuristicTable();
  } catch (e) { if (token === S.token) setStatus(e.message); }
}

function showTraces(traces, jumpEnd) {
  S.traces = traces;
  S.maxK = Math.max(1, ...traces.map(t => t.steps.length));
  S.k = jumpEnd || reduceMotion ? S.maxK : 0;
  S.playing = !jumpEnd && !reduceMotion;
  S.credit = 0;
  buildPanelViews();
  $('scrub').max = S.maxK;
  syncClock(); panelTable(); detailBlock();
}

function buildPanelViews() {
  const box = $('panels');
  for (const v of S.views) v.view.destroy();
  box.replaceChildren();
  const n = S.traces.length;
  box.style.setProperty('--cols', n <= 3 ? n : n === 4 ? 2 : 3);
  box.classList.toggle('dense', n > 3);
  S.views = S.traces.map(trace => {
    const el = document.createElement('div'); el.className = 'panel';
    const ttl = document.createElement('div'); ttl.className = 'ttl';
    const name = document.createElement('b'); name.textContent = trace.label || `w=${trace.weight}`;
    const line = document.createElement('span');
    ttl.append(name, line);
    const holder = document.createElement('div'); holder.className = 'mapbox';
    el.append(ttl, holder); box.append(el);
    const view = new MapView(holder, G, {
      overlay: (v, c) => drawTrace(v, c, trace),
      base: (v, b) => drawZoneRoads(v, b, trace),
      click: onMapClick, hover: (v, x, y, e) => onHover(v, x, y, e, trace), leave: () => { S.hover = null; $('tip').hidden = true; G.redraw(); },
    });
    return { trace, view, line };
  });
  G.needsFit = G.needsFit && true;
  G.changed();
}

/* ---- 그리기 ---- */
function frame(tr) {
  const n = Math.min(S.k, tr.steps.length), done = tr.steps.slice(0, n);
  const closed = new Map(), frontier = new Set(), pruned = new Set(), relaxed = [], moved = [];
  let events = n;
  done.forEach((s, i) => {
    closed.set(s.node, { i, s });
    s.pushed.forEach(v => frontier.add(v));
    (s.pruned || []).forEach(v => pruned.add(v));
    relaxed.push(...(s.pushed_edges || []));
    if (s.move_edge != null) moved.push(s.move_edge);
    events += s.pushed.length + (s.pruned || []).length;
  });
  for (const v of closed.keys()) { frontier.delete(v); pruned.delete(v); }
  return { n, done, closed, frontier, pruned, relaxed, moved, events, finished: n === tr.steps.length };
}

function edgeBetween(a, b) {
  return World.edgeOf.get(a + ',' + b) || World.edgeOf.get(b + ',' + a);
}

function hOf(tr, id) {
  const row = S.hTable && S.hTable.table[id];
  if (!row) return null;
  const name = tr.heuristic && tr.heuristic !== 'zero' ? tr.heuristic : 'alt', geo = S.hTable.alpha * row.hav;
  return name === 'haversine' ? row.hav : name === 'geo' ? geo : name === 'max' ? Math.max(geo, row.alt) : row.alt;
}

function drawSteepRoads(v, b) {
  /* 경사 3% 이상인 도로를 주황으로, 경사가 클수록 진하게. 경로에서 오르는 구간은 분홍, 나머지는 파랑. */
  const z = World.meta.elevation;
  if (!z) return;
  b.save(); b.lineWidth = 3;
  for (const r of World.roads) {
    if (r.u >= r.v || r.w <= 0) continue;
    const grade = Math.abs(z[r.v] - z[r.u]) / r.w;
    if (grade < 0.03) continue;
    b.strokeStyle = `rgba(232,89,12,${Math.min(0.85, 0.15 + grade * 2.5).toFixed(2)})`;
    b.beginPath(); v.tracePts(b, r); b.stroke();
  }
  b.restore();
}
function drawZoneRoads(v, b, tr) {
  if (tr.algorithm === 'alt_uphill') return drawSteepRoads(v, b);
  if (tr.algorithm !== 'walk_drive_walk') return;
  const zone = zoneNodes();
  b.save(); b.strokeStyle = '#0a35f3'; b.globalAlpha = 0.45; b.lineWidth = 2.5; b.beginPath();
  for (const r of World.roads) if (r.u < r.v && zone.has(r.u) && zone.has(r.v)) v.tracePts(b, r);
  b.stroke(); b.restore();
  v.circle(b, [S.zone.lat, S.zone.lon], +$('driveRadius').value || 200, '#0a35f3');
}
function zoneNodes() {
  const r = +$('driveRadius').value || 200, out = new Set();
  for (const n of World.nodes.values()) if (haversine(n, S.zone) < r) out.add(n.id);
  return out;
}

function drawArrows(v, c, ids, color) {
  c.fillStyle = color;
  ids.forEach((id, i) => {
    if (i % 2) return;
    const p = World.roads[id].pts, m = (p.length >> 2) * 2, j = Math.min(m, p.length - 4);
    const ax = v.X(p[j]), ay = v.Y(p[j + 1]), bx = v.X(p[j + 2]), by = v.Y(p[j + 3]);
    c.save(); c.translate((ax + bx) / 2, (ay + by) / 2); c.rotate(Math.atan2(by - ay, bx - ax));
    c.beginPath(); c.moveTo(5, 0); c.lineTo(-3, -3.5); c.lineTo(-3, 3.5); c.fill(); c.restore();
  });
}

function drawStar(c, x, y, r, fill) {
  c.beginPath();
  for (let i = 0; i < 10; i++) {
    const a = -Math.PI / 2 + i * Math.PI / 5, q = i % 2 ? r * 0.45 : r;
    c[i ? 'lineTo' : 'moveTo'](x + q * Math.cos(a), y + q * Math.sin(a));
  }
  c.closePath(); c.fillStyle = fill; c.fill(); c.strokeStyle = '#000'; c.lineWidth = 1; c.stroke();
}

function drawTrace(v, c, tr) {
  const f = frame(tr);
  if (Layers.heat && S.hTable) {
    const hi = [11, 95, 105], lo = [223, 231, 234];
    for (const n of World.nodes.values()) {
      const row = S.hTable.table[n.id];
      if (!row || row.true == null || row.true === 0) continue;
      const q = Math.max(0, Math.min(1, hOf(tr, n.id) / row.true));
      c.fillStyle = `rgb(${lo.map((x, i) => Math.round(x + (hi[i] - x) * q))})`;
      c.beginPath(); c.arc(v.X(n.wx), v.Y(n.wy), 4, 0, 7); c.fill();
    }
  }
  if (Layers.relaxed && f.relaxed.length) v.strokeRoads(c, f.relaxed, '#557aab', 1.7);
  if (Layers.tree) {
    const tree = [];
    for (const { s } of f.closed.values()) if (s.parent != null && s.move_edge == null) {
      const e = edgeBetween(s.parent, s.node);
      if (e) tree.push(e.id);
    }
    c.globalAlpha = 0.55; v.strokeRoads(c, tree, '#1f9a8a', 1.6); c.globalAlpha = 1;
  }
  const last = Math.max(1, S.maxK - 1);
  for (const [id, { i, s }] of f.closed) {
    const [x, y] = v.px(id);
    c.fillStyle = s.direction === 'backward' ? '#9467bd' : VIRIDIS[Math.round(i / last * 127)];
    c.beginPath(); c.arc(x, y, 3.2, 0, 7); c.fill();
  }
  if (Layers.frontier) {
    c.fillStyle = '#ff9f1c';
    for (const id of f.frontier) { const [x, y] = v.px(id); c.beginPath(); c.arc(x, y, 3, 0, 7); c.fill(); }
  }
  c.fillStyle = '#e48389';
  for (const id of f.pruned) { const [x, y] = v.px(id); c.beginPath(); c.arc(x, y, 3, 0, 7); c.fill(); }
  if (f.moved.length && !f.finished) v.strokeRoads(c, f.moved, '#f4b56b', 3);

  if (f.finished) {
    if (tr.segments && tr.segments.length) {
      for (const seg of tr.segments) { v.strokeRoads(c, seg.edge_ids, SEGMENT[seg.kind], 4.5); drawArrows(v, c, seg.edge_ids, '#fff'); }
    } else if (tr.edge_ids && tr.edge_ids.length) {
      v.strokeRoads(c, tr.edge_ids, tr.found ? '#d62728' : '#f4b56b', 4.5, tr.found ? null : [7, 6]);
      if (tr.found) drawArrows(v, c, tr.edge_ids, '#fff');
    } else if (tr.path && tr.path.length > 1) {
      c.strokeStyle = '#d62728'; c.lineWidth = 4; c.beginPath();
      tr.path.forEach((id, i) => { const [x, y] = v.px(id); i ? c.lineTo(x, y) : c.moveTo(x, y); }); c.stroke();
    }
  } else if (f.n && !f.moved.length) {
    const par = new Map(f.done.map(s => [s.node, s.parent])), chain = [], seen = new Set();
    let n = f.done[f.n - 1].node;
    while (n != null && !seen.has(n)) { seen.add(n); const p = par.get(n); if (p != null) { const e = edgeBetween(p, n); if (e) chain.push(e.id); } n = p; }
    v.strokeRoads(c, chain, 'rgba(214,39,40,.7)', 3);
  }
  if (f.n && !f.finished) { const [x, y] = v.px(f.done[f.n - 1].node); c.strokeStyle = '#000'; c.lineWidth = 1.5; c.beginPath(); c.arc(x, y, 7, 0, 7); c.stroke(); }

  if (S.hover != null && tr.heuristic && ['alt', 'max'].includes(tr.heuristic) && S.hTable) {
    const li = S.hTable.table[S.hover].li;
    if (li >= 0 && S.landmarks[li] != null) {
      const [a, b] = v.px(S.hover), [p, q] = v.px(S.landmarks[li]);
      c.save(); c.setLineDash([4, 4]); c.strokeStyle = '#7a55b0'; c.lineWidth = 1.5; c.beginPath(); c.moveTo(a, b); c.lineTo(p, q); c.stroke(); c.restore();
    }
  }
  if (S.mode === 'l' || tr.algorithm === 'alt_uphill' || (tr.heuristic && ['alt', 'max'].includes(tr.heuristic))) drawLandmarks(v, c, tr);
  if (tr.algorithm === 'via' || S.mode === 'w') drawWaypoints(v, c, tr);
  for (const [key, label, color] of [['start', '출발', 'green'], ['goal', '도착', 'red']]) {
    const snap = S.snap[key];
    if (snap && !S.fromFile) {
      const [wx, wy] = mercator(snap.lat, snap.lon), [x, y] = v.px(key === 'start' ? tr.start : tr.goal);
      c.save(); c.strokeStyle = color; c.setLineDash([5, 5]); c.lineWidth = 2; c.beginPath(); c.moveTo(v.X(wx), v.Y(wy)); c.lineTo(x, y); c.stroke();
      c.setLineDash([]); c.beginPath(); c.arc(v.X(wx), v.Y(wy), 5, 0, 7); c.stroke(); c.restore();
    }
  }
  const [sx, sy] = v.px(tr.start);
  c.fillStyle = 'limegreen'; c.strokeStyle = '#000'; c.lineWidth = 1; c.beginPath(); c.arc(sx, sy, 7, 0, 7); c.fill(); c.stroke();
  const [gx, gy] = v.px(tr.goal); drawStar(c, gx, gy, 10, 'red');
  if (S.hover != null) { const [x, y] = v.px(S.hover); c.strokeStyle = cssVar('--text'); c.lineWidth = 1.5; c.beginPath(); c.arc(x, y, 5, 0, 7); c.stroke(); }

  const entry = S.views.find(x => x.trace === tr);
  if (entry) {
    const unit = tr.cost_unit || 'm', cur = f.done[f.n - 1];
    const state = f.finished ? (tr.found ? `cost ${fmt(tr.cost)} ${unit}` : (FAIL[tr.reason] || '실패')) : cur ? `g=${cur.g.toFixed(0)} f=${cur.f.toFixed(0)}` : '대기';
    entry.line.textContent = `expanded ${f.n}/${tr.steps.length} | ${state} | events ${f.events}`;
  }
}

function drawLandmarks(v, c, tr) {
  const li = S.hover != null && S.hTable ? S.hTable.table[S.hover].li : -1;
  c.font = '600 11px system-ui';
  S.landmarks.forEach((id, i) => {
    const [x, y] = v.px(id), r = i === li ? 8 : 6;
    c.fillStyle = '#7a55b0'; c.strokeStyle = '#fff'; c.lineWidth = 2;
    c.beginPath(); c.moveTo(x, y - r); c.lineTo(x + r, y); c.lineTo(x, y + r); c.lineTo(x - r, y); c.closePath(); c.fill(); c.stroke();
    c.fillText('L' + (i + 1), x + 9, y - 6);
  });
}
function drawWaypoints(v, c, tr) {
  const chosen = tr.extra && tr.extra.via;
  c.font = '600 11px system-ui';
  for (const id of S.waypoints) {
    if (!World.nodes.has(id)) continue;
    const [x, y] = v.px(id);
    c.fillStyle = id === chosen ? '#ff7f0e' : '#ffd8a8'; c.strokeStyle = '#000'; c.lineWidth = 1;
    c.beginPath(); c.arc(x, y, id === chosen ? 8 : 6, 0, 7); c.fill(); c.stroke();
    c.fillStyle = '#7a3e00'; c.fillText((id === chosen ? 'W ' : '') + id, x + 9, y - 7);
  }
}

/* ---- 입력 ---- */
const HINTS = { s: '지도를 클릭하면 출발지로 지정합니다.', t: '지도를 클릭하면 도착지로 지정합니다.', l: '지도를 클릭하면 랜드마크를 추가하거나 뺍니다.', w: '지도를 클릭하면 경유지 후보를 추가하거나 뺍니다.', z: '지도를 클릭하면 차량 구역의 중심을 옮깁니다.' };
function setMode(m) {
  S.mode = m;
  document.querySelectorAll('#clickMode button').forEach(b => b.setAttribute('aria-pressed', b.dataset.v === m));
  $('modeHint').textContent = HINTS[m];
  G.redraw();
}
function onMapClick(v, x, y) {
  if (S.mode === 'z') {
    const scale = v.scale, wx = G.cx + (x - v.W / 2) / scale, wy = G.cy + (y - v.H / 2) / scale;
    S.zone = { lat: Math.atan(Math.sinh(Math.PI * (1 - 2 * wy))) * 180 / Math.PI, lon: wx * 360 - 180 };
    G.changed(); runPanels(); return;
  }
  const id = v.nearestNode(x, y, 18);
  if (id == null) { const r = v.nearestRoad(x, y); if (r) showRoad(r, true); return; }
  if (S.mode === 's' && id !== S.goal) { setEndpoints(id, S.goal); setMode('t'); }
  else if (S.mode === 't' && id !== S.start) setEndpoints(S.start, id);
  else if (S.mode === 'l') {
    const i = S.landmarks.indexOf(id), next = S.landmarks.slice();
    if (i >= 0) { if (next.length > 1) next.splice(i, 1); } else next.push(id);
    applyLandmarks({ ids: next });
  } else if (S.mode === 'w') {
    const i = S.waypoints.indexOf(id);
    if (i >= 0) S.waypoints.splice(i, 1); else S.waypoints.push(id);
    $('waypoints').value = S.waypoints.join(' ');
    if (S.waypoints.length) runPanels(); else G.redraw();
  }
}
function setEndpoints(s, t, keepSnap) {
  if (!keepSnap) S.snap = { start: null, goal: null };
  S.start = s; S.goal = t; $('start').value = s; $('goal').value = t;
  S.hTable = null; S.compare = null; compareTable();
  runPanels();
}
function showRoad(r, pinned) {
  const parts = [`간선 ${r.id + 1}`, `${r.u} → ${r.v}`, `${r.w.toFixed(2)} m`];
  if (r.name) parts.push(r.name);
  if (r.highway) parts.push(r.highway);
  $('hover').textContent = (pinned ? '선택: ' : '') + parts.join(' · ');
}
function onHover(v, x, y, e, tr) {
  const id = v.nearestNode(x, y);
  if (id !== S.hover) { S.hover = id; G.redraw(); }
  const tip = $('tip');
  if (id == null) {
    tip.hidden = true;
    const r = v.nearestRoad(x, y);
    if (r) showRoad(r); else $('hover').textContent = '드래그: 이동 · 휠: 확대 · 클릭: 지정';
    return;
  }
  const n = World.nodes.get(id), row = S.hTable && S.hTable.table[id], f = frame(tr), rec = f.closed.get(id);
  $('hover').textContent = `Node ${id} · ${n.lat.toFixed(6)}, ${n.lon.toFixed(6)}`;
  let html = `<b>노드 ${id}</b>${World.meta.main_nodes.includes(id) ? '' : ' (작은 성분)'}<br>`;
  if (row) {
    html += `실제 d(v,t)  ${row.true == null ? '∞' : row.true.toFixed(1) + ' m'}<br>h ALT        ${row.alt.toFixed(1)} m${row.li >= 0 ? ' (L' + (row.li + 1) + ')' : ''}<br>h 직선       ${row.hav.toFixed(1)} m<br>`;
  }
  html += `g 현재 패널  ${rec ? rec.s.g.toFixed(1) + ' ' + (tr.cost_unit || 'm') : '—'}`;
  tip.innerHTML = html.replace(/ {2,}/g, m => '&nbsp;'.repeat(m.length));
  tip.hidden = false;
  const w = tip.offsetWidth, h = tip.offsetHeight;
  tip.style.left = Math.min(e.clientX + 14, innerWidth - w - 8) + 'px';
  tip.style.top = (e.clientY + 14 + h > innerHeight ? e.clientY - h - 10 : e.clientY + 14) + 'px';
}
async function refreshHeuristicTable() {
  if (!S.landmarks.length) return;
  const goal = S.goal, data = await api('/api/heuristic', { goal, landmarks: S.landmarks });
  if (goal === S.goal) { S.hTable = data; G.redraw(); }
}
async function applyLandmarks(body) {
  try {
    const d = await api('/api/landmarks', body);
    S.landmarks = d.landmarks;
    $('k').value = Math.min(16, d.landmarks.length); $('kVal').textContent = d.landmarks.length;
    $('preInfo').textContent = `Dijkstra ${d.dijkstra_runs}번 (정방향·역방향 ×${d.landmarks.length}) · 거리 ${d.stored.toLocaleString()}개 저장 · ${d.prep_ms.toFixed(1)} ms`;
    S.hTable = null; S.compare = null; compareTable();
    await runPanels();
  } catch (e) { setStatus(e.message); }
}
function pickLandmarks(seed) { return applyLandmarks({ k: +$('k').value, method: $('lmMethod').value, seed }); }
function defaultWaypoints() {
  const a = World.nodes.get(S.start), b = World.nodes.get(S.goal), main = World.meta.main_nodes;
  const targets = [-0.3, 0, 0.3].map(o => ({ lat: (a.lat + b.lat) / 2 + o * (b.lon - a.lon), lon: (a.lon + b.lon) / 2 - o * (b.lat - a.lat) }));
  S.waypoints = [...new Set(targets.map(t => main.reduce((best, id) => haversine(World.nodes.get(id), t) < haversine(World.nodes.get(best), t) ? id : best, main[0])))];
  $('waypoints').value = S.waypoints.join(' ');
}

/* ---- 패널 구성 ---- */
function panelRows() {
  const box = $('panelRows'); box.replaceChildren();
  S.panels.forEach((p, i) => {
    const row = document.createElement('div'); row.className = 'panelrow';
    const sel = document.createElement('select');
    for (const a of World.meta.algorithms) sel.add(new Option(a.label, a.id, false, a.id === p.algorithm));
    sel.onchange = () => { const a = algo(sel.value); S.panels[i] = { algorithm: a.id, heuristic: a.default_heuristic, weight: a.default_weight }; panelsChanged(); };
    const x = document.createElement('button'); x.className = 'x'; x.textContent = '×'; x.title = '패널 삭제'; x.disabled = S.panels.length === 1;
    x.onclick = () => { S.panels.splice(i, 1); panelsChanged(); };
    row.append(sel, x);
    const a = algo(p.algorithm);
    if (a.uses_heuristic) {
      const sub = document.createElement('div'); sub.className = 'sub';
      const h = document.createElement('select');
      for (const [id, label] of Object.entries(World.meta.heuristics)) if (id !== 'zero') h.add(new Option(label, id, false, id === (p.heuristic || a.default_heuristic)));
      h.onchange = () => { p.heuristic = h.value; customChanged(); };
      sub.append(h);
      if (p.algorithm === 'weighted') {
        const w = document.createElement('input'); w.type = 'number'; w.min = 0; w.step = 0.5; w.value = p.weight ?? 3; w.title = '가중치 w';
        w.onchange = () => { p.weight = Math.max(0, +w.value || 0); customChanged(); };
        sub.append(w);
      } else sub.style.gridTemplateColumns = '1fr';
      row.append(sub);
    }
    if (p.algorithm === 'alt_uphill') {
      const sub = document.createElement('div'); sub.className = 'sub'; sub.style.gridTemplateColumns = '1fr auto';
      const t = document.createElement('span'); t.textContent = '업힐 λ (오르막 1 m = 평지 λ m)'; t.className = 'hint';
      const w = document.createElement('input'); w.type = 'number'; w.min = 0; w.step = 1; w.value = p.uphill ?? 8; w.title = '업힐 가중치 λ';
      w.onchange = () => { p.uphill = Math.max(0, +w.value || 0); customChanged(); };
      sub.append(t, w); row.append(sub);
    }
    box.append(row);
  });
  $('algoHint').textContent = [...new Set(S.panels.map(p => algo(p.algorithm).hint))].join(' / ');
  const via = S.panels.some(p => p.algorithm === 'via'), wdw = S.panels.some(p => p.algorithm === 'walk_drive_walk');
  document.querySelectorAll('.needs-via').forEach(e => { e.hidden = !via; });
  document.querySelectorAll('.needs-wdw').forEach(e => { e.hidden = !wdw; });
  if ((S.mode === 'w' && !via) || (S.mode === 'z' && !wdw)) setMode('s');
}
function customChanged() { $('preset').value = 'custom'; panelRows(); runPanels(); }
function panelsChanged() { customChanged(); }

function panelTable() {
  const unitOf = t => (t.cost_unit || 'm') + (t.extra && t.extra.uphill_lambda != null ? '@' + t.extra.uphill_lambda : '');  // λ가 다르면 비용 함수가 달라 비교하지 않음
  const bestOf = t => { const c = S.traces.filter(x => x.found && x.cost != null && unitOf(x) === unitOf(t)).map(x => x.cost); return c.length ? Math.min(...c) : null; };
  const mx = Math.max(1, ...S.traces.map(t => t.steps.length));
  let h = '<tr><th>패널</th><th>확장</th><th>비용</th><th>비용/최소</th><th>경로 정점</th><th>시간(ms)</th><th>상태</th></tr>';
  for (const t of S.traces) h += `<tr><td>${t.label}<div class="mini"><i style="width:${(100 * t.steps.length / mx).toFixed(1)}%"></i></div></td><td>${t.steps.length}</td><td>${t.found ? fmt(t.cost) + ' ' + (t.cost_unit || 'm') : '—'}</td><td>${t.found && bestOf(t) ? (t.cost / bestOf(t)).toFixed(4) : '—'}</td><td>${t.path.length}</td><td>${fmt(t.elapsed_ms, 2)}</td><td>${t.found ? '성공' : (FAIL[t.reason] || '실패')}</td></tr>`;
  $('panelTable').innerHTML = h;
}
function detailBlock() {
  let h = '';
  for (const t of S.traces) {
    if (t.algorithm === 'walk_drive_walk' && t.found) {
      h += `<p><b>${t.label}</b> 총 ${fmt(t.cost, 2)}분 · 도로 ${fmt(t.extra.distance_m)} m · 차량 구역 정점 ${t.extra.drive_nodes.length}개<br>` + t.segments.map(s => `${SEGMENT_NAME[s.kind]} ${fmt(s.meters)} m, ${fmt(s.minutes, 2)}분`).join(' → ') + '</p>';
    }
    if (t.algorithm === 'alt_uphill' && t.found) {
      const x = t.extra;
      h += `<p><b>${t.label}</b> 도로 ${fmt(x.distance_m)} m · 오르막 ${fmt(x.climb_m, 0)} m · 내리막 ${fmt(x.descent_m, 0)} m<br>` +
        `업힐 무시 최단 경로: ${fmt(x.shortest_distance_m)} m · 오르막 ${fmt(x.shortest_climb_m, 0)} m → ` +
        `오르막 ${fmt(x.shortest_climb_m - x.climb_m, 0)} m 줄이고 ${fmt(x.distance_m - x.shortest_distance_m)} m 더 걷기<br>` +
        t.segments.map(s => `${SEGMENT_NAME[s.kind]} ${fmt(s.meters, 0)} m${s.kind === 'uphill' ? ` (+${fmt(s.climb, 0)} m)` : ''}`).join(' → ') + '</p>';
    }
    if (t.algorithm === 'via' && t.extra.ranking) {
      h += `<table><tr><th>경유지 후보</th><th>총거리</th><th>출발→경유</th><th>경유→도착</th></tr>` + t.extra.ranking.map(r => `<tr><td>${r.node === t.extra.via ? '<b>' + r.node + ' (선택)</b>' : r.node}</td><td>${fmt(r.total)}</td><td>${fmt(r.from_start)}</td><td>${fmt(r.to_goal)}</td></tr>`).join('') + '</table>';
    }
    if (t.path.length && t.found) h += `<div class="pathlist"><b>${t.label}</b>: ${t.path.join(' → ')}</div>`;
  }
  $('detail').innerHTML = h;
}

/* ---- 전체 비교 ---- */
function compareTable() {
  if (!S.compare) { $('compareTable').innerHTML = ''; return; }
  const c = S.compare, mx = Math.max(1, ...c.results.map(r => r.expanded));
  let h = '<tr><th>알고리즘 / 휴리스틱</th><th>비용</th><th>상태</th><th>확장·방문</th><th>질의 ms</th><th>ALT 전처리 ms*</th><th>비용 / 최적</th></tr>';
  c.results.forEach((r, i) => {
    const usesAlt = ['alt', 'max'].includes(r.heuristic);
    h += `<tr class="click" data-i="${i}"><td>${r.label}<div class="mini"><i style="width:${(100 * r.expanded / mx).toFixed(1)}%"></i></div></td><td>${r.found ? fmt(r.cost, 2) : '—'}</td><td>${r.found ? '성공' : FAIL[r.reason]}</td><td>${r.expanded}</td><td>${r.median_ms.toFixed(3)}</td><td>${usesAlt ? c.alt_prep_ms.toFixed(2) : '—'}</td><td>${r.cost_ratio == null ? '—' : r.cost_ratio.toFixed(3)}</td></tr>`;
  });
  $('compareTable').innerHTML = h;
}
$('compareTable').onclick = e => {
  const tr = e.target.closest('tr[data-i]'); if (!tr) return;
  const r = S.compare.results[+tr.dataset.i], a = algo(r.algorithm);
  S.panels = [{ algorithm: r.algorithm, heuristic: r.heuristic || a.default_heuristic, weight: a.default_weight }];
  $('preset').value = 'custom'; panelRows(); runPanels({ jumpEnd: true });
};
$('compare').onclick = async () => {
  $('compare').disabled = true; setStatus('같은 출발·도착으로 비교 중…');
  try {
    S.compare = await api('/api/compare', { start: S.start, goal: S.goal, landmarks: S.landmarks, options: options(), repeats: 3 });
    compareTable(); setStatus('비교 완료 · 행을 클릭하면 경로를 표시합니다');
  } catch (e) { setStatus(e.message); } finally { $('compare').disabled = false; }
};
function download(name, obj) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$('exportCompare').onclick = () => { if (S.compare) download('path_lab_results.json', S.compare); else setStatus('먼저 전체 비교를 실행하세요'); };
$('saveTrace').onclick = () => download(`traces_${S.start}_${S.goal}.json`, S.traces);
$('saveRoute').onclick = () => download(`route_${S.start}_${S.goal}.json`, S.traces.map(t => ({
  algorithm: t.label, start: t.start, end: t.goal, found: t.found, cost: t.cost, cost_unit: t.cost_unit, path: t.path,
  edge_rows: t.edge_ids.map(e => e + 1),
  segments: t.edge_ids.map(e => ({ edge_row: e + 1, source: World.roads[e].u, target: World.roads[e].v, weight_m: World.roads[e].w })),
})));
$('loadTrace').onchange = async e => {
  try {
    const loaded = (await Promise.all([...e.target.files].map(async f => JSON.parse(await f.text())))).flat();
    const fixed = loaded.map(t => ({ label: t.label || `w=${t.weight}`, edge_ids: [], segments: [], extra: {}, cost_unit: 'm', found: t.cost != null, ...t, steps: t.steps.map(s => ({ pushed: [], ...s })) }));
    if (!fixed.every(t => World.nodes.has(t.start) && World.nodes.has(t.goal))) throw Error('이 그래프에 없는 정점이 들어 있는 Trace입니다.');
    S.fromFile = true; S.token++;
    S.start = fixed[0].start; S.goal = fixed[0].goal; $('start').value = S.start; $('goal').value = S.goal;
    showTraces(fixed, false); refreshHeuristicTable(); setStatus(`Trace ${fixed.length}개를 불러왔습니다 (탐색을 다시 돌리지 않음)`);
  } catch (err) { setStatus('불러오기 실패: ' + err.message); }
  e.target.value = '';
};

/* ---- 재생 ---- */
function syncClock() {
  $('scrub').value = S.k; $('clock').textContent = `${Math.min(S.k, S.maxK)} / ${S.maxK}`;
  $('play').textContent = S.playing ? '일시정지' : S.k >= S.maxK ? '다시' : '재생';
  G.redraw();
}
const speedOf = () => Math.round(2 * 10 ** (+$('speed').value / 100 * 3));
function tick(ts) {
  const dt = Math.min((ts - S.last) / 1000, 0.1); S.last = ts;
  if (S.playing) {
    S.credit += dt * speedOf();
    const n = Math.floor(S.credit);
    if (n) { S.credit -= n; S.k = Math.min(S.k + n, S.maxK); if (S.k >= S.maxK) S.playing = false; syncClock(); }
  }
  renderAllViews();
  requestAnimationFrame(tick);
}
$('play').onclick = () => { if (S.k >= S.maxK) S.k = 0; S.playing = !S.playing; syncClock(); };
$('step').onclick = () => { S.playing = false; S.k = Math.min(S.k + 1, S.maxK); syncClock(); };
$('reset').onclick = () => { S.playing = false; S.k = 0; syncClock(); };
$('finish').onclick = () => { S.playing = false; S.k = S.maxK; syncClock(); };
$('scrub').oninput = () => { S.playing = false; S.k = +$('scrub').value; syncClock(); };
$('speed').oninput = () => { $('speedVal').textContent = speedOf() + '/초'; };

/* ---- 나머지 입력 ---- */
$('clickMode').onclick = e => { const b = e.target.closest('button'); if (b) setMode(b.dataset.v); };
const idInput = () => {
  const s = +$('start').value, t = +$('goal').value;
  if (!World.nodes.has(s) || !World.nodes.has(t)) { setStatus('존재하는 Node ID를 입력하세요'); return; }
  setEndpoints(s, t);
};
$('start').onchange = idInput; $('goal').onchange = idInput;
$('applyCoord').onclick = () => {
  const limit = +$('maxSnap').value || 500, next = { start: S.start, goal: S.goal }, snap = { ...S.snap };
  try {
    for (const [key, field, label] of [['start', 'startCoord', '출발'], ['goal', 'goalCoord', '도착']]) {
      const text = $(field).value.trim();
      if (!text) continue;
      const [lat, lon] = text.split(/[\s,]+/).map(Number);
      if (!(lat > -90 && lat < 90 && lon >= -180 && lon <= 180)) throw Error('좌표는 위도, 경도 순서로 입력하세요.');
      let best = null, bd = Infinity;
      for (const n of World.nodes.values()) { const d = haversine(n, { lat, lon }); if (d < bd) { bd = d; best = n.id; } }
      if (bd > limit) throw Error(`${label} 좌표에서 가장 가까운 도로 정점까지 ${bd.toFixed(1)} m입니다. 최대 연결 거리를 조정하세요.`);
      next[key] = best; snap[key] = { lat, lon, dist: bd };
    }
    S.snap = snap;
    setEndpoints(next.start, next.goal, true);
    setTimeout(() => setStatus(['start', 'goal'].filter(k => snap[k]).map(k => `${k === 'start' ? '출발' : '도착'} 좌표 → 정점 ${next[k]} (${snap[k].dist.toFixed(1)} m, 경로 합계에는 포함 안 함)`).join(' · ')), 400);
  } catch (e) { setStatus(e.message); }
};
$('preset').onchange = () => { S.panels = PRESETS[$('preset').value](); panelRows(); runPanels(); };
$('addPanel').onclick = () => { S.panels.push({ algorithm: 'astar', heuristic: 'haversine', weight: 1 }); panelsChanged(); };
for (const id of ['beamWidth', 'budget', 'seed', 'noRevisit', 'walkKmh', 'driveKmh', 'driveRule']) $(id).onchange = () => runPanels();
$('driveRadius').onchange = () => { G.changed(); runPanels(); };
$('waypoints').onchange = () => { S.waypoints = $('waypoints').value.trim().split(/[\s,]+/).filter(Boolean).map(Number); runPanels(); };
$('zoneReset').onclick = () => { S.zone = { lat: World.meta.center[0], lon: World.meta.center[1] }; G.changed(); runPanels(); };
$('k').oninput = () => { $('kVal').textContent = $('k').value; };
$('k').onchange = () => pickLandmarks(0);
$('lmMethod').onchange = () => pickLandmarks(0);
$('rePick').onclick = () => pickLandmarks(Math.floor(Math.random() * 1e9));
document.querySelectorAll('[data-layer]').forEach(c => { c.onchange = () => { Layers[c.dataset.layer] = c.checked; for (const g of groups) g.changed(); }; });
$('tileOpacity').oninput = () => { Layers.tileOpacity = +$('tileOpacity').value / 100; for (const g of groups) g.changed(); };
$('fit').onclick = () => G.fit();
$('theme').onclick = () => {
  const root = document.documentElement, dark = root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  root.dataset.theme = dark ? 'light' : 'dark';
  for (const g of groups) g.changed();
  if (window.Analysis) Analysis.redraw();
};
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { for (const g of groups) g.changed(); if (window.Analysis) Analysis.redraw(); });
$('tabs').onclick = e => {
  const b = e.target.closest('button'); if (!b) return;
  document.querySelectorAll('#tabs button').forEach(x => x.setAttribute('aria-pressed', x === b));
  for (const t of ['search', 'analysis']) $('tab-' + t).hidden = t !== b.dataset.tab;
  if (b.dataset.tab === 'analysis') Analysis.open();
};

async function init() {
  let meta;
  try { meta = await api('/api/meta'); }
  catch (e) { setStatus('서버에 연결할 수 없습니다. python main.py 로 실행한 뒤 표시되는 주소로 접속하세요.'); return; }
  loadWorld(meta);
  S.zone = { lat: meta.center[0], lon: meta.center[1] };
  $('counts').textContent = `${meta.stats.nodes.toLocaleString()}개 정점 · ${meta.stats.csv_rows.toLocaleString()}개 방향 간선` + (meta.network_type ? ` · ${meta.network_type}` : '');
  for (const n of meta.nodes) { const o = document.createElement('option'); o.value = n[0]; $('ids').append(o); }
  S.start = meta.default_start; S.goal = meta.default_goal; $('start').value = S.start; $('goal').value = S.goal;
  $('speedVal').textContent = speedOf() + '/초';
  panelRows();
  requestAnimationFrame(ts => { S.last = ts; tick(ts); });
  await pickLandmarks(0);
  Analysis.init();
}
addEventListener('load', init);
