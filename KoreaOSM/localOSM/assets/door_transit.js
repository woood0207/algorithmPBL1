(() => {
  'use strict';
  const facilities=L.layerGroup().addTo(map);
  const names={walk:'Walk',drive:'Car / taxi',rail:'Train',bus:'Bus',public:'Train + bus'};
  const colors=routeVisual.palette;
  const clock=value=>value?value.slice(5,16).replace('T',' '):'';
  function clear(){facilities.clearLayers();}
  function renderNetwork(r){
    clear();const box=$('route-result');box.hidden=false;
    box.append(el('div',r.complete?(r.objective==='distance'?distance(r.distance_m):duration(r.duration_s)):'No connected route','distance'));
    box.append(el('p',r.complete?`${r.selected_label} · ${distance(r.distance_m)} · Estimated ${duration(r.duration_s)}`:r.message,'totals'));
    box.append(el('p','No timetable · Assumed continuous service · No waiting','hint'));
    const bounds=[r.start_coordinate,r.end_coordinate],features=[],list=el('ol',undefined,'door-trip-stages');
    function draw(points,mode,properties,dashed=false){
      if(!points?.length)return;
      bounds.push(...points);
      routeVisual.draw(points,mode,properties.description||names[mode]||'Connection',routeLines,
        properties.geometry_kind?.startsWith('schematic'));
      features.push({type:'Feature',properties:{mode,routing_model:'network',estimated:true,service_verified:false,...properties},
        geometry:{type:'LineString',coordinates:points.map(([lat,lon])=>[lon,lat])}});
    }
    const shown=new Set();
    for(const leg of r.network_legs){
      if(leg.kind==='ground'){
        const p=leg.part,road=p.road;
        draw(road.coordinates,leg.mode,{geometry_kind:'road_graph',distance_m:road.distance_m,
          duration_s:road.duration_s,description:`${names[leg.mode]} ${leg.label}`});
        for(const [point,snap] of [[p.start_coordinate,road.start],[p.end_coordinate,road.end]])
          draw([point,snap.coordinate],'connection',{geometry_kind:'unverified_snap_link',description:'Estimated walk to the road'});
        list.append(el('li',`${names[leg.mode]} ${leg.label} · ${distance(leg.distance_m)} · ${duration(leg.duration_s)}${p.alternate_snap?' (alternate road connection)':''}`));
      }else{
        const g=leg.geometry;
        draw(g.coordinates,leg.mode,{geometry_kind:g.geometry_kind,assumed_service:true,schedule_confirmed:false,
          distance_m:leg.distance_m,duration_s:leg.duration_s,description:`${names[leg.mode]} ${leg.from_stop.name} → ${leg.to_stop.name} · assumed continuous service`},true);
        for(const link of g.connectors)draw(link,'connection',{geometry_kind:'unverified_ride_facility_link',description:'Estimated walk to the network'});
        for(const s of [leg.from_stop,leg.to_stop])if(!shown.has(s.id)){
          shown.add(s.id);
          L.circleMarker(s.coordinate,{pane:'route',radius:8,color:'white',weight:3,fillColor:colors[leg.mode],fillOpacity:1,bubblingMouseEvents:false})
            .bindTooltip(el('span',s.name),{pane:'routeLabels'}).addTo(facilities);
        }
        list.append(el('li',`${names[leg.mode]} ${leg.from_stop.name} → ${leg.to_stop.name}\n${distance(leg.distance_m)} · Estimated ${duration(leg.duration_s)} · No waiting`));
      }
    }
    box.append(list);
    if(r.complete)box.append(el('p',`${r.transfers} transfers${r.network_legs.some(l=>l.mode==='rail')?' · Train '+r.rail_speed_kmh+' km/h':''}${r.network_legs.some(l=>l.mode==='bus')?' · Bus: road speeds':''}`,'hint'));
    if(r.alternatives.length>1){
      const table=el('table',undefined,'door-network-alternatives'),head=el('tr');
      for(const title of ['Route','Distance','Est. time'])head.append(el('th',title));
      table.append(head);
      for(const a of r.alternatives){const row=el('tr');for(const value of [a.label,distance(a.distance_m),duration(a.duration_s)])row.append(el('td',value));table.append(row);}
      box.append(table);
    }
    const notes=el('details',undefined,'route-assumptions');notes.append(el('summary','Assumptions and search scope'));
    notes.append(el('p',`Nearest ${r.candidate_count} facilities per mode${r.mode==='public_transit'?', plus the nearest bus terminal within 3 km of each station':''}. This is a bounded candidate search, not a global search over every station and unlimited transfers.`,'hint'));
    notes.append(el('p','Assumed service: trains use connected tracks in both directions; buses use the car road network. Disconnected tracks and roads remain unreachable. Actual services and fares are not verified.','hint'));
    notes.append(el('p','Gray dashed links are estimated walking connections, included in distance and time. Physical access is unverified.','hint'));
    if(r.access_mode==='drive')notes.append(el('p','Car or taxi access is assumed at both ends.','hint'));
    box.append(notes);
    box.append(el('p',`Search ${((r.elapsed_ms||0)/1000).toFixed(2)} sec · ${r.candidate_pairs_checked||0} connection pairs`,'hint'));
    if(features.length){
      state.download=URL.createObjectURL(new Blob([JSON.stringify({type:'FeatureCollection',properties:{routing_model:'network',objective:r.objective,assumed_service:true},features})],{type:'application/geo+json'}));
      const a=el('a','Download network route GeoJSON');a.href=state.download;a.download='network-transit-route.geojson';box.append(a);
    }
    if(features.length)routeVisual.show(bounds);fit(L.latLngBounds(bounds),18);
    requestAnimationFrame(()=>box.scrollIntoView({block:innerWidth<=680?'start':'nearest'}));
  }
  function render(r){
    if(r.routing_model==='network'){renderNetwork(r);return;}
    clear();const box=$('route-result');box.hidden=false;
    const start=r.selected_start||r.nearest_start,end=r.selected_end||r.nearest_end;
    const bounds=[r.start_coordinate,r.end_coordinate],features=[];
    box.append(el('div',r.complete?duration(r.duration_s):'Total time unconfirmed','distance'));
    if(r.complete)box.append(el('p',`${clock(r.departure)} Start → ${clock(r.arrival)} Arrival`,'totals'));
    box.append(el('p',r.complete?'Access + waiting and ride + final connection':'Road access + transit preview · Service and time unconfirmed','totals'));
    for(const [stop,title] of [[start,'Board'],[end,'Alight']])if(stop){
      const color=stop.kind==='bus_terminal'?colors.bus:colors.rail;
      const label=`${title}: ${stop.name}${stop.details.operator?' · '+stop.details.operator:''}`;
      box.append(el('p',`${label} · Proximity rank ${stop.proximity_rank} · straight-line ${distance(stop.proximity_m)}`,'hint'));
      L.circleMarker(stop.coordinate,{pane:'route',radius:8,color:'white',weight:2,fillColor:color,fillOpacity:1,bubblingMouseEvents:false})
        .bindTooltip(el('span',label),{pane:'routeLabels',permanent:false}).addTo(facilities);
      bounds.push(stop.coordinate);
    }
    const list=el('ol',undefined,'door-trip-stages');
    function ride(preview){
      const points=preview.coordinates;bounds.push(...points);
      routeVisual.draw(points,preview.mode,`${names[preview.mode]||'Transit'} preview · ${preview.message}`,routeLines,
        preview.geometry_kind.startsWith('schematic'));
      features.push({type:'Feature',properties:{mode:preview.mode,geometry_kind:preview.geometry_kind,
        service_verified:false,schedule_confirmed:preview.schedule_confirmed,complete:r.complete,description:preview.message},
        geometry:{type:'LineString',coordinates:points.map(([lat,lon])=>[lon,lat])}});
      for(const connector of preview.connectors||[]){
        routeVisual.draw(connector,'connection','Unverified facility connection');
        features.push({type:'Feature',properties:{mode:'connection',geometry_kind:'unverified_ride_facility_link',service_verified:false},
          geometry:{type:'LineString',coordinates:connector.map(([lat,lon])=>[lon,lat])}});
      }
    }
    function ground(part,title){
      if(!part){list.append(el('li',`${title}: Could not calculate.`));return;}
      if(!part.reachable){list.append(el('li',`${title}: Road connection failed. The facility may be over 500 m from a usable road or on a disconnected segment.`));return;}
      const road=part.road;
      const coords=road.coordinates;
      bounds.push(...coords);
      routeVisual.draw(coords,part.mode,`${title} · ${duration(part.duration_s)} · ${distance(part.distance_m)}`);
      features.push({type:'Feature',properties:{stage:title,mode:part.mode,geometry_kind:'road_graph',complete:r.complete,
        duration_s:road.duration_s,distance_m:road.distance_m},geometry:{type:'LineString',coordinates:coords.map(([lat,lon])=>[lon,lat])}});
      for(const [point,snap] of [[part.start_coordinate,road.start],[part.end_coordinate,road.end]]){
        routeVisual.draw([point,snap.coordinate],'connection','Estimated walk to the road');
        features.push({type:'Feature',properties:{stage:title,mode:'walk',geometry_kind:'unverified_snap_link',
          duration_s:snap.distance_m*3.6/4.5,distance_m:snap.distance_m,complete:r.complete},
          geometry:{type:'LineString',coordinates:[point,snap.coordinate].map(([lat,lon])=>[lon,lat])}});
      }
      list.append(el('li',`${names[part.mode]} ${title} · ${duration(part.duration_s)} · ${distance(part.distance_m)}`));
    }
    ground(r.access,`Origin → ${start?.name||'Boarding stop'}`);
    if(r.transit){
      let rideIndex=0;
      for(const leg of r.transit.legs){
        list.append(el('li',`${names[leg.mode]} ${leg.from_stop.name} → ${leg.to_stop.name}\nWait ${duration(leg.waiting_s)} · ${clock(leg.departure)} ~ ${clock(leg.arrival)}\n${leg.source}`));
        const preview=['rail','bus'].includes(leg.mode)?r.ride_previews?.[rideIndex++]:null;
        if(preview){ride(preview);continue;}
        const points=[leg.from_stop.coordinate,leg.to_stop.coordinate];bounds.push(...points);
        routeVisual.draw(points,leg.mode,'Scheduled service: schematic geometry',routeLines,true);
        features.push({type:'Feature',properties:{mode:leg.mode,geometry_kind:'schematic_schedule_connection',
          departure:leg.departure,arrival:leg.arrival,source:leg.source,complete:true},
          geometry:{type:'LineString',coordinates:points.map(([lat,lon])=>[lon,lat])}});
      }
    }else if(r.ride_previews?.length){
      for(const preview of r.ride_previews){
        ride(preview);
        list.append(el('li',`${names[preview.mode]||'Transit'} Preview: ${preview.from_stop.name} → ${preview.to_stop.name}\n${preview.message}\nNo timetable · Actual service and travel time unverified`));
      }
    }else list.append(el('li','No intermediate transit connection is available.'));
    ground(r.egress,`${end?.name||'Arrival stop'} → Destination`);box.append(list);
    if(r.ground_distance_m!==null)box.append(el('p',`First and last road connections ${distance(r.ground_distance_m)} · ${duration(r.known_ground_duration_s)}`,'hint'));
    if(r.complete)box.append(el('p',`Wait ${duration(r.waiting_s)} · ${r.transfers} transfers · ${r.fare_krw===null?'Transit fare unavailable':'KRW '+r.fare_krw.toLocaleString()+' (transit only)'}`,'hint'));
    box.append(el('p',r.candidate_count===1?'Uses the nearest facility at each end by straight-line distance.':'Compares up to three nearby facilities at each end. This is not a global optimum over every station.','hint'));
    box.append(el('p',r.served_filter_applied?'Candidates are restricted to stops in the imported timetable.':'Nearby infrastructure was selected. Check timetables for actual operating services.','hint'));
    const connectors=(r.access?.connector_distance_m||0)+(r.egress?.connector_distance_m||0);
    box.append(el('p',`Gray road connectors ${distance(connectors)} are included as estimated walking time; access is unverified.`,'hint'));
    if(r.access_mode==='drive')box.append(el('p','Car or taxi access is assumed at both ends. Parking, taxi waits and fares are excluded.','hint'));
    if(r.ride_previews?.length)box.append(el('p','Purple: train; orange: bus. Preview geometry does not verify operating service. Schematic links are dashed where the network is unavailable.','hint'));
    if(r.candidate_count>1){
      const details=el('details');details.append(el('summary','Nearby facility candidates'));
      for(const [items,label] of [[r.start_candidates,'Start'],[r.end_candidates,'Arrival']])for(const stop of items)
        details.append(el('p',`${label} #${stop.proximity_rank}: ${stop.name} · ${distance(stop.proximity_m)}`,'hint'));
      box.append(details);
    }
    if(features.length){
      state.download=URL.createObjectURL(new Blob([JSON.stringify({type:'FeatureCollection',features})],{type:'application/geo+json'}));
      const link=el('a',r.complete?'Download transit route GeoJSON':'Download preview GeoJSON');link.href=state.download;link.download='transit-linked-route.geojson';box.append(link);
    }
    routeVisual.show(bounds);fit(L.latLngBounds(bounds),18);
    requestAnimationFrame(()=>box.scrollIntoView({block:innerWidth<=680?'start':'nearest'}));
  }
  const now=new Date(Date.now()+9*3600000);$('door-departure').value=now.toISOString().slice(0,16).replace('T',' ');
  for(const id of ['door-departure','door-candidates','door-access-mode','door-transfer','door-stop-policy','door-routing-model','door-rail-speed'])$(id).onchange=()=>{
    syncTransitModel();
    cancelRequest();clearResult();status('Options changed. Choose Find route to recalculate.');
  };
  window.doorTransit={clear,render};
})();
