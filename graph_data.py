"""
1) 데이터 처리 부분
- nodes.csv (id, latitude, longitude), edges.csv (source, target, weight)를 읽어
  인접 리스트 그래프를 만든다. (WeightedAstar_visualization/graph_data.py 기반)
- road_route가 저장한 GraphML이 있으면 도로 곡선, 도로 이름, 종류도 간선에 붙인다.
- 표준 라이브러리만 쓴다. osmnx는 새 지역을 내려받을 때(road_map.py)만 필요하다.
"""
import csv
import heapq
import json
import math
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Tuple
from collections import deque
from dataclasses import dataclass, field

EARTH_RADIUS_M = 6371008.8  # 평균 지구 반지름 (weight 계산에 쓰인 값과 동일함을 확인함)


def haversine(lat1, lon1, lat2, lon2):
    """두 위경도 점 사이의 대원거리(m). 도로 데이터는 쓰지 않고 좌표만 사용."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


class Vehicle:
    WALK = "walk"
    DRIVE = "drive"
    BIKE = "bike"

@dataclass
class Road:
    """CSV 한 행. id는 0부터, CSV 행 번호(edge_row)는 id + 1이다."""
    id: int
    u: int
    v: int
    w: float
    coords: list = field(default_factory=list)  # 지도용 도로 굴곡: [(위도, 경도), ...]
    name: str = ""
    highway: str = ""
    permissions: Dict[Vehicle, bool] = field(default_factory=dict) # Dict instead of set to keep dataclass serializable
    length: float | None = None                  # 실제 길이(m). CSV에 length_m이 없으면 w (예전 CSV는 w가 m)

    def permits(self, vehicle: Vehicle):
        if vehicle in self.permissions:
            return self.permissions[vehicle]
        return False

@dataclass
class Graph:
    coords: dict = field(default_factory=dict)    # id -> (lat, lon)
    adj: dict = field(default_factory=dict)       # id -> {neighbor: weight}
    radj: dict = field(default_factory=dict)      # 역방향 인접 리스트
    edge_id: Dict[Tuple[int, int], int] = field(default_factory=dict)   # (u, v) -> 가장 짧은 평행 간선의 Road.id
    roads: List[Road] = field(default_factory=list)     # CSV 모든 행 (평행 간선, self-loop 포함)
    alpha: float = 0.0                            # min(weight / 직선거리)
    center: tuple | None = None
    radius: float | None = None
    network_type: str | None = None
    stats: dict = field(default_factory=dict)
    main_nodes: list = field(default_factory=list)  # 가장 큰 약연결 성분
    component: dict = field(default_factory=dict)
    elevation: dict = field(default_factory=dict)   # id -> 고도(m), road_elevation.csv가 있을 때만
    weight_unit: str = "m"                        # CSV weight 단위: m(도로 길이) 또는 s(이동 시간)
    max_speed: float = 1.0                        # weight_unit이 s일 때 직선거리(m)를 시간(s)으로 바꾸는 최고 속도(m/s)

    @property
    def basis(self):
        """간선 비용이 무엇인지 한 줄 설명."""
        if self.weight_unit == "s":
            return (f"이동 시간(초). 같은 두 정점 사이에 수단(도보·자전거·차량) 간선이 여럿이면 가장 빠른 것 "
                    f"· 직선거리 h = 직선거리 ÷ 최고 {self.max_speed * 3.6:.0f} km/h")
        return "도로 길이(m) · 직선거리 h = 직선거리"

    def neighbors(self, u):
        return self.adj.get(u, {}).items()

    def reverse_neighbors(self, u):
        return self.radj.get(u, {}).items()

    def straight(self, u, goal):
        """두 정점 사이 직선거리(m)."""
        la1, lo1 = self.coords[u]
        la2, lo2 = self.coords[goal]
        return haversine(la1, lo1, la2, lo2)

    def heuristic(self, u, goal):
        """직선거리 휴리스틱을 weight와 같은 단위로. s면 최고 속도로 나눈 시간이라 하한이 유지된다."""
        d = self.straight(u, goal)
        return d / self.max_speed if self.weight_unit == "s" else d

    def geographic(self, u, goal):
        """직선거리 × alpha. 가중치 단위가 거리가 아니어도 하한이 유지된다."""
        return self.alpha * self.straight(u, goal)

    def edges(self):
        for u, nbrs in self.adj.items():
            for v, w in nbrs.items():
                yield u, v, w

    def path_edges(self, path):
        return [self.edge_id[(a, b)] for a, b in zip(path, path[1:])]

    def default_endpoints(self):
        """지도 남서쪽 끝과 북동쪽 끝. 긴 경로가 나와 비교하기 좋다."""
        k = math.cos(math.radians(sum(c[0] for c in self.coords.values()) / len(self.coords)))
        score = lambda n: self.coords[n][1] * k + self.coords[n][0]
        return min(self.main_nodes, key=score), max(self.main_nodes, key=score)


def distances(graph, src, reverse=False):
    """src에서 도달 가능한 모든 정점까지의 거리. reverse면 각 정점에서 src까지의 거리."""
    dist = {src: 0.0}
    heap = [(0.0, src)]
    while heap:
        d, u = heapq.heappop(heap)
        if d != dist[u]:
            continue
        for v, w in (graph.reverse_neighbors(u) if reverse else graph.neighbors(u)):
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                heapq.heappush(heap, (nd, v))
    return dist


def _read_graphml(path):
    """road_route의 number_graph()와 같은 순서로 도로 곡선을 읽는다 (osmnx 불필요)."""
    ns = "{http://graphml.graphdrawing.org/xmlns}"
    root = ET.parse(path).getroot()
    keys = {k.get("id"): k.get("attr.name") for k in root.findall(ns + "key")}
    graph = root.find(ns + "graph")
    meta = {keys[d.get("key")]: d.text for d in graph.findall(ns + "data")}

    pos = {}
    for node in graph.findall(ns + "node"):
        data = {keys[d.get("key")]: d.text for d in node.findall(ns + "data")}
        pos[int(node.get("id"))] = (float(data["y"]), float(data["x"]))
    mapping = {osm: i for i, osm in enumerate(sorted(pos), 1)}

    rows = []
    for edge in graph.findall(ns + "edge"):
        data = {keys[d.get("key")]: d.text for d in edge.findall(ns + "data")}
        u, v, key = int(edge.get("source")), int(edge.get("target")), int(edge.get("id") or 0)
        points = [pos[u], pos[v]]
        if data.get("geometry"):
            points = [(float(lat), float(lon)) for lon, lat in re.findall(
                r"(-?\d+\.?\d*)\s+(-?\d+\.?\d*)", data["geometry"])]
        start = pos[u]
        squared = lambda p: (p[0] - start[0]) ** 2 + (p[1] - start[1]) ** 2
        if squared(points[-1]) < squared(points[0]):  # 일부 역방향 간선은 곡선 순서가 원래대로다
            points.reverse()
        rows.append((u, v, key, mapping[u], mapping[v], float(data["length"]), points,
                     (data.get("name") or "").strip("[]'"),
                     (data.get("highway") or "").strip("[]'")))
    rows.sort(key=lambda r: r[:3])
    return meta, rows


def load_graph(nodes_csv, edges_csv, graphml=None):
    g = Graph()
    with open(nodes_csv, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            nid = int(row["id"])
            g.coords[nid] = (float(row["latitude"]), float(row["longitude"]))
            g.adj.setdefault(nid, {})
            g.radj.setdefault(nid, {})

    n_self, n_dup, ratios, units, speeds = 0, 0, [], set(), [1e-9]
    with open(edges_csv, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            u, v, w = int(row["source"]), int(row["target"]), float(row["weight"])
            if u not in g.coords or v not in g.coords or not math.isfinite(w) or w < 0:
                raise ValueError(f"잘못된 간선입니다: {u} -> {v}, weight={w}")
            units.add(row.get("weight_unit") or "m")
            road = Road(len(g.roads), u, v, w, [g.coords[u], g.coords[v]], permissions=_edge_permissions(row),
                        length=float(row["length_m"]) if row.get("length_m") else w, highway=row.get("highway") or "")
            g.roads.append(road)
            if u == v:                 # 자기 자신으로 가는 간선: 최단경로에 의미 없음
                n_self += 1
                continue
            d = g.straight(u, v)
            if d > 0:
                ratios.append(w / d)
            if w > 0:   # 실제로 낼 수 있는 속도의 최댓값 (길이와 직선거리 중 큰 쪽으로 계산해 하한을 보장)
                speeds.append(max(road.length, d) / w)
            if v in g.adj[u]:          # 같은 두 노드 사이 여러 간선: 가장 짧은 것만 탐색에 사용
                n_dup += 1
                if w >= g.adj[u][v]:
                    continue
            g.adj[u][v] = w
            g.radj[v][u] = w
            g.edge_id[(u, v)] = road.id
    g.alpha = min(ratios, default=0.0)
    if len(units) > 1 or not units <= {"m", "s"}:
        raise ValueError(f"weight_unit이 한 가지(m 또는 s)여야 합니다: {sorted(units)}")
    g.weight_unit = units.pop() if units else "m"
    g.max_speed = max(speeds)

    curved = 0
    if graphml:
        meta, rows = _read_graphml(graphml)
        matches = len(rows) == len(g.roads) and all(
            r[3] == road.u and r[4] == road.v and abs(r[5] - road.w) < 1e-6
            for r, road in zip(rows, g.roads))
        if matches:
            for r, road in zip(rows, g.roads):
                road.coords, road.name, road.highway = r[6], r[7], r[8]
                curved += len(r[6]) > 2
            if meta.get("route_center"):
                g.center = tuple(json.loads(meta["route_center"]))
            if meta.get("route_radius_m"):
                g.radius = float(meta["route_radius_m"])
            g.network_type = meta.get("route_network_type")
        else:
            print("경고: GraphML과 CSV의 간선이 달라 도로 곡선을 쓰지 않습니다.")
            _copy_stairs_by_coords(g, rows)

    if g.center is None:
        n = len(g.coords)
        g.center = (sum(c[0] for c in g.coords.values()) / n, sum(c[1] for c in g.coords.values()) / n)

    from elevation import elevation_path, load_elevation
    z = load_elevation(elevation_path(nodes_csv))
    if z and set(g.coords) <= set(z):
        g.elevation = {n: z[n] for n in g.coords}

    _label_components(g)
    g.stats = {"nodes": len(g.coords),
               "edges": sum(len(n) for n in g.adj.values()),
               "csv_rows": len(g.roads),
               "self_loops_removed": n_self,
               "parallel_edges_merged": n_dup,
               "curved_roads": curved,
               "weak_components": len(set(g.component.values())),
               "main_component": len(g.main_nodes),
               "alpha": g.alpha,
               "has_elevation": bool(g.elevation),
               "weight_unit": g.weight_unit,
               "max_speed_kmh": g.max_speed * 3.6 if g.weight_unit == "s" else None}
    return g


def _label_components(g):
    sizes = {}
    for s in g.coords:
        if s in g.component:
            continue
        cid, g.component[s], count, q = len(sizes), len(sizes), 0, deque([s])
        while q:
            x = q.popleft()
            count += 1
            for y in list(g.adj[x]) + list(g.radj[x]):
                if y not in g.component:
                    g.component[y] = cid
                    q.append(y)
        sizes[cid] = count
    main = max(sizes, key=sizes.get)
    g.main_nodes = [n for n in g.coords if g.component[n] == main]

def _copy_stairs_by_coords(g, rows):
    """GraphML이 다른 그래프용이어도, 양 끝 좌표가 같은 간선에는 계단(highway=steps) 표시를 옮긴다.
    CSV에 highway 열이 생기면 필요 없다. GraphML이 덮는 범위 밖의 계단은 알 수 없다."""
    key = lambda p: (round(p[0], 6), round(p[1], 6))
    node_at = {key(c): n for n, c in g.coords.items()}
    stairs = set()
    for r in rows:
        if "steps" in r[8]:
            a, b = node_at.get(key(r[6][0])), node_at.get(key(r[6][-1]))
            if a is not None and b is not None:
                stairs |= {(a, b), (b, a)}
    for road in g.roads:
        if (road.u, road.v) in stairs and not road.highway:
            road.highway = "steps"


def _edge_permissions(edge: Dict[str, str | None]):
    permissions = {}
    match edge.get("road_use"):          # 예전 CSV(수단 열 없음)는 빈 dict -> 모든 간선을 도보로 본다
        case "walk_only": 
            permissions[Vehicle.WALK] = True
            permissions[Vehicle.DRIVE] = False
        case "drive_only": 
            permissions[Vehicle.WALK] = False
            permissions[Vehicle.DRIVE] = True
        case "both": 
            permissions[Vehicle.WALK] = True
            permissions[Vehicle.DRIVE] = True
    match edge.get("mode"):
        case "walk": 
            permissions[Vehicle.WALK] = True
        case "drive": 
            permissions[Vehicle.DRIVE] = True
        case "bike": 
            permissions[Vehicle.BIKE] = True
    return permissions
