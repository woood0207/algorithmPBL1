/* 오프라인 배경 지도: localOSM/map_data.js(window.LOCAL_OSM, redduk)의 GeoJSON을 캔버스에 직접 그린다.
   인터넷 타일 없이 도로·물·녹지·건물·지명을 보여 준다. 탐색 그래프와는 상관없는 배경일 뿐이다. */
const Offline = { state: 'idle', layers: null, places: [] };

const OFFLINE_STYLE = {
  water: { fill: '#aad3df', stroke: '#8cc0d6' },
  green: { fill: '#cfe5bd' },
  land: { fill: '#ece9e0' },
  building: { fill: '#d9d0c9', stroke: '#c4b8ae' },
  roads: [   // [최소 줌, 굵기(px), 색, highway 값]
    [10, 3.2, '#f7c873', ['motorway', 'motorway_link', 'trunk', 'trunk_link']],
    [11, 2.6, '#fbe3a3', ['primary', 'primary_link', 'secondary', 'secondary_link']],
    [13, 1.8, '#ffffff', ['tertiary', 'tertiary_link', 'unclassified', 'residential', 'living_street']],
    [15, 1.1, '#ffffff', ['service', 'pedestrian', 'track']],
    [16, 0.9, '#f2a39a', ['footway', 'path', 'steps', 'cycleway', 'corridor']],
  ],
};
const GREEN = new Set(['forest', 'wood', 'park', 'grass', 'meadow', 'scrub', 'farmland', 'orchard', 'garden', 'golf_course', 'recreation_ground', 'cemetery']);

function loadOfflineMap() {
  if (Offline.state !== 'idle') return;
  Offline.state = 'loading';
  const s = document.createElement('script');
  s.src = '/offline/map_data.js';
  s.onload = () => {
    try { buildOffline(window.LOCAL_OSM); Offline.state = 'ready'; window.LOCAL_OSM = null; }
    catch (e) { Offline.state = 'error'; console.error(e); }
    for (const g of groups) g.changed();
  };
  s.onerror = () => { Offline.state = 'error'; for (const g of groups) g.changed(); };
  document.head.append(s);
}

function buildOffline(d) {
  const ring = coords => {   // [[lon, lat], ...] -> Float64Array 세계 좌표 + bbox
    const pts = new Float64Array(coords.length * 2);
    let a = Infinity, b = -Infinity, c = Infinity, e = -Infinity;
    coords.forEach(([lon, lat], i) => {
      const [x, y] = mercator(lat, lon); pts[2 * i] = x; pts[2 * i + 1] = y;
      a = Math.min(a, x); b = Math.max(b, x); c = Math.min(c, y); e = Math.max(e, y);
    });
    return { pts, box: [a, b, c, e] };
  };
  const shape = (geom) => {   // 한 도형 = 고리 여러 개 (구멍은 evenodd로 처리)
    const rings = geom.type === 'LineString' ? [geom.coordinates]
      : geom.type === 'Polygon' ? geom.coordinates
      : geom.type === 'MultiPolygon' ? geom.coordinates.flat() : [];
    const parts = rings.map(ring);
    const box = parts.reduce((m, p) => [Math.min(m[0], p.box[0]), Math.max(m[1], p.box[1]), Math.min(m[2], p.box[2]), Math.max(m[3], p.box[3])], [Infinity, -Infinity, Infinity, -Infinity]);
    return { parts, box };
  };
  const L = { water: [], waterLine: [], green: [], land: [], building: [], roads: OFFLINE_STYLE.roads.map(() => []) };
  const roadClass = new Map();
  OFFLINE_STYLE.roads.forEach(([, , , kinds], i) => kinds.forEach(k => roadClass.set(k, i)));
  for (const f of d.geojson.features) {
    const p = f.properties, g = f.geometry;
    if (p.kind === 'water') (g.type === 'LineString' ? L.waterLine : L.water).push(shape(g));
    else if (p.kind === 'land') (GREEN.has(p.landuse || p.leisure || p.natural) ? L.green : L.land).push(shape(g));
    else if (p.kind === 'road' && g.type === 'LineString') {
      const i = roadClass.get(p.highway);
      if (i !== undefined) L.roads[i].push(shape(g));
    }
  }
  for (const f of (d.building_footprints || {}).features || []) L.building.push(shape(f.geometry));
  Offline.layers = L;
  const rank = { city: 0, town: 1, borough: 2, quarter: 3, village: 3, square: 4, locality: 4, hamlet: 5 };
  Offline.places = (d.places || []).map(p => {
    const [wx, wy] = mercator(p.coordinate[0], p.coordinate[1]);
    return { wx, wy, name: p.name_local || p.name, rank: rank[p.place] ?? 5 };
  }).sort((a, b) => a.rank - b.rank);
}

function drawOffline(view, b) {
  const L = Offline.layers, g = view.group;
  const view_box = [g.cx - view.W / 2 / view.scale, g.cx + view.W / 2 / view.scale, g.cy - view.H / 2 / view.scale, g.cy + view.H / 2 / view.scale];
  const visible = s => s.box[1] >= view_box[0] && s.box[0] <= view_box[1] && s.box[3] >= view_box[2] && s.box[2] <= view_box[3];
  const minPx = 1.5 / view.scale;   // 화면에서 1.5 px보다 작은 도형은 건너뜀
  const path = (shapes, closed) => {
    b.beginPath();
    for (const s of shapes) {
      if (!visible(s) || (closed && s.box[1] - s.box[0] < minPx && s.box[3] - s.box[2] < minPx)) continue;
      for (const { pts } of s.parts) {
        for (let i = 0; i < pts.length; i += 2) {
          const x = view.X(pts[i]), y = view.Y(pts[i + 1]);
          if (i) b.lineTo(x, y); else b.moveTo(x, y);
        }
        if (closed) b.closePath();
      }
    }
  };
  b.save();
  b.globalAlpha = Layers.tileOpacity;
  b.fillStyle = '#f2efe9'; b.fillRect(0, 0, view.W, view.H);
  b.fillStyle = OFFLINE_STYLE.land.fill; path(L.land, true); b.fill('evenodd');
  b.fillStyle = OFFLINE_STYLE.green.fill; path(L.green, true); b.fill('evenodd');
  b.fillStyle = OFFLINE_STYLE.water.fill; path(L.water, true); b.fill('evenodd');
  b.strokeStyle = OFFLINE_STYLE.water.stroke; b.lineWidth = 1.2; path(L.waterLine, false); b.stroke();
  if (g.z >= 15) {
    b.fillStyle = OFFLINE_STYLE.building.fill; b.strokeStyle = OFFLINE_STYLE.building.stroke; b.lineWidth = 0.6;
    path(L.building, true); b.fill('evenodd'); if (g.z >= 16.5) b.stroke();
  }
  b.lineCap = 'round'; b.lineJoin = 'round';
  const grow = Math.max(1, (g.z - 14) * 0.6 + 1);
  for (let i = OFFLINE_STYLE.roads.length - 1; i >= 0; i--) {
    const [minZ, width, color] = OFFLINE_STYLE.roads[i];
    if (g.z < minZ) continue;
    b.strokeStyle = color; b.lineWidth = width * grow; path(L.roads[i], false); b.stroke();
  }
  // 지명: 순위가 높은 것부터, 겹치지 않게
  const maxRank = g.z < 12 ? 1 : g.z < 14 ? 3 : 5, taken = [];
  b.font = '600 11px system-ui'; b.textAlign = 'center'; b.textBaseline = 'middle';
  for (const p of Offline.places) {
    if (p.rank > maxRank) break;
    const x = view.X(p.wx), y = view.Y(p.wy);
    if (x < 0 || x > view.W || y < 0 || y > view.H) continue;
    const w = b.measureText(p.name).width / 2 + 4;
    if (taken.some(([tx, ty, tw]) => Math.abs(tx - x) < tw + w && Math.abs(ty - y) < 14)) continue;
    taken.push([x, y, w]);
    b.lineWidth = 3; b.strokeStyle = 'rgba(255,255,255,.9)'; b.strokeText(p.name, x, y);
    b.fillStyle = '#4a4f57'; b.fillText(p.name, x, y);
  }
  b.restore();
}
