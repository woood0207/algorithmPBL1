"""Shared railway/road paths without any claim of verified operating service.

The schedule model uses these as previews; the network model explicitly assumes
continuous service on connected paths and assigns its own estimated ride cost.
"""
import heapq
from collections import OrderedDict
import json
import math
from pathlib import Path
import threading

if __package__:
    from .routing import distance_m
else:
    from routing import distance_m


class RideGeometry:
    def __init__(self,road_graph,transit):
        self.road_graph,self.transit=road_graph,transit
        self._rail=None
        self._lock=threading.Lock()
        self._cache=OrderedDict()

    def _rail_graph(self):
        with self._lock:
            if self._rail is not None:
                return self._rail
            path=self.transit.directory/'rail_graph.json'
            if not path.exists():
                self._rail=([],[])
                return self._rail
            data=json.loads(path.read_text(encoding='utf-8'))
            if data.get('format')!='localosm-rail-preview-v1':
                raise ValueError('Unsupported rail display graph')
            expected=self.transit.metadata.get('source_sha256')
            if expected and expected!=data.get('source_sha256'):
                raise ValueError('Rail display graph does not match transit source')
            nodes=[n[1:] for n in data['nodes']]
            adjacency=[[] for _ in nodes]
            for u,v in data['edges']:
                length=distance_m(nodes[u],nodes[v])
                adjacency[u].append((v,length));adjacency[v].append((u,length))
            self._rail=(nodes,adjacency)
            return self._rail

    @staticmethod
    def schematic(start,end,mode,reason):
        return dict(mode=mode,coordinates=[start,end],connectors=[],geometry_kind='schematic_unverified_connection',
                    service_verified=False,duration_s=None,distance_m=None,message=reason)

    def rail(self,start,end,connector_factor=1):
        nodes,adjacency=self._rail_graph()
        if not nodes:
            return self.schematic(start,end,'rail','철도 시설망이 없어 역 사이 연결선으로 표시합니다.')
        def nearest(point):
            matches=[]
            for i,p in enumerate(nodes):
                # Cheap rectangle before exact great-circle distance (South Korea).
                if abs(p[0]-point[0])<.02 and abs(p[1]-point[1])<.03:
                    meters=distance_m(point,p)
                    if meters<=1500:
                        matches.append((meters,i))
            return sorted(matches)[:16]
        starts,ends=nearest(start),nearest(end)
        if not starts or not ends:
            return self.schematic(start,end,'rail','1.5 km 이내 연결 가능한 선로 후보가 없어 역 사이 연결선으로 표시합니다.')
        targets={n:meters*connector_factor for meters,n in ends}
        costs={n:meters*connector_factor for meters,n in starts};parents={n:None for _,n in starts}
        queue=[(cost,n) for n,cost in costs.items()];heapq.heapify(queue)
        best,goal=math.inf,None
        while queue:
            cost,u=heapq.heappop(queue)
            if cost!=costs[u]:
                continue
            if cost>=best:
                break
            if u in targets and cost+targets[u]<best:
                best,goal=cost+targets[u],u
            for v,w in adjacency[u]:
                candidate=cost+w
                if candidate<costs.get(v,math.inf):
                    costs[v]=candidate;parents[v]=u;heapq.heappush(queue,(candidate,v))
        if goal is None:
            return self.schematic(start,end,'rail','시설망에서 이어지는 선로를 찾지 못해 역 사이 연결선으로 표시합니다.')
        path=[];cursor=goal
        while cursor is not None:
            path.append(cursor);cursor=parents[cursor]
        path.reverse();coords=[nodes[n] for n in path]
        # A shared nearest track point alone is not a railway ride.
        if len(coords)<2:
            return self.schematic(start,end,'rail','서로 가까운 시설의 연결선입니다. 기차 운행은 확인되지 않았습니다.')
        return dict(mode='rail',coordinates=coords,connectors=[[start,coords[0]],[coords[-1],end]],
                    geometry_kind='rail_infrastructure_preview',service_verified=False,duration_s=None,
                    distance_m=sum(distance_m(a,b) for a,b in zip(coords,coords[1:])),
                    message='철도 시설망을 따른 예상 연결선 · 실제 운행·선로 방향·환승 미확인')

    def bus(self,start,end,objective='time'):
        road=self.road_graph.route(start,end,'drive',objective=objective)
        if not road['reachable']:
            return self.schematic(start,end,'bus','차량 도로망 연결이 없어 터미널 사이 연결선으로 표시합니다.')
        if len(road['coordinates'])<2:
            return self.schematic(start,end,'bus','두 시설이 같은 도로 연결점에 연결되어 시설 사이 연결선으로 표시합니다.')
        return dict(mode='bus',coordinates=road['coordinates'],
                    connectors=[[start,road['start']['coordinate']],[road['end']['coordinate'],end]],
                    geometry_kind='bus_road_preview',service_verified=False,duration_s=None,distance_m=road['distance_m'],
                    road_duration_s=road['duration_s'],
                    message='승용차 도로망 기준 버스 예상선 · 실제 버스노선·전용 통행조건 미확인')

    def between(self,start,end,mode,objective='time',rail_speed_kmh=None):
        key=(mode,tuple(start),tuple(end),objective,rail_speed_kmh)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return dict(self._cache[key])
        factor=rail_speed_kmh/4.5 if rail_speed_kmh and objective=='time' else 1
        result=self.rail(start,end,factor) if mode=='rail' else self.bus(start,end,objective) if mode=='bus' else self.schematic(
            start,end,'public','기차·버스 환승정보가 없어 시설 사이 연결선만 표시합니다.')
        with self._lock:
            self._cache[key]=result
            self._cache.move_to_end(key)
            while len(self._cache)>16:self._cache.popitem(last=False)
        return dict(result)

    def attach(self,result,mode):
        result['ride_previews']=[]
        if result.get('transit'):
            for leg in result['transit']['legs']:
                if leg['mode'] in ('rail','bus'):
                    preview=self.between(leg['from_stop']['coordinate'],leg['to_stop']['coordinate'],leg['mode'])
                    preview.update(from_stop=leg['from_stop'],to_stop=leg['to_stop'],schedule_confirmed=True)
                    result['ride_previews'].append(preview)
        else:
            start=result.get('selected_start') or result.get('nearest_start')
            end=result.get('selected_end') or result.get('nearest_end')
            if start and end and start['id']!=end['id']:
                selected=mode
                if mode=='all':
                    selected='rail' if start['kind']==end['kind']=='rail_station' else 'bus' if start['kind']==end['kind']=='bus_terminal' else 'all'
                preview=self.between(start['coordinate'],end['coordinate'],selected)
                preview.update(from_stop=start,to_stop=end,schedule_confirmed=False)
                result['ride_previews'].append(preview)
        return result
