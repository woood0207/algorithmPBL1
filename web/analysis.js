/* 통계 분석 탭 (repbl/greedy_analysis 기반). 실험은 서버의 analysis.py가 돌린다. */
const Analysis = (() => {
  const COL = { dijkstra: '#8b93a7', greedy: '#f59e0b', beam: '#3b82f6', bfs: '#a855f7', local: '#10b981', random: '#ef4444', astar: '#0891b2', bidirectional: '#db2777' };
  const NAME = { dijkstra: 'Dijkstra(최적)', greedy: 'Greedy', beam: 'Beam', local: 'Local(hill-climb)', random: 'Random walk', bfs: 'BFS(홉 최소)' };
  const ALG = ['greedy', 'beam', 'bfs', 'local', 'random'];
  const MODES = ['off', 'both', 'nodes', 'path'], ML = { off: '끔', both: '경로+탐색', nodes: '탐색만', path: '경로만' };
  const vis = { dijkstra: 'path', greedy: 'path', beam: 'path', bfs: 'path', local: 'path', random: 'off' };
  const g2 = new ViewGroup(); g2.needsFit = true;
  let view = null, opened = false, polling = false, data = null, cfg = null, routes = {}, sel = null, pick = null;

  const f = (x, d = 2) => x == null ? '-' : x.toFixed(d), P = x => (x * 100).toFixed(1) + '%';
  const pct = (xs, q) => { if (!xs.length) return null; xs = [...xs].sort((a, b) => a - b); const i = (xs.length - 1) * q, l = Math.floor(i), h = Math.ceil(i); return xs[l] + (xs[h] - xs[l]) * (i - l); };

  function opts() {
    return { pairs: +$('np').value, bins: +$('nb').value, beam_width: +$('bw').value, budget: +$('bud').value, walk_seeds: +$('rs').value, seed: +$('sseed').value, algos: ALG.filter(a => $('ck_' + a).checked) };
  }
  async function run() {
    if (polling) return;
    const o = opts();
    if (!o.algos.length) { $('prog').textContent = '  알고리즘을 하나 이상 고르세요'; return; }
    cfg = o; polling = true; $('run').disabled = true;
    try {
      await api('/api/analysis', o);
      for (;;) {
        const st = await api('/api/analysis');
        $('prog').textContent = `  ${st.done}/${st.total} 쌍 처리 중…`;
        if (st.state === 'error') throw Error(st.error);
        if (st.state === 'done') { data = st.result; break; }
        await new Promise(r => setTimeout(r, 400));
      }
      $('prog').textContent = `  완료 · 쌍 ${data.pairs.length}개, 실행 ${data.rows.length}회`;
      render();
      if (!sel && data.pairs.length) setSel(...data.pairs[0]);
    } catch (e) { $('prog').textContent = '  오류: ' + e.message; } finally { polling = false; $('run').disabled = false; }
  }
  function render() {
    if (!data) return;
    const algs = cfg.algos;
    let h = '<tr><th>알고리즘</th><th>시도</th><th>도달률</th><th>최적률 (95% CI)</th><th>평균 비용비</th><th>p90</th><th>확장수비</th><th>속도배</th><th>ρ(거리,최적)</th></tr>';
    for (const a of algs) {
      const v = data.overall.find(x => x.algo === a); if (!v) continue;
      h += `<tr><td><span class="dot big" style="background:${COL[a]}"></span>${NAME[a]}</td><td>${v.n}</td><td>${P(v.success)}</td><td>${P(v.optimal)} [${P(v.optimal_ci[0])}, ${P(v.optimal_ci[1])}]</td><td>${f(v.ratio_mean, 3)}</td><td>${f(v.ratio_p90, 3)}</td><td>${f(v.expanded_ratio)}</td><td>${f(v.speedup)}</td><td>${f(v.rho_dist_vs_optimal)}</td></tr>`;
    }
    $('ov').innerHTML = h;
    const mk = (k, ci) => algs.map(a => ({ n: NAME[a], c: COL[a], p: data.by_bin.filter(b => b.algo === a && b[k] != null).map(b => [(b.lo + b.hi) / 2, b[k], ci ? b.optimal_ci[0] : null, ci ? b.optimal_ci[1] : null]) }));
    chart($('c1'), { title: '최적해 발견 확률 P(최적)', y0: 0, y1: 1, series: mk('optimal', 1) });
    chart($('c2'), { title: '도착 성공률 (수렴률)', y0: 0, y1: 1, series: mk('success') });
    chart($('c3'), { title: '평균 비용비 (도착한 경우, 1.0=최적)', ref: 1, yc: 2.5, series: mk('ratio_mean').filter(x => x.p.some(p => p[1] <= 2.5)) });
    chart($('c4'), { title: '확장 노드 ÷ Dijkstra (낮을수록 이득)', ref: 1, y0: 0, series: mk('expanded_ratio') });
    const keep = $('sa').value;
    $('sa').innerHTML = algs.map(a => `<option value="${a}">${NAME[a]}</option>`).join('');
    $('sa').value = algs.includes(keep) ? keep : algs.includes('greedy') ? 'greedy' : algs[0];
    scatter();
  }
  function chart(cv, o) {
    const dpr = devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
    if (!W) return;
    cv.width = W * dpr; cv.height = H * dpr;
    const g = cv.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const mu = cssVar('--muted'), ln = cssVar('--line'), fg = cssVar('--text');
    const L = 46, R = 12, T = 44, B = 36, pts = o.series.flatMap(s => s.p);
    if (!pts.length) return;
    let x0 = Math.min(...pts.map(p => p[0])), x1 = Math.max(...pts.map(p => p[0])); if (x0 === x1) { x0 -= 1; x1 += 1; }
    let y0 = o.y0 ?? Math.min(1, ...pts.map(p => p[1])), y1 = o.y1 ?? Math.max(...pts.map(p => p[3] ?? p[1]));
    if (o.yc) y1 = Math.min(y1, o.yc);
    if (o.ref != null && y1 < o.ref) y1 = o.ref;
    y1 += (y1 - y0) * 0.05;
    const X = v => L + (v - x0) / (x1 - x0) * (W - L - R), Y = v => H - B - (Math.min(v, y1) - y0) / (y1 - y0) * (H - B - T);
    g.fillStyle = fg; g.font = '600 12px system-ui,sans-serif'; g.fillText(o.title, L, 16); g.font = '11px system-ui,sans-serif';
    for (let i = 0; i <= 4; i++) { const v = y0 + (y1 - y0) * i / 4, y = Y(v); g.strokeStyle = ln; g.beginPath(); g.moveTo(L, y); g.lineTo(W - R, y); g.stroke(); g.fillStyle = mu; g.textAlign = 'right'; g.fillText(v.toFixed(v < 10 ? 2 : 0), L - 5, y + 4); }
    g.textAlign = 'center';
    for (let i = 0; i <= 4; i++) { const v = x0 + (x1 - x0) * i / 4; g.fillStyle = mu; g.fillText(Math.round(v), X(v), H - B + 14); }
    g.fillText('최적 경로 길이 (m)', (L + W - R) / 2, H - 6); g.textAlign = 'left';
    if (o.ref != null) { g.setLineDash([4, 4]); g.strokeStyle = mu; g.beginPath(); g.moveTo(L, Y(o.ref)); g.lineTo(W - R, Y(o.ref)); g.stroke(); g.setLineDash([]); }
    const hits = [];
    for (const s of o.series) {
      g.strokeStyle = s.c; g.fillStyle = s.c;
      if (o.dots) { g.globalAlpha = 0.4; for (const p of s.p) { g.beginPath(); g.arc(X(p[0]), Y(p[1]), 2.6, 0, 7); g.fill(); hits.push([X(p[0]), Y(p[1]), p[2]]); } g.globalAlpha = 1; continue; }
      g.lineWidth = 2; g.beginPath(); s.p.forEach((p, i) => i ? g.lineTo(X(p[0]), Y(p[1])) : g.moveTo(X(p[0]), Y(p[1]))); g.stroke(); g.lineWidth = 1;
      for (const p of s.p) { if (p[2] != null) { g.beginPath(); g.moveTo(X(p[0]), Y(p[2])); g.lineTo(X(p[0]), Y(p[3])); g.stroke(); } g.beginPath(); g.arc(X(p[0]), Y(p[1]), 3.5, 0, 7); g.fill(); }
    }
    if (!o.dots) { let lx = W - R; g.textAlign = 'right'; for (const s of [...o.series].reverse()) { g.fillStyle = mu; g.fillText(s.n, lx, 34); const w = g.measureText(s.n).width; g.fillStyle = s.c; g.fillRect(lx - w - 12, 26, 8, 8); lx -= w + 22; } g.textAlign = 'left'; }
    cv._hits = hits;
  }
  function scatter() {
    const a = $('sa').value; if (!a || !data) return;
    const rs = data.rows.filter(r => r.algo === a && r.cost_ratio != null);
    chart($('c5'), { title: `${NAME[a]}: 쌍별 비용비 (1.0=최적)`, ref: 1, y0: 0.95, yc: Math.min(3, Math.max(1.3, pct(rs.map(r => r.cost_ratio), 0.98) || 1.3)), dots: 1, series: [{ n: a, c: COL[a], p: rs.map(r => [r.opt_cost, r.cost_ratio, r]) }] });
  }

  /* 지도에서 경로 비교 */
  async function setSel(s, t) {
    sel = [s, t];
    const o = cfg || opts();
    const panels = ['dijkstra', ...ALG].map(a => ({ algorithm: a, heuristic: a === 'bfs' || a === 'random' || a === 'dijkstra' ? undefined : 'geo' }));
    const res = await api('/api/run', { start: s, goal: t, options: { beam_width: o.beam_width, budget: o.budget, seed: 0 }, panels });
    routes = Object.fromEntries(res.traces.map(x => [x.algorithm, x]));
    g2.redraw(); table();
  }
  function overlay(v, c) {
    const L = ['dijkstra', ...ALG].filter(a => routes[a] && vis[a] !== 'off');
    const hasP = a => vis[a] === 'both' || vis[a] === 'path', hasN = a => vis[a] === 'both' || vis[a] === 'nodes';
    const uniq = a => new Set(routes[a].steps.map(s => s.node));
    const big = !L.some(hasP), X = L.filter(hasN).sort((a, b) => uniq(b).size - uniq(a).size);
    for (const a of X) { c.fillStyle = COL[a]; c.globalAlpha = X.length > 1 ? 0.8 : 0.7; for (const id of uniq(a)) { const [x, y] = v.px(id); c.beginPath(); c.arc(x, y, big ? 4 : 2.8, 0, 7); c.fill(); } }
    c.globalAlpha = 1;
    for (const a of L.filter(hasP)) {
      const r = routes[a]; if (!r.edge_ids.length) continue;
      c.globalAlpha = a === 'dijkstra' ? 0.5 : 0.95;
      v.strokeRoads(c, r.edge_ids, COL[a], a === 'dijkstra' ? 8 : 3.5, r.found ? null : [6, 5]);
      c.globalAlpha = 1;
    }
    if (sel) [[sel[0], '출발'], [sel[1], '도착']].forEach(([id, l], i) => {
      const [x, y] = v.px(id); c.fillStyle = i ? '#ef4444' : '#22c55e'; c.beginPath(); c.arc(x, y, 7, 0, 7); c.fill();
      c.fillStyle = cssVar('--text'); c.font = '600 12px system-ui'; c.fillText(l, x + 10, y - 8);
    });
  }
  function table() {
    const b = routes.dijkstra;
    let h = '<tr><th>알고리즘</th><th>도착</th><th>비용(m)</th><th>비용비</th><th>확장 노드</th><th>확장수비</th></tr>';
    for (const a of ['dijkstra', ...ALG]) { const r = routes[a]; if (!r) continue; h += `<tr><td><span class="dot big" style="background:${COL[a]}"></span>${NAME[a]}</td><td>${r.found ? '✓' : '✗'}</td><td>${r.found ? r.cost.toFixed(0) : '-'}</td><td>${r.found && b.found ? (r.cost / b.cost).toFixed(3) : '-'}</td><td>${r.steps.length}</td><td>${(r.steps.length / b.steps.length).toFixed(2)}</td></tr>`; }
    $('rt').innerHTML = h;
  }
  function chips() { $('vis').innerHTML = ['dijkstra', ...ALG].map(a => `<button class="chip ${vis[a] === 'off' ? '' : 'on'}" data-a="${a}" style="--c:${COL[a]}"><span class="dot" style="background:${COL[a]}"></span> ${NAME[a]} · ${ML[vis[a]]}</button>`).join(''); }
  function onClick(v, x, y) {
    const id = v.nearestNode(x, y, 30); if (id == null) return;
    if (pick == null) { pick = id; sel = [id, id]; routes = {}; g2.redraw(); $('pickHint').textContent = '도착 지점을 클릭하세요'; }
    else { const s = pick; pick = null; $('pickHint').textContent = '지도에서 두 지점을 클릭하세요 (출발 → 도착)'; if (s !== id) setSel(s, id); }
  }

  function init() {
    $('algs').innerHTML = ALG.map(a => `<label class="check"><input type="checkbox" id="ck_${a}" checked><span class="dot" style="background:${COL[a]}"></span>${NAME[a]}</label>`).join(' ');
    chips();
    $('vis').onclick = e => { const b = e.target.closest('button'); if (!b) return; const a = b.dataset.a; vis[a] = MODES[(MODES.indexOf(vis[a]) + 1) % 4]; chips(); g2.redraw(); };
    $('run').onclick = run;
    $('sa').onchange = scatter;
    $('c5').onclick = e => {
      const r = e.target.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top; let best = null, bd = 14 * 14;
      for (const h of e.target._hits || []) { const d = (h[0] - x) ** 2 + (h[1] - y) ** 2; if (d < bd) { bd = d; best = h[2]; } }
      if (best) { setSel(best.start, best.goal); $('mapcard').scrollIntoView({ behavior: 'smooth' }); }
    };
    $('rp').onclick = () => {
      const m = World.meta.main_nodes; let s, t;
      do { s = m[Math.floor(Math.random() * m.length)]; t = m[Math.floor(Math.random() * m.length)]; } while (s === t);
      setSel(s, t);
    };
    addEventListener('resize', render);
  }
  function open() {
    if (!view) view = new MapView($('amap'), g2, { overlay, click: onClick });
    if (!opened) { opened = true; run(); } else render();
  }
  return { init, open, redraw: render };
})();
window.Analysis = Analysis;
