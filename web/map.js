/* 지도 렌더링: OSM 타일 + 도로 곡선 + 패널끼리 공유하는 이동·확대 */
const Layers = { tiles: true, tileOpacity: 0.45, vertices: true, labels: false, boundary: true, frontier: true, tree: true, relaxed: false, heat: false };
const World = { nodes: new Map(), roads: [], meta: null };
const tileCache = new Map();
const groups = [];

function mercator(lat, lon) {
  const s = Math.sin(lat * Math.PI / 180);
  return [(lon + 180) / 360, 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)];
}
function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function haversine(a, b) {
  const R = 6371008.8, rad = Math.PI / 180, dp = (b.lat - a.lat) * rad, dl = (b.lon - a.lon) * rad;
  const q = Math.sin(dp / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dl / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(q)));
}

function loadWorld(meta) {
  World.meta = meta;
  for (const [id, lat, lon] of meta.nodes) {
    const [wx, wy] = mercator(lat, lon);
    World.nodes.set(id, { id, lat, lon, wx, wy });
  }
  World.roads = meta.roads.map(([id, u, v, w, name, highway, coords, length]) => {
    const pts = new Float64Array(coords.length * 2);
    coords.forEach(([lat, lon], i) => { const [x, y] = mercator(lat, lon); pts[2 * i] = x; pts[2 * i + 1] = y; });
    return { id, u, v, w, name, highway, pts, length: length ?? w };
  });
  World.edgeOf = new Map();
  for (const r of World.roads) {
    const key = r.u + ',' + r.v;
    if (r.u !== r.v && (!World.edgeOf.has(key) || World.edgeOf.get(key).w > r.w)) World.edgeOf.set(key, r);
  }
}

class ViewGroup {
  constructor() { this.cx = 0.5; this.cy = 0.5; this.z = 14; this.views = new Set(); groups.push(this); }
  changed() { for (const v of this.views) { v.baseDirty = true; v.dirty = true; } }
  redraw() { for (const v of this.views) v.dirty = true; }
  fit() {
    const v = [...this.views][0];
    if (!v || !v.W) return;
    let a = Infinity, b = -Infinity, c = Infinity, d = -Infinity;
    for (const n of World.nodes.values()) { a = Math.min(a, n.wx); b = Math.max(b, n.wx); c = Math.min(c, n.wy); d = Math.max(d, n.wy); }
    this.cx = (a + b) / 2; this.cy = (c + d) / 2;
    this.z = Math.log2(Math.min((v.W - 40) / ((b - a) * 256), (v.H - 40) / ((d - c) * 256)));
    this.changed();
  }
}

class MapView {
  constructor(holder, group, hooks) {
    this.holder = holder; this.group = group; this.hooks = hooks || {};
    this.canvas = document.createElement('canvas');
    this.ctx = this.canvas.getContext('2d');
    this.base = document.createElement('canvas');
    holder.append(this.canvas);
    this.W = 0; this.H = 0; this.dirty = true; this.baseDirty = true;
    group.views.add(this);
    this.bind();
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(holder);
  }
  destroy() { this.ro.disconnect(); this.group.views.delete(this); this.canvas.remove(); }
  resize() {
    const r = this.holder.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const first = !this.W;
    this.W = r.width; this.H = r.height; this.dpr = devicePixelRatio || 1;
    for (const c of [this.canvas, this.base]) { c.width = Math.round(this.W * this.dpr); c.height = Math.round(this.H * this.dpr); }
    this.baseDirty = this.dirty = true;
    if (first && this.group.needsFit) { this.group.needsFit = false; this.group.fit(); }
  }
  get scale() { return 256 * 2 ** this.group.z; }
  X(wx) { return (wx - this.group.cx) * this.scale + this.W / 2; }
  Y(wy) { return (wy - this.group.cy) * this.scale + this.H / 2; }
  px(id) { const n = World.nodes.get(id); return [this.X(n.wx), this.Y(n.wy)]; }

  bind() {
    const c = this.canvas; let drag = null;
    const pos = e => { const r = c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    c.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY, cx: this.group.cx, cy: this.group.cy, moved: false }; c.setPointerCapture(e.pointerId); });
    c.addEventListener('pointermove', e => {
      if (drag) {
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        if (Math.hypot(dx, dy) > 4) drag.moved = true;
        if (drag.moved) { document.getElementById('tip').hidden = true; this.group.cx =drag.cx - dx / this.scale; this.group.cy = drag.cy - dy / this.scale; this.group.changed(); return; }
      }
      if (this.hooks.hover) this.hooks.hover(this, ...pos(e), e);
    });
    c.addEventListener('pointerup', e => { if (drag && !drag.moved && this.hooks.click) this.hooks.click(this, ...pos(e)); drag = null; });
    c.addEventListener('pointercancel', () => { drag = null; });
    c.addEventListener('pointerleave', () => { if (this.hooks.leave) this.hooks.leave(this); });
    c.addEventListener('wheel', e => {
      e.preventDefault();
      const [x, y] = pos(e), g = this.group;
      const wx = g.cx + (x - this.W / 2) / this.scale, wy = g.cy + (y - this.H / 2) / this.scale;
      g.z = Math.max(10, Math.min(21, g.z - e.deltaY * 0.002));
      g.cx = wx - (x - this.W / 2) / this.scale; g.cy = wy - (y - this.H / 2) / this.scale;
      g.changed();
    }, { passive: false });
  }

  nearestNode(x, y, maxPx = 14) {
    let best = null, bd = maxPx * maxPx;
    for (const n of World.nodes.values()) {
      const dx = this.X(n.wx) - x, dy = this.Y(n.wy) - y, d = dx * dx + dy * dy;
      if (d < bd) { bd = d; best = n.id; }
    }
    return best;
  }
  nearestRoad(x, y, maxPx = 7) {
    let best = null, bd = maxPx * maxPx;
    for (const r of World.roads) {
      for (let i = 0; i + 3 < r.pts.length; i += 2) {
        const ax = this.X(r.pts[i]), ay = this.Y(r.pts[i + 1]), bx = this.X(r.pts[i + 2]), by = this.Y(r.pts[i + 3]);
        const l = (bx - ax) ** 2 + (by - ay) ** 2, t = l ? Math.max(0, Math.min(1, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / l)) : 0;
        const d = (ax + t * (bx - ax) - x) ** 2 + (ay + t * (by - ay) - y) ** 2;
        if (d < bd) { bd = d; best = r; }
      }
    }
    return best;
  }

  drawTiles(b) {
    const g = this.group, zt = Math.max(0, Math.min(19, Math.round(g.z))), n = 2 ** zt;
    const size = 256 * 2 ** (g.z - zt), left = g.cx - this.W / 2 / this.scale, top = g.cy - this.H / 2 / this.scale;
    const x0 = Math.floor(left * n), y0 = Math.floor(top * n);
    const x1 = Math.floor((g.cx + this.W / 2 / this.scale) * n), y1 = Math.floor((g.cy + this.H / 2 / this.scale) * n);
    b.globalAlpha = Layers.tileOpacity;
    for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) {
      if (y < 0 || y >= n) continue;
      const key = `${zt}/${((x % n) + n) % n}/${y}`;
      let img = tileCache.get(key);
      if (!img) {
        img = new Image();
        img.onload = () => { for (const gr of groups) gr.changed(); };
        img.src = `https://tile.openstreetmap.org/${key}.png`;
        tileCache.set(key, img);
      }
      if (img.complete && img.naturalWidth) b.drawImage(img, Math.floor((x / n - left) * this.scale), Math.floor((y / n - top) * this.scale), Math.ceil(size) + 1, Math.ceil(size) + 1);
    }
    b.globalAlpha = 1;
  }

  drawBase() {
    const b = this.base.getContext('2d');
    b.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    b.fillStyle = cssVar('--map-bg'); b.fillRect(0, 0, this.W, this.H);
    if (Layers.tiles) this.drawTiles(b);
    b.lineCap = 'round'; b.lineJoin = 'round';
    b.strokeStyle = cssVar('--road'); b.globalAlpha = 0.7; b.lineWidth = 1.2; b.beginPath();
    for (const r of World.roads) if (r.u < r.v) this.tracePts(b, r);
    b.stroke(); b.globalAlpha = 1;
    if (this.hooks.base) this.hooks.base(this, b);
    const m = World.meta;
    if (Layers.boundary && m.radius) this.circle(b, m.center, m.radius, '#2563eb');
    if (Layers.vertices) {
      b.fillStyle = cssVar('--road');
      for (const n of World.nodes.values()) { b.beginPath(); b.arc(this.X(n.wx), this.Y(n.wy), 1.4, 0, 7); b.fill(); }
    }
    if (Layers.labels) {
      b.font = '600 10px system-ui';
      for (const n of World.nodes.values()) {
        const x = this.X(n.wx) + 4, y = this.Y(n.wy) - 4;
        if (x < -30 || x > this.W || y < 0 || y > this.H + 12) continue;
        b.fillStyle = 'rgba(255,255,255,.85)'; b.fillRect(x - 1, y - 10, b.measureText(n.id).width + 3, 12);
        b.fillStyle = '#17283d'; b.fillText(n.id, x, y);
      }
    }
    // 축척과 저작권 표시
    const lat = m.center[0], metersPerPx = 40075016.7 * Math.cos(lat * Math.PI / 180) / this.scale;
    const target = 90 * metersPerPx; let meters = 10 ** Math.floor(Math.log10(target));
    if (target / meters >= 5) meters *= 5; else if (target / meters >= 2) meters *= 2;
    const len = meters / metersPerPx, text = cssVar('--text');
    b.strokeStyle = text; b.lineWidth = 2; b.beginPath(); b.moveTo(12, this.H - 14); b.lineTo(12 + len, this.H - 14); b.stroke();
    b.font = '11px system-ui'; b.fillStyle = text; b.fillText(`${meters} m`, 12, this.H - 20);
    if (Layers.tiles) {
      const s = '© OpenStreetMap contributors', w = b.measureText(s).width;
      b.fillStyle = 'rgba(255,255,255,.75)'; b.fillRect(this.W - w - 10, this.H - 17, w + 10, 17);
      b.fillStyle = '#333'; b.fillText(s, this.W - w - 5, this.H - 5);
    }
    this.baseDirty = false;
  }

  tracePts(c, r) {
    for (let i = 0; i < r.pts.length; i += 2) {
      const x = this.X(r.pts[i]), y = this.Y(r.pts[i + 1]);
      if (i) c.lineTo(x, y); else c.moveTo(x, y);
    }
  }
  circle(c, center, radius, color) {
    const [wx, wy] = mercator(center[0], center[1]);
    const r = radius / (40075016.7 * Math.cos(center[0] * Math.PI / 180)) * this.scale;
    c.save(); c.strokeStyle = color; c.lineWidth = 1.2; c.setLineDash([5, 5]); c.beginPath(); c.arc(this.X(wx), this.Y(wy), r, 0, 7); c.stroke(); c.restore();
  }
  strokeRoads(c, ids, color, width, dash) {
    c.save(); c.strokeStyle = color; c.lineWidth = width; c.lineCap = 'round'; c.lineJoin = 'round';
    if (dash) c.setLineDash(dash);
    c.beginPath();
    for (const id of ids) this.tracePts(c, World.roads[id]);
    c.stroke(); c.restore();
  }
  render() {
    if (!this.W || !this.dirty) return;
    if (this.baseDirty) this.drawBase();
    const c = this.ctx;
    c.setTransform(1, 0, 0, 1, 0, 0); c.drawImage(this.base, 0, 0);
    c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    if (this.hooks.overlay) this.hooks.overlay(this, c);
    this.dirty = false;
  }
}

function renderAllViews() { for (const g of groups) for (const v of g.views) v.render(); }
