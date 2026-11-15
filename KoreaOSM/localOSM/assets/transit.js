(() => {
  'use strict';
  const selection={start:null,end:null},stopLayers={rail_station:L.layerGroup(),bus_terminal:L.layerGroup(),interchange:L.layerGroup()};
  const transitPaths=L.layerGroup().addTo(map),routeLayer=L.layerGroup().addTo(map);
  const modeNames={rail:'Train',bus:'Bus',walk:'Walk transfer'},modeColors={rail:'#8253a3',bus:'#d27737',walk:'#628273'};
  let available=false,schedules=0,mapController=null,mapId=0,searchController=null,searchId=0,planController=null,planId=0,mapTimer,searchTimer;
  map.createPane('transitRails');map.getPane('transitRails').style.zIndex=440;
  map.createPane('transitStops');map.getPane('transitStops').style.zIndex=510;
  const typeName=s=>s.kind==='rail_station'?'Train stations':s.kind==='interchange'?'Interchange':'Bus terminals';
  function resetPlan() {planId++;planController?.abort();planController=null;routeLayer.clearLayers();routeVisual.clear(routeLayer);$('transit-result').hidden=true;$('transit-result').replaceChildren();}
  function selectStop(which,stop) {
    resetPlan();selection[which]=stop;
    $(which==='start'?'transit-from':'transit-to').textContent=`${which==='start'?'Start':'Arrival'}: ${stop.name} (${typeName(stop)}${stop.details.operator?' · '+stop.details.operator:''})`;
    $('transit-message').textContent=schedules?'Check departure time and search imported timetables.':'Stop selected. Import timetables for scheduled travel times, or use network routing above.';
    map.closePopup();
  }
  function popup(stop) {
    const box=el('div');box.append(el('strong',stop.name),el('p',`${typeName(stop)} · ${stop.source}`));
    if(stop.details.operator)box.append(el('p',`Operator: ${stop.details.operator}`));
    box.append(el('p','OSM infrastructure alone does not verify operating train or bus services.'));
    const details=el('details');details.append(el('summary','Stop ID for timetable imports'),el('code',stop.id));box.append(details);
    for(const [key,title] of [['start','Use as departure'],['end','Use as arrival']]) {
      const button=el('button',title);button.onclick=()=>selectStop(key,stop);box.append(button);
    }
    return box;
  }
  function updateVisibility() {
    for(const [kind,control] of [['rail_station','transit-stations'],['bus_terminal','transit-terminals'],['interchange','transit-stations']]) {
      if($(control).checked)stopLayers[kind].addTo(map);else map.removeLayer(stopLayers[kind]);
    }
    if($('transit-rails').checked)transitPaths.addTo(map);else map.removeLayer(transitPaths);
  }
  async function loadMap() {
    if(!available)return;
    mapController?.abort();mapController=new AbortController();const id=++mapId,b=map.getBounds();
    const bbox=[Math.max(-180,b.getWest()),Math.max(-90,b.getSouth()),Math.min(180,b.getEast()),Math.min(90,b.getNorth())];
    try {
      const response=await fetch(`/api/transit/map?bbox=${bbox.join(',')}&zoom=${map.getZoom()}`,{signal:mapController.signal});
      const data=EnglishUI.payload(await response.json());if(id!==mapId)return;if(!response.ok)throw new Error(data.error||'Could not load transit map');
      transitPaths.clearLayers();Object.values(stopLayers).forEach(layer=>layer.clearLayers());
      L.geoJSON(data.railways,{pane:'transitRails',style:{color:'#8253a3',weight:map.getZoom()<10?1.5:2.2,opacity:.8,dashArray:'6 3'},interactive:false}).addTo(transitPaths);
      for(const stop of data.stops) {
        const color=stop.kind==='bus_terminal'?'#d27737':'#8253a3';
        L.circleMarker(stop.coordinate,{pane:'transitStops',radius:map.getZoom()<9?3:5,color:'white',weight:1.1,fillColor:color,fillOpacity:1,bubblingMouseEvents:false})
          .bindTooltip(el('span',`${stop.name} · ${typeName(stop)}`)).bindPopup(popup(stop)).addTo(stopLayers[stop.kind]);
      }
      updateVisibility();
      if(data.truncated)$('transit-message').textContent='Many stations and terminals. Zoom in or search by name.';
    } catch(error) {if(error.name!=='AbortError'&&id===mapId)$('transit-message').textContent=error.message;}
  }
  async function refresh() {
    try {
      const response=await fetch('/api/transit/status'),data=EnglishUI.payload(await response.json());if(!response.ok)throw new Error(data.error||'Could not load transit status');
      available=data.available;schedules=data.scheduled_journeys;
      $('transit-plan').disabled=!available||!schedules;
      $('transit-status').textContent=available?`Train stations ${(data.stops.rail_station||0).toLocaleString()} · Bus terminals ${(data.stops.bus_terminal||0).toLocaleString()} · Imported journeys ${schedules.toLocaleString()}`:'Offline transit data is not installed.';
      if(data.schedule_range)$('transit-status').textContent+=` · ${data.schedule_range[0].slice(0,10)}~${data.schedule_range[1].slice(0,10)}`;
      if(!schedules)$('transit-message').textContent='Infrastructure is available. Use network routing above without a timetable, or import schedules for actual departures.';
      await loadMap();
    } catch(error) {$('transit-status').textContent=error.message;}
  }
  map.on('moveend',()=>{clearTimeout(mapTimer);mapTimer=setTimeout(loadMap,220);});
  for(const id of ['transit-rails','transit-stations','transit-terminals'])$(id).onchange=updateVisibility;
  $('transit-refresh').onclick=refresh;
  $('transit-search').oninput=()=>{
    clearTimeout(searchTimer);searchController?.abort();const id=++searchId,q=$('transit-search').value.trim();$('transit-search-results').replaceChildren();if(!q||!available)return;
    searchTimer=setTimeout(async()=>{
      searchController=new AbortController();
      try {
        const response=await fetch(`/api/transit/stops?q=${encodeURIComponent(q)}`,{signal:searchController.signal});const data=EnglishUI.payload(await response.json());if(id!==searchId)return;
        if(!response.ok)throw new Error(data.error||'Station search failed');
        for(const stop of data.stops.slice(0,10)) {
          const row=el('div',undefined,'transit-search-row'),name=el('button',`${stop.name} · ${typeName(stop)}${stop.details.operator?' · '+stop.details.operator:''}`,'transit-stop-name');
          name.onclick=()=>{map.setView(stop.coordinate,16,{animate:false});L.popup().setLatLng(stop.coordinate).setContent(popup(stop)).openOn(map);};row.append(name);
          for(const [key,title] of [['start','From here'],['end','To here']]){const button=el('button',title);button.onclick=()=>selectStop(key,stop);row.append(button);}
          $('transit-search-results').append(row);
        }
        if(!data.stops.length)$('transit-search-results').append(el('p','No matching station or terminal.','hint'));
      } catch(error){if(error.name!=='AbortError'&&id===searchId)$('transit-search-results').append(el('p',error.message,'hint'));}
    },250);
  };
  for(const id of ['transit-departure','transit-margin','transit-modes'])$(id).onchange=resetPlan;
  $('transit-plan').onclick=async()=>{
    resetPlan();if(!selection.start||!selection.end){$('transit-message').textContent='Select departure and arrival stations or terminals.';return;}
    if(!$('transit-departure').value){$('transit-message').textContent='Enter a departure date and time in Korea Standard Time.';return;}
    planController=new AbortController();const id=planId;$('transit-message').textContent='Searching imported timetables, including waits and transfers…';
    try {
      const response=await fetch('/api/transit/route',{method:'POST',headers:{'Content-Type':'application/json'},signal:planController.signal,
        body:JSON.stringify({start:selection.start.id,end:selection.end.id,departure:departureISO('transit-departure'),
          min_transfer_s:Number($('transit-margin').value),boarding_s:300,modes:$('transit-modes').value})});
      const r=EnglishUI.payload(await response.json());if(id!==planId)return;if(!response.ok)throw new Error(r.error||'Transit routing failed');
      if(!r.reachable){$('transit-message').textContent=r.message||'No connection in the imported timetables. This does not mean no service operates.';return;}
      const box=$('transit-result');box.hidden=false;box.append(el('strong',`${duration(r.duration_s)} · ${r.arrival.slice(0,16).replace('T',' ')} Arrival`));
      box.append(el('p',`Wait ${duration(r.waiting_s)} · ${r.transfers} transfers · ${r.fare_krw===null?'Total fare unavailable':'KRW '+r.fare_krw.toLocaleString()}`));
      const list=el('ol');
      for(const leg of r.legs) {
        list.append(el('li',`${modeNames[leg.mode]} ${leg.from_stop.name} → ${leg.to_stop.name}\n${leg.departure.slice(5,16).replace('T',' ')} ~ ${leg.arrival.slice(5,16).replace('T',' ')} · Wait ${duration(leg.waiting_s)} · ${leg.source}`));
        routeVisual.draw([leg.from_stop.coordinate,leg.to_stop.coordinate],leg.mode,'Scheduled service: schematic connection',routeLayer,true);
      }
      box.append(list,el('p','Dashed lines show stop-to-stop schematic connections, not actual vehicle paths.','hint'));
      if(r.legs.length){const bounds=r.legs.flatMap(leg=>[leg.from_stop.coordinate,leg.to_stop.coordinate]);routeVisual.show(bounds);fit(L.latLngBounds(bounds),16);}
      $('transit-message').textContent='Earliest arrival within imported timetables. Access to the station is excluded.';
    } catch(error){if(error.name!=='AbortError'&&id===planId)$('transit-message').textContent=error.message;}
  };
  const now=new Date(Date.now()+9*3600000);$('transit-departure').value=now.toISOString().slice(0,16).replace('T',' ');
  refresh();
})();
