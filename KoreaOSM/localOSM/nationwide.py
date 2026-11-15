"""Compact, exact bidirectional Dijkstra and disk-backed offline map queries.

Only Python's standard library is required at runtime. Distances are meters.
"""
from array import array
from contextlib import closing
import heapq
import json
import math
from pathlib import Path
import sqlite3
import sys
import time

if __package__:
    from .routing import RoadGraph, coordinate, distance_m, empty_result, mode_mask
else:
    from routing import RoadGraph, coordinate, distance_m, empty_result, mode_mask

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT / 'national_data'
ARRAYS = {'latitudes': 'd', 'longitudes': 'd', 'node_ids': 'q', 'offsets': 'I',
          'targets': 'I', 'lengths': 'd', 'masks': 'B', 'incident': 'B',
          'reverse_offsets': 'I', 'reverse_sources': 'I', 'reverse_edges': 'I'}


class Coordinates:
    def __init__(self, latitudes, longitudes):
        self.latitudes, self.longitudes = latitudes, longitudes

    def __len__(self):
        return len(self.latitudes)

    def __getitem__(self, i):
        if not 0 <= i < len(self):
            raise IndexError(i)
        return [self.latitudes[i], self.longitudes[i]]


def read_array(path, kind, count):
    out = array(kind)
    with path.open('rb') as stream:
        out.fromfile(stream, count)
        if stream.read(1):
            raise ValueError(f'Unexpected trailing data: {path.name}')
    if sys.byteorder != 'little':
        out.byteswap()
    return out


class NationalGraph(RoadGraph):
    def __init__(self, directory=DEFAULT_DATA, speed_directory=None):
        self.directory = Path(directory).resolve()
        self.metadata = json.loads((self.directory / 'manifest.json').read_text(encoding='utf-8'))
        if self.metadata.get('format') != 'localosm-national-v1':
            raise ValueError('Unsupported national graph format')
        for name, kind in ARRAYS.items():
            setattr(self, name, read_array(self.directory / f'{name}.bin', kind, self.metadata['arrays'][name]))
        self.nodes = Coordinates(self.latitudes, self.longitudes)
        self.source_format = 'national-csr'
        overview = self.directory / 'overview.json'
        self.overview = json.loads(overview.read_text(encoding='utf-8')) if overview.exists() else None
        self._validate()
        self.speed_directory = Path(speed_directory).resolve() if speed_directory else self.directory / 'speeds'
        self.speed_profiles = None
        self._time_factors = {}
        speed_manifest = self.speed_directory / 'manifest.json'
        if speed_directory or speed_manifest.exists():
            speed = json.loads(speed_manifest.read_text(encoding='utf-8'))
            if (speed.get('format') != 'localosm-speeds-v1' or speed.get('edges') != len(self.targets)
                    or speed.get('graph_sha256') != self.metadata.get('source_sha256')):
                raise ValueError('Speed data does not match the national routing graph')
            self.speed_profiles = speed['profiles']
            for item in self.speed_profiles:
                if any(isinstance(item[k],bool) or not isinstance(item[k],(float,int)) or not math.isfinite(item[k]) or item[k] < 0
                       for k in ('walk_kmh','bike_kmh','drive_kmh')):
                    raise ValueError('Invalid speed profile')
            self.edge_profiles = read_array(self.speed_directory / 'edge_profiles.bin','I',len(self.targets))
            self.edge_roads = read_array(self.speed_directory / 'edge_roads.bin','I',len(self.targets))
            if self.edge_profiles and max(self.edge_profiles) >= len(self.speed_profiles):
                raise ValueError('Invalid edge speed profile reference')
            self.metadata = dict(self.metadata, speed_data={k:speed[k] for k in ('rule_version','counts','defaults_kmh','walk_kmh','bike_kmh','conditional_policy')})
        self.metadata = dict(self.metadata, objectives=['distance','time'] if self.speed_profiles is not None else ['distance'])

    def _validate(self):
        n, m = len(self.nodes), len(self.targets)
        if (not n or len(self.node_ids) != n or len(self.longitudes) != n or len(self.incident) != n
                or len(self.offsets) != n + 1 or len(self.reverse_offsets) != n + 1
                or self.offsets[0] or self.reverse_offsets[0]
                or self.offsets[-1] != m or self.reverse_offsets[-1] != m
                or any(len(getattr(self, a)) != m for a in ('lengths', 'masks', 'reverse_sources', 'reverse_edges'))):
            raise ValueError('Incomplete national graph. Rebuild the national data directory.')

    def connect(self):
        return sqlite3.connect((self.directory / 'map.sqlite').as_uri() + '?mode=ro', uri=True)

    def nearest(self, point, mode='mixed', max_distance=500):
        point, mask = coordinate(point), mode_mask(mode)
        if isinstance(max_distance, bool) or not isinstance(max_distance, (int, float)) or not math.isfinite(max_distance) or max_distance < 0:
            raise ValueError('Maximum snap distance must be a finite non-negative number')
        # Conservative WGS84 query rectangle; actual acceptance uses haversine.
        dy = max_distance / 110000 + 1e-6
        dx = dy / max(.001, math.cos(math.radians(point[0])))
        best = None
        with closing(self.connect()) as db:
            for (i,) in db.execute('SELECT id FROM node_index WHERE min_lon<=? AND max_lon>=? AND min_lat<=? AND max_lat>=?',
                                   (point[1] + dx, point[1] - dx, point[0] + dy, point[0] - dy)):
                if self.incident[i] & mask:
                    meters = distance_m(point, self.nodes[i])
                    if meters <= max_distance and (best is None or (meters, i) < (best['distance_m'], best['node'])):
                        best = dict(node=i, coordinate=self.nodes[i], distance_m=meters)
        return best

    def shortest_path(self, start, end, mode='mixed', objective='distance'):
        """Exact directed bidirectional Dijkstra. No heuristic or search cutoff."""
        began = time.perf_counter()
        mask, n = mode_mask(mode), len(self.nodes)
        if objective not in ('distance','time'):
            raise ValueError('Objective must be distance or time')
        factors = None
        if objective == 'time' and self.speed_profiles is None:
            raise ValueError('Time routing requires speed data. Run build_speeds.py first.')
        if self.speed_profiles is not None:
            if mask not in self._time_factors:
                if __package__:
                    from .speed_rules import seconds_per_meter
                else:
                    from speed_rules import seconds_per_meter
                self._time_factors[mask] = seconds_per_meter(self.speed_profiles,mask)
            factors = self._time_factors[mask]
        if type(start) is not int or type(end) is not int or not 0 <= start < n or not 0 <= end < n:
            raise ValueError('Unknown endpoint node')
        if start == end:
            return dict(reachable=True, distance_m=0.0, path=[start], coordinates=[self.nodes[start]],
                        **self.describe_path([start], [], mask,objective), objective=objective, expanded=0, elapsed_ms=0)
        forward, backward = {start: 0.0}, {end: 0.0}
        previous, following = {}, {}
        queues = [[(0.0, start)], [(0.0, end)]]
        best, meeting, expanded = math.inf, None, 0
        while queues[0] and queues[1]:
            for q, dist in zip(queues, (forward, backward)):
                while q and q[0][0] != dist.get(q[0][1]):
                    heapq.heappop(q)
            if not all(queues) or queues[0][0][0] + queues[1][0][0] >= best:
                break
            side = 0 if queues[0][0][0] <= queues[1][0][0] else 1
            cost, u = heapq.heappop(queues[side])
            expanded += 1
            own, other = (forward, backward) if side == 0 else (backward, forward)
            offsets = self.offsets if side == 0 else self.reverse_offsets
            for j in range(offsets[u], offsets[u + 1]):
                edge = j if side == 0 else self.reverse_edges[j]
                if not self.masks[edge] & mask:
                    continue
                v = self.targets[j] if side == 0 else self.reverse_sources[j]
                factor = factors[self.edge_profiles[edge]*8+self.masks[edge]] if factors is not None else 1
                if not math.isfinite(factor):
                    continue
                weight = self.lengths[edge]*factor if objective == 'time' else self.lengths[edge]
                candidate = cost + weight
                if candidate < own.get(v, math.inf):
                    own[v] = candidate
                    (previous if side == 0 else following)[v] = (u, edge)
                    heapq.heappush(queues[side], (candidate, v))
                    total = candidate + other.get(v, math.inf)
                    if total < best:
                        best, meeting = total, v
        if meeting is None:
            result = empty_result()
        else:
            path, edges, cursor = [meeting], [], meeting
            while cursor != start:
                cursor, edge = previous[cursor]
                path.append(cursor)
                edges.append(edge)
            path.reverse()
            edges.reverse()
            cursor = meeting
            while cursor != end:
                cursor, edge = following[cursor]
                path.append(cursor)
                edges.append(edge)
            result = dict(reachable=True, distance_m=sum(self.lengths[e] for e in edges), path=path,
                          coordinates=[self.nodes[i] for i in path], **self.describe_path(path, edges, mask,objective))
        result.update(objective=objective, expanded=expanded, elapsed_ms=round((time.perf_counter() - began) * 1000, 1))
        return result

    def route(self, start, end, mode='mixed', max_snap_m=500, objective='distance'):
        mask = mode_mask(mode)
        if objective not in ('distance','time'):
            raise ValueError('Objective must be distance or time')
        if objective == 'time' and self.speed_profiles is None:
            raise ValueError('Time routing requires speed data. Run build_speeds.py first.')
        source = self.nearest(coordinate(start),mask,max_snap_m)
        target = self.nearest(coordinate(end),mask,max_snap_m)
        result = self.shortest_path(source['node'],target['node'],mask,objective) if source and target else empty_result()
        if source is None or target is None:
            result['error'] = 'snap_failed'
        if not result['reachable']:
            result.update(duration_s=None,speed_sections=[],duration_by_mode_s=dict.fromkeys(('walk','bike','drive'),0.0))
        result.update(start=source,end=target,mode=mask,backend='python',objective=objective,
                      algorithm='bidirectional_dijkstra',coverage='South Korea',time_model='speed_limit_free_flow')
        return result

    def road_records(self, ids, by_osm=False):
        if self.speed_profiles is None or not ids:
            return {}
        records = {}
        column = 'osm_id' if by_osm else 'id'
        with closing(sqlite3.connect((self.speed_directory / 'roads.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
            values = list(set(ids))
            for offset in range(0,len(values),900):
                chunk = values[offset:offset+900]
                for row in db.execute(f'SELECT id,osm_id,name,highway,forward_profile,backward_profile,tags FROM roads WHERE {column} IN ({",".join("?" for _ in chunk)})',chunk):
                    record = dict(zip(('id','osm_id','name','highway','forward_profile','backward_profile','tags'),row))
                    records[record[column]] = record
        return records

    def describe_path(self,path,edges,allowed,objective):
        if self.speed_profiles is None:
            return dict(**super()._describe_modes(path,edges,allowed),duration_s=None,speed_sections=[])
        names, bits = ('walk','bike','drive'),(1,2,4)
        parents, costs = [], [0,0,0]
        for step,edge in enumerate(edges):
            p = self.speed_profiles[self.edge_profiles[edge]]
            speeds = [p[f'{name}_kmh'] if self.masks[edge]&allowed&bit else 0 for name,bit in zip(names,bits)]
            fastest = max(speeds)
            row, next_costs = [-1]*3,[math.inf]*3
            for mode,speed in enumerate(speeds):
                if speed > 0 and (objective != 'time' or speed == fastest):
                    before = min(range(3),key=lambda j:costs[j]+(j!=mode))
                    next_costs[mode] = 0 if step == 0 else costs[before]+(before!=mode)
                    row[mode] = before
            parents.append(row)
            costs = next_costs
        chosen, last = [0]*len(edges),min(range(3),key=lambda j:costs[j])
        for i in range(len(edges)-1,-1,-1):
            chosen[i],last = last,parents[i][last]
        segments, legs, sections = [], [], []
        totals, times = dict.fromkeys(names,0.0),dict.fromkeys(names,0.0)
        drive_osm, drive_default, conditional_distance = 0.0,0.0,0.0
        for step,edge in enumerate(edges):
            name, bit, length = names[chosen[step]],bits[chosen[step]],self.lengths[edge]
            pid, rid = self.edge_profiles[edge],self.edge_roads[edge]
            p = self.speed_profiles[pid]
            speed = p[f'{name}_kmh']
            seconds = length*3.6/speed
            source = p['drive_source'] if name == 'drive' else 'model'
            limit = p['drive_limit_kmh'] if name == 'drive' else p['bike_limit_kmh'] if name == 'bike' else None
            segments.append(dict(source=path[step],target=path[step+1],edge=edge,mode=name,mode_bit=bit,
                                 distance_m=length,duration_s=seconds,speed_kmh=speed,speed_limit_kmh=limit,speed_source=source))
            totals[name] += length
            times[name] += seconds
            if name == 'drive':
                if source == 'osm':
                    drive_osm += length
                else:
                    drive_default += length
            if p['conditional_conservative']:
                conditional_distance += length
            if not legs or legs[-1]['mode'] != name:
                legs.append(dict(mode=name,distance_m=0.0,duration_s=0.0,coordinates=[self.nodes[path[step]]]))
            legs[-1]['distance_m'] += length
            legs[-1]['duration_s'] += seconds
            legs[-1]['coordinates'].append(self.nodes[path[step+1]])
            if not sections or (sections[-1]['road'],sections[-1]['profile'],sections[-1]['mode']) != (rid,pid,name):
                sections.append(dict(road=rid,profile=pid,mode=name,distance_m=0.0,duration_s=0.0,speed_kmh=speed,
                    speed_limit_kmh=limit,min_speed_kmh=p['min_drive_kmh'] if name=='drive' else None,speed_source=source,
                    conditional_conservative=p['conditional_conservative'],unparsed_tag=p['unparsed_tag'],from_index=step,to_index=step))
            sections[-1]['distance_m'] += length
            sections[-1]['duration_s'] += seconds
            sections[-1]['to_index'] = step+1
        records = self.road_records([s['road'] for s in sections])
        for section in sections:
            road = records.get(section['road'],{})
            section.update(name=road.get('name',''),highway=road.get('highway',''),osm_id=road.get('osm_id'))
        return dict(segments=segments,legs=legs,distance_by_mode_m=totals,duration_by_mode_s=times,
                    duration_s=sum(times.values()),mode_changes=max(0,len(legs)-1),speed_sections=sections,
                    speed_coverage=dict(drive_osm_distance_m=drive_osm,drive_default_distance_m=drive_default,
                                        conditional_conservative_distance_m=conditional_distance))

    def enrich_features(self,features):
        records = self.road_records([f['properties'].get('osm_id') for f in features if f['properties'].get('kind')=='road'],by_osm=True)
        for f in features:
            road = records.get(f['properties'].get('osm_id'))
            if road:
                f['properties']['speeds'] = {direction:self.speed_profiles[road[direction+'_profile']] if road[direction+'_profile'] is not None else None
                                             for direction in ('forward','backward')}
        return features

    def features(self, bounds, zoom):
        west, south, east, north = bounds
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError('Invalid map bounds')
        if not math.isfinite(zoom) or not 0 <= zoom <= 22:
            raise ValueError('Invalid zoom')
        if zoom < 10 and self.overview is not None:
            return dict(type='FeatureCollection',features=[f for f in self.overview['features'] if f['properties']['min_zoom'] <= zoom],truncated=False,overview=True)
        limit = 8000
        with closing(self.connect()) as db:
            rows = db.execute('''SELECT f.geojson FROM feature_index AS r JOIN features AS f ON f.id=r.id
                WHERE r.min_lon<=? AND r.max_lon>=? AND r.min_lat<=? AND r.max_lat>=? AND f.min_zoom<=?
                ORDER BY f.min_zoom, f.id LIMIT ?''', (east, west, north, south, zoom, limit + 1)).fetchall()
        return dict(type='FeatureCollection', features=self.enrich_features([json.loads(r[0]) for r in rows[:limit]]), truncated=len(rows) > limit)

    def search_places(self, query):
        query = query.strip()[:100]
        if not query:
            return []
        literal = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        with closing(self.connect()) as db:
            return [dict(name=name, coordinate=[lat, lon], kind=kind) for name, lat, lon, kind in db.execute(
                "SELECT name,lat,lon,kind FROM places WHERE name LIKE ? ESCAPE '\\' ORDER BY rank,name LIMIT 20", ('%' + literal + '%',))]
