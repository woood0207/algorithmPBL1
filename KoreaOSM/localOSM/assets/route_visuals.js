'use strict';
// Shared route treatment for roads, train/bus journeys and timetable previews.
const routeVisual = (() => {
  const palette={rail:'#6d28d9',bus:'#e75b12',walk:'#008767',drive:'#0866e8',bike:'#b026b9',public:'#6d28d9'};
  const arrows=L.layerGroup().addTo(map),records=[];
  let bounds=null;
  map.createPane('routeArrows');map.getPane('routeArrows').style.zIndex=655;
  function updateArrows(){
    arrows.clearLayers();let total=0;
    for(const record of records){
      if(!map.hasLayer(record.layer)||!record.line._map)continue;
      let next=55,travelled=0;
      const points=record.points.map(p=>map.latLngToLayerPoint(p));
      for(let i=1;i<points.length && total<180;i++){
        const a=points[i-1],b=points[i],dx=b.x-a.x,dy=b.y-a.y,length=Math.hypot(dx,dy);
        if(length===0)continue;
        while(next<=travelled+length && total<180){
          const t=(next-travelled)/length,x=a.x+dx*t,y=a.y+dy*t,ux=dx/length,uy=dy/length;
          const center=L.point(x,y);
          if(map.getPixelBounds().contains(map.project(map.layerPointToLatLng(center)))){
            const coords=[[-5,3.5],[2,0],[-5,-3.5]].map(([forward,side])=>
              map.layerPointToLatLng(L.point(x+ux*forward-uy*side,y+uy*forward+ux*side)));
            L.polyline(coords,{pane:'routeArrows',color:'white',weight:2.4,opacity:1,interactive:false,lineCap:'round'}).addTo(arrows);
            total++;
          }
          next+=100;
        }
        travelled+=length;
      }
    }
  }
  function draw(points,mode,label,layer=routeLines,schematic=false){
    if(!points?.length)return;
    const connector=mode==='connection';
    const common={pane:'route',lineCap:'round',lineJoin:'round',interactive:false};
    L.polyline(points,{...common,color:'white',weight:connector?6:14,opacity:1}).addTo(layer);
    if(!connector)L.polyline(points,{...common,color:'#172c43',weight:10.5,opacity:.88}).addTo(layer);
    const line=L.polyline(points,{...common,color:connector?'#536579':palette[mode]||palette.public,
      weight:connector?2.8:7.5,opacity:1,dashArray:connector?'4 6':schematic?'12 9':mode==='walk'?'8 5':undefined,
      interactive:true,bubblingMouseEvents:false}).bindTooltip(el('span',label||mode),{pane:'routeLabels'}).addTo(layer);
    if(!connector&&!schematic)records.push({points,line,layer});
    return line;
  }
  function show(points){
    bounds=L.latLngBounds(points);
    $('route-legend').hidden=false;
    map.getContainer().classList.add('has-route');
    focus();updateArrows();
  }
  function clear(layer=routeLines){
    for(let i=records.length-1;i>=0;i--)if(records[i].layer===layer)records.splice(i,1);
    updateArrows();
    if(!records.length){bounds=null;$('route-legend').hidden=true;map.getContainer().classList.remove('has-route','route-focus');}
  }
  function focus(){map.getContainer().classList.toggle('route-focus',!$('route-legend').hidden&&$('focus-route').checked);}
  $('focus-route').onchange=focus;
  $('fit-route').onclick=()=>{if(bounds)fit(bounds,18);};
  map.on('zoomend moveend',updateArrows);
  return {draw,show,clear,palette};
})();
window.routeVisualReady=routeVisual;
