"""Coordinate -> road access -> rail/bus -> road egress.

Default: timetable-free connected-network assumption. The explicit schedule
model retains the original earliest-arrival search and actual waiting times.

Nearest means great-circle proximity. The default uses exactly the nearest
facility at each end; optional nearby-candidate search is explicitly bounded.
Ground legs use the actual directed road graph, never a straight-line substitute.
Off-network snap links are separately labeled walking estimates.
"""
from contextlib import closing
import math
import time

if __package__:
    from .routing import coordinate,distance_m
    from .transit import timestamp,iso,bounded_integer
    from .ride_geometry import RideGeometry
else:
    from routing import coordinate,distance_m
    from transit import timestamp,iso,bounded_integer
    from ride_geometry import RideGeometry

TRANSIT_MODES={'rail_transit':'rail','bus_transit':'bus','public_transit':'all'}


class MultiModalRouter:
    def __init__(self,graph,transit):
        self.graph,self.transit=graph,transit
        self.ride_geometry=RideGeometry(graph,transit)

    def nearest_stops(self,point,mode,count,served_role=None):
        kind={'rail':'rail_station','bus':'bus_terminal','all':None}[mode]
        with closing(self.transit.connect()) as db:
            rows=db.execute('SELECT * FROM stops'+(' WHERE kind IN (?,?)' if kind else ''),
                            (kind,'interchange') if kind else ()).fetchall()
            served=None
            if served_role:
                column='from_stop' if served_role=='departure' else 'to_stop'
                served={r[0] for r in db.execute(f'SELECT DISTINCT {column} FROM journeys'+(' WHERE mode=?' if mode!='all' else ''),
                                                (mode,) if mode!='all' else ())}
        ranked=[]
        for row in rows:
            stop=self.transit.stop_dict(row)
            if served is not None and stop['id'] not in served:
                continue
            types=set(stop['details'].get('station','').split(';'))
            if stop['kind']=='rail_station' and types & {'subway','light_rail','monorail'} and 'train' not in types:
                continue  # A subway-only tag does not establish a mainline train station.
            stop['proximity_m']=distance_m(point,stop['coordinate'])
            ranked.append(stop)
        ranked.sort(key=lambda stop:(stop['proximity_m'],stop['id']))
        return [dict(stop,proximity_rank=i+1) for i,stop in enumerate(ranked[:count])]

    def ground_leg(self,start,end,mode,objective='time'):
        road=self.graph.route(start,end,mode,objective=objective)
        leg=dict(mode=mode,start_coordinate=start,end_coordinate=end,reachable=road['reachable'],road=road,
                 distance_m=None,duration_s=None,connector_distance_m=None,connector_duration_s=None,
                 geometry='road_graph_with_unverified_snap_links')
        if road['reachable']:
            connectors=road['start']['distance_m']+road['end']['distance_m']
            connector_seconds=connectors*3.6/4.5
            leg.update(distance_m=road['distance_m']+connectors,
                       duration_s=math.ceil(road['duration_s']+connector_seconds),
                       connector_distance_m=connectors,connector_duration_s=connector_seconds)
        return leg

    def route(self,start,end,mode,departure=None,access_mode='walk',candidate_count=1,
              min_transfer_s=600,boarding_s=300,objective='time',stop_policy='served',
              routing_model='network',rail_speed_kmh=100):
        if routing_model=='schedule':
            return self.scheduled_route(start,end,mode,departure,access_mode,candidate_count,
                                        min_transfer_s,boarding_s,objective,stop_policy)
        if routing_model!='network':
            raise ValueError('routing_model must be network or schedule')
        if __package__:
            from .network_transit import NetworkTransitRouter
        else:
            from network_transit import NetworkTransitRouter
        return NetworkTransitRouter(self).route(start,end,mode,access_mode,candidate_count,
                                                objective,rail_speed_kmh)

    def scheduled_route(self,start,end,mode,departure,access_mode='walk',candidate_count=1,
              min_transfer_s=600,boarding_s=300,objective='time',stop_policy='served'):
        began=time.perf_counter()
        start,end=coordinate(start),coordinate(end)
        if mode not in TRANSIT_MODES:
            raise ValueError('Choose rail_transit, bus_transit or public_transit')
        if objective!='time':
            raise ValueError('Rail/bus connections use earliest-arrival time, not distance optimization')
        if access_mode not in ('walk','drive'):
            raise ValueError('Station access mode must be walk or drive')
        if stop_policy not in ('served','all'):
            raise ValueError('stop_policy must be served or all')
        if isinstance(candidate_count,bool) or not isinstance(candidate_count,int) or not 1<=candidate_count<=3:
            raise ValueError('candidate_count must be an integer between 1 and 3')
        min_transfer_s=bounded_integer(min_transfer_s,'min_transfer_s')
        boarding_s=bounded_integer(boarding_s,'boarding_s')
        depart=timestamp(departure)
        coverage=self.transit.status()
        result=dict(kind='door_to_door_transit',mode=mode,objective='time',reachable=False,complete=False,
            departure=iso(depart),arrival=None,duration_s=None,start_coordinate=start,end_coordinate=end,
            access_mode=access_mode,candidate_count=candidate_count,scope='nearest geographic facilities; imported timetables only',
            nearest_start=None,nearest_end=None,access=None,egress=None,transit=None,coverage=coverage,
            start_candidates=[],end_candidates=[],ground_distance_m=None,known_ground_duration_s=None,
            message='',candidate_pairs_checked=0,elapsed_ms=0)
        def finish(**updates):
            out=dict(result,**updates)
            self.ride_geometry.attach(out,TRANSIT_MODES[mode])
            out['elapsed_ms']=round((time.perf_counter()-began)*1000,1)
            return out
        if not coverage['available']:
            return finish(error='transit_data_missing',message='역·터미널 데이터가 설치되지 않았습니다.')
        allowed=TRANSIT_MODES[mode]
        with closing(self.transit.connect()) as db:
            mode_count=db.execute('SELECT count(*) FROM journeys'+(' WHERE mode=?' if allowed!='all' else ''),
                                 (allowed,) if allowed!='all' else ()).fetchone()[0]
        filtered=stop_policy=='served' and mode_count>0
        result.update(stop_policy=stop_policy,served_filter_applied=filtered)
        starts=self.nearest_stops(start,allowed,candidate_count,'departure' if filtered else None)
        ends=self.nearest_stops(end,allowed,candidate_count,'arrival' if filtered else None)
        result.update(start_candidates=starts,end_candidates=ends,
                      nearest_start=starts[0] if starts else None,nearest_end=ends[0] if ends else None)
        if not starts or not ends:
            return finish(error='no_stops',message='선택한 수단의 역·터미널이 없습니다.')
        # Without schedules, preserve the unknown arrival time. A separate,
        # explicitly unverified ride preview provides visual continuity.
        limit=candidate_count if mode_count else 1
        access=[self.ground_leg(start,s['coordinate'],access_mode) for s in starts[:limit]]
        egress=[self.ground_leg(s['coordinate'],end,access_mode) for s in ends[:limit]]
        result.update(access=access[0],egress=egress[0])
        if access[0]['reachable'] and egress[0]['reachable']:
            result.update(ground_distance_m=access[0]['distance_m']+egress[0]['distance_m'],
                          known_ground_duration_s=access[0]['duration_s']+egress[0]['duration_s'])
        if not mode_count:
            missing_ground=not access[0]['reachable'] or not egress[0]['reachable']
            return finish(error='timetable_missing',message='도로 구간과 기차·버스 예상 연결선을 표시했습니다. 시간표가 없어 실제 운행과 전체 도착시간은 확인되지 않았습니다.'+
                          (' 일부 시설은 도로망 연결도 확인되지 않았습니다.' if missing_ground else ''))
        best,best_arrival=None,math.inf
        checked=0
        same_stop=False
        for i,a in enumerate(access):
            if not a['reachable']:
                continue
            station_arrival=depart+a['duration_s']
            for j,e in enumerate(egress):
                if not e['reachable']:
                    continue
                if starts[i]['id']==ends[j]['id']:
                    same_stop=True
                    continue
                checked+=1
                middle=self.transit.plan(starts[i]['id'],ends[j]['id'],iso(station_arrival),
                    min_transfer_s=min_transfer_s,boarding_s=boarding_s,modes=allowed)
                if not middle['reachable'] or not any(l['mode'] in ('rail','bus') for l in middle['legs']):
                    continue
                arrival=timestamp(middle['arrival'])+e['duration_s']
                if arrival<best_arrival:
                    best_arrival=arrival
                    best=(i,j,a,e,middle,station_arrival)
        result['candidate_pairs_checked']=checked
        if best is None:
            if not any(a['reachable'] for a in access) or not any(e['reachable'] for e in egress):
                return finish(error='ground_route_unreachable',message='가까운 역·터미널의 접근 또는 목적지 연결 도로 경로를 찾지 못했습니다. 지도에서 연결 실패 구간을 확인하세요.')
            return finish(error='same_station' if same_stop and not checked else 'no_cached_itinerary',
                message='가장 가까운 출발·도착 시설이 같습니다. 대중교통 승차가 없는 경로를 기차·버스 이용 경로로 계산하지 않습니다.' if same_stop and not checked else
                '가까운 역·터미널 사이에 연결 가능한 저장 시간표가 없습니다. 실제 미운행을 뜻하지 않습니다. 후보 3곳 비교 또는 시간표 추가를 사용하세요.')
        i,j,a,e,middle,reached=best
        result.update(access=a,egress=e,transit=middle,
            selected_start=starts[i],selected_end=ends[j],
            access_arrival=iso(reached),egress_departure=middle['arrival'],
            ground_distance_m=a['distance_m']+e['distance_m'],
            known_ground_duration_s=a['duration_s']+e['duration_s'])
        return finish(reachable=True,complete=True,arrival=iso(best_arrival),duration_s=best_arrival-depart,
            waiting_s=middle['waiting_s'],transfers=middle['transfers'],fare_krw=middle['fare_krw'],
            message='선택한 가까운 역·터미널 후보와 저장 시간표 범위에서 전체 도착시간이 가장 빠른 경로입니다.')
