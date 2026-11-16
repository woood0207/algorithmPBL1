'use strict';
const $ = id => document.getElementById(id);
const el = (tag, value, cls) => {const node = document.createElement(tag); if(value !== undefined) node.textContent = value; if(cls) node.className = cls; return node;};
const modeNames = {walk:'Walk', bike:'Cycle', drive:'Car'};
const colors = {walk:'#008767', bike:'#b026b9', drive:'#0866e8'};
const distance = m => m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${m.toFixed(0)} m`;
const duration = seconds => {if(seconds<60)return `${Math.ceil(seconds)} sec`;const minutes=Math.ceil(seconds/60); return minutes>=60 ? `${Math.floor(minutes/60)} hr ${minutes%60} min` : `${minutes} min`;};
function departureISO(id){
  const value=$(id).value.trim().replace(' ','T');
  if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value))throw new Error('Enter the departure as YYYY-MM-DD HH:mm (KST).');
  return value+':00+09:00';
}
const isDoorTransit = () => ['rail_transit','bus_transit','public_transit'].includes($('route-mode').value);
const isScheduledTransit = () => isDoorTransit() && $('door-routing-model').value==='schedule';
function syncTransitModel() {
  const scheduled=isScheduledTransit();
  $('door-schedule-options').hidden=!scheduled;
  $('door-network-options').hidden=scheduled;
  $('route-objective').disabled=scheduled;
  if(scheduled)$('route-objective').value='time';
}
const buttonLabel = () => isDoorTransit() ? 'Find transit route' : $('route-objective').value==='time' ? 'Find fastest route' : 'Find shortest route';
const speedColor = speed => speed===null ? '#939b9b' : speed<=30 ? '#be5143' : speed<=50 ? '#bb861e' : speed<=80 ? '#49815b' : '#357da6';
const map = L.map('map', {preferCanvas:true, minZoom:6, maxZoom:19, zoomSnap:.5, maxBounds:[[30,122],[41,134]], maxBoundsViscosity:.8});
map.attributionControl.setPrefix(false);
map.attributionControl.addAttribution('© OpenStreetMap contributors · ODbL · Geofabrik');
L.control.scale({imperial:false, position:'bottomright'}).addTo(map);
map.createPane('route'); map.getPane('route').style.zIndex = 650;
map.createPane('routeEndpoints');map.getPane('routeEndpoints').style.zIndex=680;
map.createPane('routeLabels');map.getPane('routeLabels').style.zIndex=690;map.getPane('routeLabels').style.pointerEvents='none';
map.createPane('baseRoads');map.getPane('baseRoads').style.zIndex=350;
map.createPane('cityLabels');map.getPane('cityLabels').style.zIndex=460;
map.createPane('land'); map.getPane('land').style.zIndex = 250;
const state = {start:null, end:null, picking:'start', request:0, controller:null, result:null, download:null, ready:false};
const routeLines = L.layerGroup().addTo(map), markers = L.layerGroup().addTo(map);
const highwayColors = {motorway:'#d89b35', trunk:'#d89b35', primary:'#cda76a', secondary:'#b2ad88'};
function roadStyle(feature) {
  const p = feature.properties, base = p.highway.replace(/_link$/, '');
  if(p.kind === 'coast') return {color:'#90b6c2', weight:1.5};
  if(p.kind === 'water') return {color:'#9fc4cf', fillColor:'#d5e8ec', weight:1, fillOpacity:.7};
  if(p.kind === 'land') return {color:'#cbd8bb', fillColor:'#e0e9d2', weight:.4, fillOpacity:.7};
  if(p.kind === 'building') return {color:'#b8b6ac', fillColor:'#d4d1c4', weight:.7, fillOpacity:.6};
  const path = ['footway','path','pedestrian','steps','cycleway','bridleway'].includes(base);
  if($('speed-colors').checked && map.getZoom()>=10 && p.kind==='road') {
    const limits=Object.values(p.speeds||{}).filter(Boolean).map(x=>x.drive_limit_kmh).filter(x=>x!==null);
    return {color:speedColor(limits.length?Math.min(...limits):null),weight:2,opacity:.9,dashArray:path?'3 3':undefined};
  }
  return {color:highwayColors[base] || (path ? '#bba28f' : '#8e9d96'), weight:base === 'motorway' ? 2.5 : base === 'trunk' ? 2 : 1.1, opacity:.85, dashArray:path ? '3 3' : undefined};
}
const background = L.geoJSON(null, {pane:'land', style:roadStyle, interactive:false}).addTo(map);
const roads = L.geoJSON(null, {pane:'baseRoads',style:roadStyle, onEachFeature:(f,l) => {
  l.on('click', () => {if(!state.picking) {
    const p=f.properties, box=$('detail'); box.replaceChildren(el('strong',`${p.name||'Unnamed road'} · ${p.highway||p.kind}`));
    for(const [key,label] of [['forward','Forward'],['backward','Reverse']]) if(p.speeds?.[key]) {
      const s=p.speeds[key]; box.append(el('div',`${label}: Limit ${s.drive_limit_kmh===null?'untagged':s.drive_limit_kmh+' km/h'} · Applied ${s.drive_kmh} km/h${s.drive_source==='default'?' (estimated)':''}${s.min_drive_kmh!==null?' · Minimum '+s.min_drive_kmh+' km/h':''}${s.conditional_conservative?' · lowest conditional value applied':''}${s.unparsed_tag?' · unparsed tag':''}`));
    }
  }});
  if(f.properties.name) l.bindTooltip(el('span',f.properties.name));
}}).addTo(map);
const cities = [
  ['Seoul',37.5665,126.9780],['Busan',35.1796,129.0756],['Incheon',37.4563,126.7052],['Daegu',35.8714,128.6014],
  ['Daejeon',36.3504,127.3845],['Gwangju',35.1595,126.8526],['Ulsan',35.5384,129.3114],['Sejong',36.4800,127.2890],
  ['Suwon',37.2636,127.0286],['Chuncheon',37.8813,127.7298],['Gangneung',37.7519,128.8761],['Cheongju',36.6424,127.4890],
  ['Jeonju',35.8242,127.1480],['Pohang',36.0190,129.3435],['Changwon',35.2280,128.6811],['Mokpo',34.8118,126.3922],
  ['Jeju',33.4996,126.5312],['Seogwipo',33.2541,126.5601],['Ulleung',37.4845,130.9057]
];
const cityLabels = L.layerGroup().addTo(map);
for(const [name,lat,lon] of cities) L.marker([lat,lon], {pane:'cityLabels',interactive:false, icon:L.divIcon({className:'place-label', html:el('span',name).outerHTML, iconSize:[85,20]})}).addTo(cityLabels);
const homeBounds = [[33.0,124.5],[38.65,131.9]];
function fit(bounds, maxZoom) {
  map.fitBounds(bounds, {paddingTopLeft:innerWidth > 680 ? [435,30] : [40,130], paddingBottomRight:innerWidth > 680 ? [30,30] : [40,innerHeight * .47+10], maxZoom, animate:false});
}
function home() {fit(homeBounds,9);}
$('home').onclick = home;
home();
let featureController = null, featureId = 0, featureTimer;
async function loadFeatures() {
  if(!state.ready) return;
  featureController?.abort(); featureController = new AbortController(); const id = ++featureId;
  const b = map.getBounds();
  const bbox = [Math.max(-180,b.getWest()),Math.max(-90,b.getSouth()),Math.min(180,b.getEast()),Math.min(90,b.getNorth())];
  $('map-status').textContent = 'Loading map detail…';
  try {
    const response = await fetch(`/api/features?bbox=${bbox.join(',')}&zoom=${map.getZoom()}`, {signal:featureController.signal});
    const data = EnglishUI.payload(await response.json()); if(id !== featureId) return;
    if(!response.ok) throw new Error(data.error || 'Map request failed');
    background.clearLayers(); roads.clearLayers();
    const filtered = data.features.filter(f => $('show-buildings').checked || f.properties.kind !== 'building');
    background.addData(filtered.filter(f => ['coast','water','land'].includes(f.properties.kind)));
    roads.addData(filtered.filter(f => !['coast','water','land'].includes(f.properties.kind)));
    $('map-status').textContent = data.overview ? 'National roads and coastline. Zoom in for local detail.' : `${filtered.length.toLocaleString()} map features${data.truncated ? ' · Zoom in for more detail.' : ''}`;
  } catch(error) {if(error.name !== 'AbortError' && id === featureId) $('map-status').textContent = `Map loading failed: ${error.message}`;}
}
map.on('moveend', () => {clearTimeout(featureTimer); featureTimer = setTimeout(loadFeatures,180);});
$('show-buildings').onchange = loadFeatures;
$('speed-colors').onchange = () => {roads.setStyle(roadStyle);$('speed-legend').hidden=!$('speed-colors').checked;};
function cancelRequest() {
  state.request++; state.controller?.abort(); state.controller = null;
  $('calculate').disabled = false; $('calculate').textContent = buttonLabel();
}
function clearResult() {
  window.doorTransit?.clear();
  routeLines.clearLayers(); state.result = null; $('route-result').hidden = true; $('route-result').replaceChildren();
  window.routeVisualReady?.clear();
  if(state.download) {URL.revokeObjectURL(state.download); state.download = null;}
}
function setPicking(key) {
  state.picking = key;
  map.getContainer().classList.toggle('route-picking',!!key);
  for(const name of ['start','end']) $(`pick-${name}`).classList.toggle('active',key === name);
}
function showMarkers(syncInputs=true) {
  markers.clearLayers();
  for(const key of ['start','end']) if(state[key]) {
    if(syncInputs) $(`${key}-coord`).value = state[key].map(x => x.toFixed(6)).join(', ');
    L.circleMarker(state[key], {pane:'routeEndpoints',radius:9,color:'white',weight:3,fillColor:key === 'start' ? '#008767' : '#d12c40',fillOpacity:1,bubblingMouseEvents:false})
      .bindTooltip(key === 'start' ? 'A · Start' : 'B · Destination',{pane:'routeLabels',permanent:true,direction:'top',className:'route-endpoint'}).addTo(markers);
  }
}
function status(message, error=false) {$('route-status').textContent = message; $('route-status').classList.toggle('error',error);}
function choosePoint(key, point, auto=true) {
  cancelRequest(); clearResult(); state[key] = [...point]; showMarkers();
  setPicking(!state.start ? 'start' : !state.end ? 'end' : null);
  if(state.start && state.end && auto) calculateRoute();
  else status(state.start ? 'Choose a destination.' : 'Choose a start point.');
}
map.on('click', event => {
  if(!state.ready || !state.picking) return;
  choosePoint(state.picking,[event.latlng.lat,event.latlng.lng]);
});
for(const key of ['start','end']) $(`pick-${key}`).onclick = () => {
  cancelRequest(); setPicking(key); status(`Click the map to choose ${key === 'start' ? 'a start point' : 'a destination'}.`);
};
function parsePoint(value) {
  const parts = value.trim().split(/[,\s]+/).filter(Boolean).map(Number);
  if(parts.length !== 2 || parts.some(x => !Number.isFinite(x)) || Math.abs(parts[0]) > 90 || Math.abs(parts[1]) > 180) throw new Error('Enter two numbers: latitude, longitude.');
  return parts;
}
for(const key of ['start','end']) $(`${key}-coord`).addEventListener('input', () => {
  try {state[key] = parsePoint($(`${key}-coord`).value);} catch {state[key] = null;}
  showMarkers(false);
  cancelRequest(); clearResult(); status('Choose Find route to use these coordinates.');
});
$('swap').onclick = () => {
  cancelRequest(); clearResult();
  [$('start-coord').value,$('end-coord').value] = [$('end-coord').value,$('start-coord').value];
  [state.start,state.end] = [state.end,state.start]; showMarkers();
  if(state.start && state.end) calculateRoute(); else setPicking(state.start ? 'end' : 'start');
};
$('clear-route').onclick = () => {
  cancelRequest(); clearResult(); state.start = state.end = null; markers.clearLayers();
  $('start-coord').value = $('end-coord').value = ''; setPicking('start'); status('Choose a start point, then a destination on the map.');
};
$('route-mode').onchange = () => {
  $('mixed-note').hidden = $('route-mode').value !== '7';
  $('door-transit-options').hidden=!isDoorTransit();
  syncTransitModel();
  cancelRequest(); clearResult(); if(state.start && state.end) calculateRoute();
};
$('route-objective').onchange = () => {cancelRequest();clearResult();if(state.start&&state.end)calculateRoute();};
$('calculate').onclick = calculateRoute;
for(const key of ['start','end']) $(`${key}-coord`).addEventListener('keydown', e => {if(e.key === 'Enter') calculateRoute();});
function renderRoute(result) {
  for(const [point,snap] of [[state.start,result.start],[state.end,result.end]])
    routeVisual.draw([point,snap.coordinate],'connection','Estimated connection to the road');
  const drawn=result.speed_sections?.length ? result.speed_sections.map(s=>({...s,coordinates:result.coordinates.slice(s.from_index,s.to_index+1)})) : result.legs;
  for(const leg of drawn) {
    const line=routeVisual.draw(leg.coordinates,leg.mode,modeNames[leg.mode]);
    if(leg.speed_kmh!==undefined) line.bindTooltip(el('span',`${leg.name||leg.highway} · Applied ${leg.speed_kmh} km/h · Limit ${leg.speed_limit_kmh===null?'untagged (estimated)':leg.speed_limit_kmh+' km/h'} · ${duration(leg.duration_s)}`));
  }
  for(let i=1;i<result.legs.length;i++) L.circleMarker(result.legs[i].coordinates[0],{pane:'route',radius:4,color:'#405460',weight:1.5,fillColor:'white',fillOpacity:1,interactive:false}).addTo(routeLines);
  const box = $('route-result'); box.hidden = false;
  box.append(el('div',result.objective==='time'?duration(result.duration_s):distance(result.distance_m),'distance'),el('p',result.objective==='time'?`Fastest route · ${distance(result.distance_m)}`:'Shortest road distance','totals'));
  if(result.objective!=='time' && result.duration_s!==null) box.append(el('p',`Estimated from speeds ${duration(result.duration_s)}`,'totals'));
  box.append(el('p',`Road connection: Start ${distance(result.start.distance_m)} · Arrival ${distance(result.end.distance_m)}`,'hint'));
  if(result.start.node === result.end.node) box.append(el('p','Both points connect to the same road node; road distance is 0 m.','hint'));
  for(const name of ['walk','bike','drive']) if(result.distance_by_mode_m[name] > 0) {
    const row = el('div',`${modeNames[name]} ${distance(result.distance_by_mode_m[name])}${result.duration_by_mode_s?' · '+duration(result.duration_by_mode_s[name]):''}`,'totals');
    const dot = el('i',undefined,'mode-dot'); dot.style.background = colors[name]; row.prepend(dot); box.append(row);
  }
  box.append(el('p',`Search ${(result.elapsed_ms / 1000).toFixed(2)} sec · ${result.expanded.toLocaleString()} nodes expanded`,'hint'));
  if(result.speed_coverage) {
    const c=result.speed_coverage,total=c.drive_osm_distance_m+c.drive_default_distance_m;
    if(total) box.append(el('p',`Driving distance with tagged limits ${(100*c.drive_osm_distance_m/total).toFixed(1)}% · estimated ${(100*c.drive_default_distance_m/total).toFixed(1)}%`,'hint'));
    if(c.conditional_conservative_distance_m>0) box.append(el('p',`Conservative conditional speeds over ${distance(c.conditional_conservative_distance_m)}`,'hint'));
  }
  if(result.speed_sections?.length) {
    const details=el('details'),list=el('ol',undefined,'speed-sections'); details.append(el('summary',`Applied road speeds · ${result.speed_sections.length} sections`));
    for(const s of result.speed_sections) {
    const item=el('li'),button=el('button',`${s.name||s.highway||'Road'} · ${distance(s.distance_m)} · ${duration(s.duration_s)}\nApplied ${s.speed_kmh} km/h / Limit ${s.speed_limit_kmh===null?'untagged':s.speed_limit_kmh+' km/h'}${s.min_speed_kmh!==null?' · Minimum '+s.min_speed_kmh+' km/h':''}${s.speed_source==='default'?' · estimated':''}${s.conditional_conservative?' · conservative conditional speed':''}${s.unparsed_tag?' · unparsed tag':''}`);
      button.onclick=()=>fit(L.latLngBounds(result.coordinates.slice(s.from_index,s.to_index+1)),18);
      item.append(button);list.append(item);
    }
    details.append(list);box.append(details);
  }
  if(result.legs.length > 1) {
    const detail = el('details'), list = el('ol',undefined,'stage-list');
    detail.append(el('summary',`${result.legs.length} legs · ${result.mode_changes} mode changes`));
    for(const leg of result.legs) list.append(el('li',`${modeNames[leg.mode]} · ${distance(leg.distance_m)}`));
    detail.append(list); box.append(detail);
  }
  const geojson = {type:'FeatureCollection',features:drawn.map(leg => ({type:'Feature',properties:{name:leg.name,mode:leg.mode,distance_m:leg.distance_m,duration_s:leg.duration_s,speed_kmh:leg.speed_kmh,speed_limit_kmh:leg.speed_limit_kmh,speed_source:leg.speed_source,objective:result.objective},geometry:{type:'LineString',coordinates:leg.coordinates.map(([lat,lon]) => [lon,lat])}}))};
  state.download = URL.createObjectURL(new Blob([JSON.stringify(geojson)],{type:'application/geo+json'}));
  const link = el('a','Download route GeoJSON'); link.href = state.download; link.download = 'national-route.geojson'; box.append(link);
  const bounds=[state.start,state.end,...result.coordinates];routeVisual.show(bounds);fit(L.latLngBounds(bounds),18);
  requestAnimationFrame(()=>box.scrollIntoView({block:innerWidth<=680?'start':'nearest'}));
}
async function calculateRoute() {
  cancelRequest(); clearResult();
  if(!state.ready) {status('Wait for the server and national dataset to load.',true); return;}
  try {state.start = parsePoint($('start-coord').value); state.end = parsePoint($('end-coord').value);}
  catch(error) {status(error.message,true); return;}
  showMarkers(); setPicking(null);
  const id = state.request, controller = new AbortController(); state.controller = controller;
  $('calculate').disabled = true; $('calculate').textContent = 'Calculating…'; status(isDoorTransit()?'Finding station connections and transit routes. Long routes may take a moment…':'Calculating the route locally…');
  try {
    const payload={start:state.start,end:state.end,mode:isDoorTransit()?$('route-mode').value:Number($('route-mode').value),objective:$('route-objective').value};
    if(isDoorTransit())Object.assign(payload,{departure:isScheduledTransit()?departureISO('door-departure'):null,access_mode:$('door-access-mode').value,
      candidate_count:Number($('door-candidates').value),min_transfer_s:Number($('door-transfer').value),boarding_s:300,stop_policy:$('door-stop-policy').value,
      routing_model:$('door-routing-model').value,rail_speed_kmh:Number($('door-rail-speed').value)});
    const response = await fetch('/api/route',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:controller.signal});
    const result = EnglishUI.payload(await response.json()); if(id !== state.request) return;
    if(!response.ok) throw new Error(result.error || 'Route request failed');
    state.result = result;
    if(result.kind==='door_to_door_transit') {
      window.doorTransit.render(result);status(result.complete?(result.routing_model==='network'?'Route found using the connected-network assumption.':'Transit route found.'):result.message,!result.complete);return;
    }
    if(!result.start || !result.end) {status('No usable road node is within 500 m. Choose a point closer to a road.',true); return;}
    if(!result.reachable) {status('No connected road route. Check the transport mode and road access. Ferries are excluded.',true); return;}
    renderRoute(result); status('Route found.');
  } catch(error) {
    if(id !== state.request || error.name === 'AbortError') return;
    status(`Routing failed: ${error.message}`,true);
  } finally {
    if(id === state.request) {state.controller = null; $('calculate').disabled = false; $('calculate').textContent = buttonLabel();}
  }
}
const examples = {'seoul-busan':[[37.5665,126.9780],[35.1796,129.0756]], 'seoul-pohang':[[37.5665,126.9780],[36.0190,129.3435]], 'daejeon-gwangju':[[36.3504,127.3845],[35.1595,126.8526]]};
document.querySelectorAll('[data-example]').forEach(button => button.onclick = () => {
  const [start,end] = examples[button.dataset.example]; cancelRequest(); clearResult();
  state.start = [...start]; state.end = [...end]; showMarkers(); calculateRoute();
});
let searchTimer, searchController, searchId = 0;
$('search').oninput = () => {
  clearTimeout(searchTimer); searchController?.abort(); const id = ++searchId;
  const query = $('search').value.trim(); $('results').replaceChildren(); if(!query) return;
  searchTimer = setTimeout(async () => {
    searchController = new AbortController();
    try {
      const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`,{signal:searchController.signal});
      const data = EnglishUI.payload(await response.json()); if(id !== searchId) return;
      if(!response.ok) throw new Error(data.error || 'Search failed');
      if(!data.results.length) {$('results').append(el('p','No results. Enter coordinates or choose a point on the map.','hint')); return;}
      for(const place of data.results.slice(0,8)) {
        const row = el('div'), name = el('button',place.name,'place-name');
        name.onclick = () => fit(L.latLngBounds([place.coordinate,place.coordinate]).pad(.01),14);
        row.append(name);
        for(const [key,title] of [['start','From here'],['end','To here']]) {
          const button = el('button',title,'small'); button.onclick = () => choosePoint(key,place.coordinate); row.append(button);
        }
        $('results').append(row);
      }
    } catch(error) {if(id === searchId && error.name !== 'AbortError') $('results').append(el('p',error.message,'hint'));}
  },250);
};
async function initialize() {
  if(location.protocol === 'file:') {$('data-status').textContent = 'Run start_national_map.bat and open the displayed HTTP address.'; return;}
  try {
    const response = await fetch('/api/map'), data = EnglishUI.payload(await response.json());
    if(!response.ok) throw new Error(data.error || 'National data is unavailable.');
    $('data-status').textContent = `${data.nodes.toLocaleString()} nodes · ${data.edges.toLocaleString()} directed edges`;
    if(!data.objectives.includes('time')) {$('route-objective').querySelector('[value="time"]').disabled=true;$('route-objective').value='distance';$('calculate').textContent=buttonLabel();}
    state.ready = true; await loadFeatures();
    window.localMapReady = true; document.documentElement.dataset.mapReady = 'true';
  } catch(error) {$('data-status').textContent = `Could not load national data: ${error.message}`; $('data-status').classList.add('error');}
}
setPicking('start'); initialize();
