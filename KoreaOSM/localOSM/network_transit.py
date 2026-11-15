"""Timetable-free routing under an explicit continuous-service assumption.

Rail uses connected OSM track topology, bus uses directed passenger-car roads.
Public mode compares bus-only, rail-only and a rail core with optional terminal
bus feeders at either end. Candidates are geographically bounded, not a claim
of global optimization across every facility and arbitrary transfers.
"""
from contextlib import closing
import math
import time

if __package__:
    from .routing import coordinate,distance_m
else:
    from routing import coordinate,distance_m


class NetworkTransitRouter:
    def __init__(self,parent):
        self.parent=parent
        self._ground={}
        self._rides={}
        self.checked=0

    def snap_candidates(self,point,mode):
        """Bounded fallback for disconnected nearest OSM footway islands."""
        graph=self.parent.graph
        if not hasattr(graph,'connect') or not hasattr(graph,'incident'):
            return []
        dy=500/110000+1e-6;dx=dy/max(.001,math.cos(math.radians(point[0])))
        bit=1 if mode=='walk' else 4
        choices=[]
        with closing(graph.connect()) as db:
            for (i,) in db.execute('SELECT id FROM node_index WHERE min_lon<=? AND max_lon>=? AND min_lat<=? AND max_lat>=?',
                    (point[1]+dx,point[1]-dx,point[0]+dy,point[0]-dy)):
                if graph.incident[i]&bit:
                    p=graph.nodes[i];meters=distance_m(point,p)
                    if meters<=500:choices.append((meters,i,p))
        out=[]
        for _,_,p in sorted(choices):
            if all(distance_m(p,q)>=50 for q in out):out.append(p)
            if len(out)==5:break
        return out

    def ground(self,start,end,mode,objective):
        key=(tuple(start),tuple(end),mode,objective)
        if key in self._ground:return self._ground[key]
        leg=self.parent.ground_leg(start,end,mode,objective)
        if not leg['reachable']:
            # Keep the original facility coordinates. Only the <=500m snap
            # connectors change; never substitute a straight road journey.
            sources=self.snap_candidates(start,mode);targets=self.snap_candidates(end,mode)
            best=None
            for a in sources:
                for b in targets:
                    road=self.parent.graph.route(a,b,mode,objective=objective)
                    if not road['reachable']:continue
                    connectors=distance_m(start,road['start']['coordinate'])+distance_m(end,road['end']['coordinate'])
                    candidate=dict(leg,reachable=True,road=road,distance_m=road['distance_m']+connectors,
                        duration_s=math.ceil(road['duration_s']+connectors*3.6/4.5),
                        connector_distance_m=connectors,connector_duration_s=connectors*3.6/4.5,
                        alternate_snap=True)
                    # Road dictionaries are fresh per query. Their original
                    # snap distances refer to a/b, so replace them explicitly.
                    road['start']=dict(road['start'],distance_m=distance_m(start,road['start']['coordinate']))
                    road['end']=dict(road['end'],distance_m=distance_m(end,road['end']['coordinate']))
                    score=candidate['duration_s'] if objective=='time' else candidate['distance_m']
                    if best is None or score<best[0]:best=(score,candidate)
            if best:leg=best[1]
        self._ground[key]=leg
        return leg

    def ground_stage(self,start,end,mode,objective,label):
        part=self.ground(start,end,mode,objective)
        if not part['reachable']:return None
        return dict(kind='ground',mode=mode,label=label,part=part,
                    distance_m=part['distance_m'],duration_s=part['duration_s'])

    def ride(self,a,b,mode,objective,speed):
        key=(a['id'],b['id'],mode,objective,speed)
        if key in self._rides:return self._rides[key]
        self.checked+=1
        if a['id']==b['id']:
            self._rides[key]=None;return None
        shape=self.parent.ride_geometry.between(a['coordinate'],b['coordinate'],mode,objective,speed)
        expected='rail_infrastructure_preview' if mode=='rail' else 'bus_road_preview'
        if shape['geometry_kind']!=expected:
            # A schematic is display-only, never a traversable network edge.
            self._rides[key]=None;return None
        connectors=sum(distance_m(x,y) for x,y in shape['connectors'])
        moving=shape['distance_m']*3.6/speed if mode=='rail' else shape['road_duration_s']
        shape=dict(shape,schedule_confirmed=False,assumed_service=True,
                   message='시간표 무시 · 연결된 선로 상시 이용 가정' if mode=='rail' else '시간표 무시 · 도로로 연결된 터미널 간 버스 이용 가정')
        leg=dict(kind='ride',mode=mode,from_stop=a,to_stop=b,geometry=shape,
                 distance_m=shape['distance_m']+connectors,duration_s=moving+connectors*3.6/4.5,
                 connector_distance_m=connectors,connector_duration_s=connectors*3.6/4.5,
                 assumed_service=True,service_verified=False)
        self._rides[key]=leg
        return leg

    @staticmethod
    def score(legs,objective):
        return sum(x['duration_s'] if objective=='time' else x['distance_m'] for x in legs)

    def route(self,start,end,mode,access_mode,count,objective,speed):
        began=time.perf_counter()
        start,end=coordinate(start),coordinate(end)
        if mode not in ('rail_transit','bus_transit','public_transit'):raise ValueError('Invalid transit mode')
        if access_mode not in ('walk','drive'):raise ValueError('Station access mode must be walk or drive')
        if objective not in ('time','distance'):raise ValueError('Objective must be time or distance')
        if type(count) is not int or not 1<=count<=3:raise ValueError('candidate_count must be an integer between 1 and 3')
        if isinstance(speed,bool) or not isinstance(speed,(float,int)) or not math.isfinite(speed) or not 10<=speed<=350:
            raise ValueError('rail_speed_kmh must be between 10 and 350')
        result=dict(kind='door_to_door_transit',routing_model='network',mode=mode,objective=objective,
            reachable=False,complete=False,estimated=True,service_verified=False,schedule_used=False,
            departure=None,arrival=None,duration_s=None,distance_m=None,waiting_s=0,transfers=0,
            fare_krw=None,start_coordinate=start,end_coordinate=end,access_mode=access_mode,candidate_count=count,
            rail_speed_kmh=speed,network_legs=[],alternatives=[],start_candidates=[],end_candidates=[],
            scope='nearest candidate facilities; direct bus or rail core with optional local bus feeders',
            assumptions=['연결된 철도 시설망은 양방향 열차 이용 가능으로 가정',
                '도로가 이어지는 터미널 사이는 버스 이용 가능으로 가정',
                '시간표·운행일·승차 대기·환승 대기 0초',
                '열차 평균속도 설정값, 버스는 승용차 도로별 적용 속도 사용',
                '가까운 후보 범위에서 비교; 모든 역과 임의 횟수 환승의 전역 최적해는 아님'])
        if not self.parent.transit.available:
            result.update(error='transit_data_missing',message='역·터미널 데이터가 설치되지 않았습니다.')
            return result
        allowed=['rail'] if mode=='rail_transit' else ['bus'] if mode=='bus_transit' else ['rail','bus']
        starts={m:self.parent.nearest_stops(start,m,count) for m in allowed}
        ends={m:self.parent.nearest_stops(end,m,count) for m in allowed}
        result['start_candidates']=[s for m in allowed for s in starts[m]]
        result['end_candidates']=[s for m in allowed for s in ends[m]]
        choices=[]
        def consider(legs,label,a,b):
            if not legs or any(x is None for x in legs):return
            if not any(x['kind']=='ride' for x in legs):return
            choices.append((self.score(legs,objective),legs,label,a,b))
        # Rail first also provides a useful response when bus candidates fail.
        for ride_mode in allowed:
            for a in starts[ride_mode]:
                first=self.ground_stage(start,a['coordinate'],access_mode,objective,'출발지 → '+a['name'])
                for b in ends[ride_mode]:
                    last=self.ground_stage(b['coordinate'],end,access_mode,objective,b['name']+' → 목적지')
                    if first is None or last is None:continue
                    middle=self.ride(a,b,ride_mode,objective,speed)
                    consider([first,middle,last],'기차' if ride_mode=='rail' else '버스',a,b)
        if mode=='public_transit':
            # At most one rail core. A feeder can ride from a nearby departure
            # terminal to the terminal nearest that station, then walk across.
            # Reverse-direction egress uses independently routed road edges.
            def options(station,outbound):
                point=end if outbound else start
                direct=self.ground_stage(station['coordinate'] if outbound else point,
                    point if outbound else station['coordinate'],access_mode,objective,
                    station['name']+' → 목적지' if outbound else '출발지 → '+station['name'])
                candidates=[[direct]] if direct else []
                adjacent=self.parent.nearest_stops(station['coordinate'],'bus',1)
                if not adjacent:return candidates
                near=adjacent[0]
                # Local station interchange is bounded to a 3km radius.
                if near['proximity_m']>3000:return candidates
                transfer=self.ground_stage(station['coordinate'] if outbound else near['coordinate'],
                    near['coordinate'] if outbound else station['coordinate'],'walk',objective,
                    station['name']+' ↔ '+near['name']+' 도보 환승')
                if transfer is None:return candidates
                for terminal in (ends['bus'] if outbound else starts['bus']):
                    outer=self.ground_stage(terminal['coordinate'] if outbound else point,
                        point if outbound else terminal['coordinate'],access_mode,objective,
                        terminal['name']+' → 목적지' if outbound else '출발지 → '+terminal['name'])
                    a,b=(near,terminal) if outbound else (terminal,near)
                    bus=self.ride(a,b,'bus',objective,speed) if outer else None
                    if outer and bus:candidates.append([transfer,bus,outer] if outbound else [outer,bus,transfer])
                return candidates
            left={s['id']:options(s,False) for s in starts['rail']}
            right={s['id']:options(s,True) for s in ends['rail']}
            for a in starts['rail']:
                for b in ends['rail']:
                    if not left[a['id']] or not right[b['id']]:continue
                    rail=self.ride(a,b,'rail',objective,speed)
                    if rail is None:continue
                    for prefix in left[a['id']]:
                        for suffix in right[b['id']]:
                            legs=prefix+[rail]+suffix
                            if any(x['mode']=='bus' for x in legs):consider(legs,'기차 + 버스',a,b)
        result['candidate_pairs_checked']=self.checked
        if choices:
            choices.sort(key=lambda x:x[0]);_,legs,label,a,b=choices[0]
            result.update(reachable=True,complete=True,network_legs=legs,selected_start=a,selected_end=b,
                distance_m=sum(x['distance_m'] for x in legs),duration_s=sum(x['duration_s'] for x in legs),
                transfers=max(0,sum(x['kind']=='ride' for x in legs)-1),selected_label=label,
                message='시간표를 무시하고 연결된 교통망을 이용한다고 가정한 후보 범위 최단 경로입니다.')
            # Show the best candidate for each actually computed route family.
            for family in ('기차','버스','기차 + 버스'):
                matches=[x for x in choices if x[2]==family]
                if matches:
                    _,ls,_,_,_=matches[0]
                    result['alternatives'].append(dict(label=family,duration_s=sum(x['duration_s'] for x in ls),
                                                      distance_m=sum(x['distance_m'] for x in ls)))
        else:
            result.update(error='network_route_unreachable',message='선택한 가까운 시설 사이에서 도로·선로가 모두 이어지는 경로를 찾지 못했습니다. 가까운 3곳 비교 또는 다른 접근 수단을 선택하세요.')
        result['elapsed_ms']=round((time.perf_counter()-began)*1000,1)
        return result
