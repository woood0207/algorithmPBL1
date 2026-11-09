r"""지역 다운로드부터 번호 그래프, 다익스트라, 지도까지 실행하는 단일 파일.

설치: python -m pip install 'osmnx>=2.1.1,<3' 'folium>=0.19,<1'
예시:
  python road_route.py --center 36.01243647 129.32180209 --start 1 --end 100 --serve
  python road_route.py --load-graph output/road_route/roads.graphml --start 10 --end 200 --serve

--start-coord 위도 경도 / --end-coord 위도 경도로도 출발·도착 지정 가능.
출발·도착을 생략하면 전체 그래프와 CSV만 생성합니다. --serve는 로컬 지도 서버 실행.
정점·간선 CSV는 이 파이썬 파일과 같은 폴더에 저장합니다.
다른 프로젝트 파일을 import하지 않습니다. 외부 패키지는 OSMnx와 Folium입니다.
"""
from __future__ import annotations

import argparse
import csv
import heapq
import json
import math
import re
import time
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse

BASE_DIR = Path(__file__).resolve().parent
ACCESS_TAGS = ("highway", "access", "vehicle", "motor_vehicle", "motorcar", "foot", "motorroad", "bicycle")
ACCESS_TAGS += tuple(f"{tag}:conditional" for tag in ACCESS_TAGS[1:])
ROAD_USE_LABELS = {"walk_only": "Walking only", "drive_only": "Driving only",
                   "both": "Walking and driving", "neither": "Neither walking nor driving",
                   "unknown": "Unknown or conditional"}
ROAD_USE_COLORS = {"walk_only": "#16a34a", "drive_only": "#2563eb",
                   "both": "#7c3aed", "neither": "#dc2626", "unknown": "#64748b"}
MODE_LABELS = {"drive": "Driving", "bike": "Cycling", "walk": "Walking"}
MODE_COLORS = {"drive": "#2563eb", "bike": "#7c3aed", "walk": "#16a34a"}
DEFAULT_SPEEDS = {"drive": 40.0, "bike": 15.0, "walk": 5.0}
DEFAULT_FARES = {"taxi_base_won": 4500.0, "taxi_base_distance_m": 1700.0,
                 "taxi_step_m": 128.0, "taxi_step_won": 100.0,
                 "scooter_unlock_won": 1000.0, "scooter_won_per_km": None,
                 "scooter_won_per_minute": 180.0}
SPEED_TAGS = ("maxspeed", "maxspeed:forward", "maxspeed:backward", "maxspeed:bicycle")


def parse_speed_limit(value):
    """숫자 km/h·mph를 해석하며 여러 값은 보수적으로 최솟값을 사용합니다."""
    if isinstance(value, (list, tuple)):
        parsed = [parse_speed_limit(v) for v in value]
        valid = [v for v in parsed if v is not None]
        return min(valid) if valid else None
    values = []
    for part in str(value).lower().split(";"):
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(mph|km/h|kph)?\s*", part)
        if match:
            speed = float(match[1]) * (1.609344 if match[2] == "mph" else 1)
            if math.isfinite(speed) and speed > 0:
                values.append(speed)
    return min(values) if values else None


def mode_permissions(data):
    """OSM 통행 태그 우선, 누락 시 도로 종류의 일반적인 기본값으로 분류.

    조건부/혼합/알 수 없는 값은 허용으로 간주하지 않습니다.
    도로 자체의 이용 분류이며 일방통행 및 회전 제한 판정은 아닙니다.
    """
    def scalar(key):
        value = data.get(key)
        if isinstance(value, (list, tuple, set)):
            values = {str(v).strip().lower() for v in value}
            return next(iter(values)) if len(values) == 1 else "__mixed__"
        return str(value).strip().lower() if value is not None else None

    highway = scalar("highway")
    ordinary = {"residential", "living_street", "service", "unclassified",
                "primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link"}
    foot_default = (True if highway in ordinary | {"footway", "pedestrian", "steps", "path"}
                    else False if highway in {"motorway", "motorway_link"} else None)
    drive_default = (True if highway in ordinary | {"motorway", "motorway_link", "trunk", "trunk_link"}
                     else False if highway in {"footway", "pedestrian", "steps", "path", "cycleway"} else None)
    if scalar("motorroad") == "yes":
        foot_default = False
    bike_default = (True if highway in ordinary | {"cycleway"}
                    else False if highway in {"motorway", "motorway_link", "steps", "footway"} else None)
    if scalar("motorroad") == "yes":
        bike_default = False

    def allowed(keys, default):
        for key in keys:
            if data.get(f"{key}:conditional"):
                return None
            value = scalar(key)
            if value is None:
                continue
            if value in {"yes", "designated", "official", "permissive"}:
                return True
            if value in {"no", "private", "use_sidepath"}:
                return False
            return None
        return default

    walk = allowed(("foot", "access"), foot_default)
    drive = allowed(("motorcar", "motor_vehicle", "vehicle", "access"), drive_default)
    bike = allowed(("bicycle", "vehicle", "access"), bike_default)
    return {"drive": drive, "bike": bike, "walk": walk}


def classify_road_use(data):
    permissions = mode_permissions(data)
    walk, drive = permissions["walk"], permissions["drive"]
    if walk is None or drive is None:
        return "unknown"
    return {(True, True): "both", (True, False): "walk_only",
            (False, True): "drive_only", (False, False): "neither"}[walk, drive]


@dataclass
class Edge:
    source: int
    target: int
    weight: float  # 원본 OSMnx 도로 길이(m)
    coordinates: list[tuple[float, float]]  # 지도용 도로 굴곡: (위도, 경도)
    road_use: str = "unknown"
    mode: str = "walk"
    speed_kph: float | None = None
    speed_limit_kph: float | None = None

    @property
    def travel_time_s(self):
        speed = self.speed_kph if self.speed_kph is not None else DEFAULT_SPEEDS[self.mode]
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError("이동 속도는 유한한 양수여야 합니다.")
        return self.weight * 3.6 / speed


def check_coordinate(coordinate):
    lat, lon = coordinate
    if not (-90 < lat < 90 and -180 <= lon <= 180):
        raise ValueError("좌표는 위도(-90~90), 경도(-180~180) 순서로 입력하세요. 쉼표는 넣지 않습니다.")


def make_boundary(center, radius):
    """WGS84 타원체에서 중심으로부터 radius m 떨어진 원형 경계를 근사합니다."""
    from pyproj import Geod
    from shapely.geometry import Polygon

    geod = Geod(ellps="WGS84")
    lat, lon = center
    ring = [geod.fwd(lon, lat, bearing, radius)[:2] for bearing in range(0, 360, 2)]
    if max(x for x, _ in ring) - min(x for x, _ in ring) > 180:
        raise ValueError("날짜변경선을 가로지르는 영역은 지원하지 않습니다.")
    return Polygon(ring)


def download_graph(center, radius=1000, network_type="all", timeout=180, server=None):
    """OSM 도로망 다운로드. 연결 실패/502/503 등은 대기 후 대체 서버에서 시도합니다."""
    import osmnx as ox
    import requests
    from osmnx._errors import ResponseStatusCodeError

    boundary = make_boundary(center, radius)
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(BASE_DIR / "cache")
    ox.settings.log_console = True
    ox.settings.requests_timeout = timeout
    ox.settings.useful_tags_way = list(dict.fromkeys(ox.settings.useful_tags_way + list(ACCESS_TAGS) + list(SPEED_TAGS)))
    servers = [server.rstrip("/")] if server else [
        "https://overpass.private.coffee/api", "https://overpass-api.de/api"]
    previous_url, last_error = ox.settings.overpass_url, None
    try:
        for attempt in range(2):
            for endpoint in servers:
                if last_error is not None:
                    print("일시적인 서버 오류입니다. 30초 후 다시 시도합니다.", flush=True)
                    time.sleep(30)
                ox.settings.overpass_url = endpoint
                print(f"도로망 서버: {endpoint} (시도 {attempt + 1}/2)", flush=True)
                try:
                    graph = ox.graph_from_polygon(boundary, network_type=network_type,
                                                  simplify=False, retain_all=True, truncate_by_edge=False)
                    # 이용 속성이 달라지는 지점을 단순화로 병합하지 않습니다.
                    graph = ox.simplify_graph(graph, edge_attrs_differ=list(ACCESS_TAGS + SPEED_TAGS))
                    graph.graph.update(route_center=json.dumps(center), route_radius_m=radius,
                                       route_network_type=network_type)
                    return graph
                except ResponseStatusCodeError as error:
                    if not re.search(r"\b(429|500|502|503|504)\b", str(error)):
                        raise
                    last_error = error
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as error:
                    last_error = error
                print(f"요청 실패: {str(last_error)[:200]}", flush=True)
        raise ValueError(f"도로망 서버 요청에 실패했습니다. 잠시 후 재시도하세요. {str(last_error)[:200]}") from last_error
    finally:
        ox.settings.overpass_url = previous_url


def number_graph(graph, speeds=None, drive_factor=0.8):
    """OSM ID 오름차순으로 1부터 번호를 부여하고 방향·평행 간선을 보존합니다."""
    speeds = dict(DEFAULT_SPEEDS, **(speeds or {}))
    if any(not math.isfinite(v) or v <= 0 for v in speeds.values()) or not math.isfinite(drive_factor) or not 0 < drive_factor <= 1:
        raise ValueError("속도는 유한한 양수, 자동차 속도 계수는 0 초과 1 이하여야 합니다.")
    if not graph or graph.number_of_edges() == 0:
        raise ValueError("이 지역에 추출 가능한 도로망이 없습니다.")
    for _, data in graph.nodes(data=True):
        for key in list(data):
            if key == "name" or key.startswith("name:") or key in {
                    "name_source", "original_node_name", "nearby_building", "building_distance_m"}:
                data.pop(key)
    mapping = {osm_id: i for i, osm_id in enumerate(sorted(graph.nodes, key=int), 1)}
    nodes = {mapping[osm_id]: (float(graph.nodes[osm_id]["y"]), float(graph.nodes[osm_id]["x"]))
             for osm_id in mapping}
    edges = []
    mode_edges = []
    for u, v, key, data in sorted(graph.edges(keys=True, data=True), key=lambda e: (int(e[0]), int(e[1]), int(e[2]))):
        road_use = classify_road_use(data)
        permissions = mode_permissions(data)
        modes = [mode for mode in MODE_LABELS if permissions[mode] is True
                 and ("mode" not in data or data["mode"] == mode)]
        if not modes:
            continue
        source, target = mapping[u], mapping[v]
        weight = float(data["length"])
        if not math.isfinite(weight) or weight < 0:
            raise ValueError(f"간선 {u} → {v}의 거리가 유효하지 않습니다.")
        geometry = data.get("geometry")
        coordinates = ([(float(p[1]), float(p[0])) for p in geometry.coords]
                       if geometry is not None else [nodes[source], nodes[target]])
        # 일부 양방향 간선의 geometry는 역방향 간선에도 원래 순서로 저장됩니다.
        start = nodes[source]
        squared = lambda p: (p[0] - start[0]) ** 2 + (p[1] - start[1]) ** 2
        if squared(coordinates[-1]) < squared(coordinates[0]):
            coordinates.reverse()
        for mode in modes:
            attrs = dict(data, road_use=road_use, mode=mode)
            limit = None
            if mode in {"drive", "bike"}:
                limit = parse_speed_limit([data.get(tag) for tag in SPEED_TAGS[:3]])
                if mode == "bike":
                    bike_limit = parse_speed_limit(data.get("maxspeed:bicycle"))
                    limit = min(limit, bike_limit) if limit and bike_limit else limit or bike_limit
            speed = min(speeds[mode], limit) if limit is not None else speeds[mode]
            if mode == "drive":
                speed *= drive_factor
            attrs.update(speed_kph=speed, travel_time_s=weight * 3.6 / speed,
                         speed_limit_source="osm_numeric_min" if limit is not None else "fallback")
            attrs.pop("speed_limit_kph", None)
            if limit is not None:
                attrs["speed_limit_kph"] = limit
            mode_edges.append((u, v, attrs))
            edges.append(Edge(source, target, weight, coordinates, road_use, mode, speed, limit))
    graph.remove_edges_from(list(graph.edges(keys=True)))
    for u, v, attrs in mode_edges:
        graph.add_edge(u, v, **attrs)
    graph.graph["edge_mode_schema"] = "drive_bike_walk_v1"
    graph.graph["route_speed_config"] = json.dumps(dict(speeds_kph=speeds, drive_factor=drive_factor))
    return nodes, edges, mapping


def save_numbered_csv(nodes, edges, output_dir, objective="time"):
    """정점 CSV와 도로 이용 분류를 포함한 간선 CSV를 저장합니다."""
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "road_nodes.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["id", "latitude", "longitude"])
        writer.writerows((node, lat, lon) for node, (lat, lon) in nodes.items())
    with (output_dir / "road_edges.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["source", "target", "weight", "road_use", "mode", "length_m", "speed_kph", "speed_limit_kph", "travel_time_s", "weight_unit"])
        writer.writerows((e.source, e.target, e.travel_time_s if objective == "time" else e.weight,
                          e.road_use, e.mode, e.weight, e.speed_kph or DEFAULT_SPEEDS[e.mode],
                          e.speed_limit_kph, e.travel_time_s, "s" if objective == "time" else "m") for e in edges)


def dijkstra(nodes, edges, start, end, modes=None, objective="distance"):
    """(최단 거리, 경로 정점 ID 목록, 선택한 간선 순번 목록)을 반환합니다.

    간선 순번은 CSV의 헤더를 제외하고 1부터 셉니다.
    도달 불가: (math.inf, [], []). 출발=도착: (0.0, [start], []).
    """
    if start not in nodes or end not in nodes:
        raise ValueError(f"출발 {start} 또는 도착 {end}가 정점 목록에 없습니다.")
    if objective not in {"time", "distance"}:
        raise ValueError("최적화 기준은 time 또는 distance여야 합니다.")
    allowed_modes = set(MODE_LABELS if modes is None else modes)
    if not allowed_modes or not allowed_modes <= MODE_LABELS.keys():
        raise ValueError("이동 수단은 drive, bike, walk 중 하나 이상이어야 합니다.")
    adjacency = {node: [] for node in nodes}
    for row, edge in enumerate(edges, 1):
        if edge.source not in nodes or edge.target not in nodes:
            raise ValueError(f"간선 {row}의 끝점이 존재하지 않습니다.")
        if not math.isfinite(edge.weight) or edge.weight < 0:
            raise ValueError("다익스트라 가중치는 유한한 0 이상이어야 합니다.")
        if edge.mode not in allowed_modes:
            continue
        adjacency[edge.source].append((edge.target, edge.travel_time_s if objective == "time" else edge.weight, row))
    distances = {node: math.inf for node in nodes}
    distances[start] = 0.0
    previous, queue = {}, [(0.0, start)]
    while queue:
        current_distance, current = heapq.heappop(queue)
        if current_distance > distances[current]:
            continue
        if current == end:
            break
        for neighbor, weight, row in adjacency[current]:
            candidate = current_distance + weight
            if candidate < distances[neighbor]:
                distances[neighbor] = candidate
                previous[neighbor] = (current, row)
                heapq.heappush(queue, (candidate, neighbor))
    if math.isinf(distances[end]):
        return math.inf, [], []
    path, edge_rows, current = [end], [], end
    while current != start:
        current, row = previous[current]
        path.append(current)
        edge_rows.append(row)
    return distances[end], path[::-1], edge_rows[::-1]


def mixed_shortest_route(nodes, edges, start, end, modes=None, objective="distance", waypoints=None):
    """정점에서 자유롭게 수단을 바꾸는 최단거리 경로와 수단별 구간을 반환합니다."""
    allowed = list(dict.fromkeys(MODE_LABELS if modes is None else modes))
    stops = [start, *(waypoints or []), end]
    if any(node not in nodes for node in stops):
        raise ValueError("출발·경유·도착 중 존재하지 않는 정점 ID가 있습니다.")
    cost, path, rows, sections = 0.0, [start], [], []
    for index, (origin, destination) in enumerate(zip(stops, stops[1:]), 1):
        section_cost, section_path, section_rows = dijkstra(nodes, edges, origin, destination, allowed, objective)
        sections.append(dict(index=index, source=origin, target=destination,
                             reachable=bool(section_path), cost=section_cost if section_path else None,
                             distance_m=sum(edges[row - 1].weight for row in section_rows) if section_path else None,
                             time_s=sum(edges[row - 1].travel_time_s for row in section_rows) if section_path else None))
        if not section_path:
            cost, path, rows = math.inf, [], []
            break
        cost += section_cost
        path.extend(section_path[1:])
        rows.extend(section_rows)
    segments, legs = [], []
    totals = {mode: 0.0 for mode in MODE_LABELS}
    times = {mode: 0.0 for mode in MODE_LABELS}
    for row in rows:
        edge = edges[row - 1]
        segments.append(dict(edge_row=row, source=edge.source, target=edge.target,
                             weight_m=edge.weight, road_use=edge.road_use, mode=edge.mode,
                             speed_kph=edge.speed_kph or DEFAULT_SPEEDS[edge.mode],
                             speed_limit_kph=edge.speed_limit_kph, time_s=edge.travel_time_s))
        totals[edge.mode] += edge.weight
        times[edge.mode] += edge.travel_time_s
        if legs and legs[-1]["mode"] == edge.mode:
            legs[-1]["target"] = edge.target
            legs[-1]["distance_m"] += edge.weight
            legs[-1]["time_s"] += edge.travel_time_s
            legs[-1]["edge_rows"].append(row)
        else:
            legs.append(dict(source=edge.source, target=edge.target, mode=edge.mode,
                             distance_m=edge.weight, time_s=edge.travel_time_s, edge_rows=[row]))
    return dict(distance_m=sum(totals.values()) if path else math.inf,
                time_s=sum(times.values()) if path else math.inf, cost=cost,
                path=path, edge_rows=rows, segments=segments, legs=legs,
                distance_by_mode_m=totals, mode_changes=max(0, len(legs) - 1),
                time_by_mode_s=times, allowed_modes=allowed, objective=objective, transfer_cost=0,
                waypoint_ids=list(waypoints or []), stop_ids=stops, sections=sections)


def add_route_fares(result, fare_config=None):
    """선택된 경로에 거리 요금을 붙입니다. 요금으로 경로를 재최적화하지 않습니다."""
    config = dict(DEFAULT_FARES, **(fare_config or {}))
    if fare_config and fare_config.get("scooter_won_per_km") is not None and "scooter_won_per_minute" not in fare_config:
        config["scooter_won_per_minute"] = None
    for key, value in config.items():
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError("요금 설정은 유한한 0 이상 숫자여야 합니다.")
    if config["taxi_step_m"] <= 0:
        raise ValueError("택시 거리 과금 단위는 0보다 커야 합니다.")
    rates = [config["scooter_won_per_km"], config["scooter_won_per_minute"]]
    if sum(rate is not None for rate in rates) > 1 or (config["scooter_unlock_won"] is None) != all(rate is None for rate in rates):
        raise ValueError("킥보드 잠금해제 요금과 거리 또는 시간 요금 하나를 지정하세요.")
    totals = {mode: 0.0 for mode in MODE_LABELS}
    missing = set()
    for leg in result["legs"]:
        distance, mode = leg["distance_m"], leg["mode"]
        if distance <= 0 or mode == "walk":
            fare = 0.0
        elif mode == "drive":
            extra = max(0.0, distance - config["taxi_base_distance_m"])
            fare = config["taxi_base_won"] + math.ceil(round(extra / config["taxi_step_m"], 10)) * config["taxi_step_won"]
        elif config["scooter_won_per_km"] is not None:
            fare = config["scooter_unlock_won"] + distance / 1000 * config["scooter_won_per_km"]
            fare = math.ceil(round(fare, 8))
        elif config["scooter_won_per_minute"] is not None:
            fare = math.ceil(round(config["scooter_unlock_won"] + leg["time_s"] / 60 * config["scooter_won_per_minute"], 8))
        else:
            fare = None
            missing.add(mode)
        leg["fare_won"] = fare
        if fare is not None:
            totals[mode] += fare
    reachable = result.get("reachable", bool(result["path"]))
    result.update(estimated_fare_won=sum(totals.values()) if reachable and not missing else None,
                  known_fare_subtotal_won=sum(totals.values()) if reachable else None,
                  fare_by_mode_won={mode: None if mode in missing or not reachable else total for mode, total in totals.items()},
                  fare_complete=reachable and not missing, unconfigured_fare_modes=sorted(missing),
                  fare_config=config, currency="KRW", fare_model="per_contiguous_mode_leg",
                  taxi_tariff_basis="Pohang daytime distance estimate, 2026-01-20",
                  scooter_provider="SWING",
                  scooter_tariff_status="reference_assumption_not_verified_for_pohang",
                  fare_notes="스윙 기본값 1000원+180원/분은 참고용 가정값이며 포항 현재 요금으로 확인되지 않음. 앱 요금으로 변경 필요.")
    return result


def fare_description(result):
    if result.get("estimated_fare_won") is not None:
        return f'Estimated fare: KRW {result["estimated_fare_won"]:,.0f}'
    if result.get("unconfigured_fare_modes"):
        return f'Scooter rate required · Known subtotal: KRW {result["known_fare_subtotal_won"]:,.0f}'
    return "Estimated fare unavailable"


def compare_shortest_routes(nodes, edges, start, end, mixed_modes=None, objective="distance", waypoints=None, fare_config=None):
    """혼합·자동차·자전거·보행 경로를 독립적으로 계산합니다."""
    results = {}
    for kind, modes in [("mixed", mixed_modes), *[(mode, [mode]) for mode in MODE_LABELS]]:
        result = mixed_shortest_route(nodes, edges, start, end, modes, objective, waypoints)
        result.update(route_type=kind, reachable=bool(result["path"]))
        if not result["reachable"]:
            result["distance_m"] = None
            result["time_s"] = result["cost"] = None
        results[kind] = add_route_fares(result, fare_config)
    return results


def save_route_comparison(nodes, edges, start, end, output, center=None, radius=None, mixed_modes=None, objective="distance", waypoints=None, fare_config=None):
    """네 종류의 HTML·JSON과 비교 페이지·JSON을 저장합니다."""
    waypoints = waypoints or []
    results = compare_shortest_routes(nodes, edges, start["id"], end["id"], mixed_modes, objective,
                                      [point["id"] for point in waypoints], fare_config)
    output.mkdir(parents=True, exist_ok=True)
    stem = f'route_{start["id"]}_{end["id"]}'
    if waypoints:
        import hashlib
        via = "_".join(str(point["id"]) for point in waypoints)
        stem += "_via_" + (via if len(via) <= 80 else hashlib.sha256(via.encode()).hexdigest()[:16])
    stop_label = " → ".join(map(str, [start["id"], *[point["id"] for point in waypoints], end["id"]]))
    labels = dict(mixed="Mixed modes", **MODE_LABELS)
    lines = []
    print("요금: 포항 택시 주간 거리요금 추정 / 스윙은 참고용 설정값(포항 현재 요금 미확인)", flush=True)
    for kind, result in results.items():
        result.update(start=start, end=end, waypoints=waypoints)
        filename = f"{stem}_{kind}"
        map_path = output / f"{filename}.html"
        if result["reachable"]:
            visualize(nodes, edges, map_path, center, radius, result)
            status = f'{result["time_s"] / 60:,.2f} min · {result["distance_m"]:,.2f} m · {result["mode_changes"]} mode changes'
            status += " · " + fare_description(result)
        else:
            status = "No reachable route"
            map_path.write_text(f'<!doctype html><html lang="en"><meta charset="utf-8">'
                                f'<title>{labels[kind]} route</title><h1>{labels[kind]}: {status}</h1>'
                                f'<p>{stop_label}</p></html>', encoding="utf-8")
        (output / f"{filename}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        print(f'{labels[kind]} 최단 경로: {status}', flush=True)
        for leg in result["legs"]:
            print(f'  {leg["source"]} → {leg["target"]}: {MODE_LABELS[leg["mode"]]} {leg["distance_m"]:.2f}m · {leg["time_s"] / 60:.2f}분')
        lines.append(f'<tr><td>{labels[kind]}</td><td>{status}</td>'
                     f'<td><a href="{filename}.html">View route</a> · <a href="{filename}.json">JSON</a></td></tr>')
    comparison_path = output / f"{stem}_comparison.html"
    comparison_path.write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Route comparison</title>'
        '<style>body{font:16px system-ui;max-width:900px;margin:40px auto;padding:16px}'
        'table{border-collapse:collapse;width:100%}td,th{padding:14px;border-bottom:1px solid #ddd;text-align:left}</style>'
        f'<h1>{stop_label} route comparison</h1>'
        f'<p>Optimize: {"Estimated travel time" if objective == "time" else "Total distance"} · Mode-switch penalty: 0</p>'
        '<p>Driving fares use estimated Pohang daytime taxi distance charges; cycling fares use the configured scooter rates. '
        'A base fare is charged once per continuous use of a mode, and again when returning to that mode after another mode. '
        'Nighttime, out-of-town, low-speed time charges, tolls and waiting fees are excluded. Routes are not optimized for lowest fare.</p>'
        '<p>SWING defaults (KRW 1,000 unlock + KRW 180/min) are reference assumptions, not verified current Pohang prices. Update them using the app rates.</p>'
        '<table><tr><th>Mode</th><th>Result</th><th>Files</th></tr>' + ''.join(lines) + '</table></html>', encoding="utf-8")
    (output / f"{stem}_comparison.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return comparison_path


def spherical_distance(a, b):
    """두 위경도 사이 구면 직선 거리(m). 도로 접근 거리의 참고값입니다."""
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlat, dlon = lat2 - lat1, math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371009 * 2 * math.asin(min(1.0, math.sqrt(h)))


def select_endpoint(nodes, node_id, coordinate, label, max_snap):
    if node_id is not None:
        if node_id not in nodes:
            raise ValueError(f"{label} ID {node_id}가 없습니다. 새로 생성된 CSV/graph_map.html을 확인하세요.")
        return {"id": node_id, "input_coordinate": None, "snap_distance_m": 0.0}
    node_id = min(nodes, key=lambda node: spherical_distance(coordinate, nodes[node]))
    distance = spherical_distance(coordinate, nodes[node_id])
    if distance > max_snap:
        raise ValueError(f"{label} 좌표에서 가장 가까운 도로 정점까지 {distance:.1f}m입니다. "
                         "중심·반경·좌표를 확인하거나 --max-snap을 조정하세요.")
    print(f"{label} 좌표 → 정점 {node_id}, 정점까지 직선 거리 {distance:.1f}m")
    return {"id": node_id, "input_coordinate": list(coordinate), "snap_distance_m": distance}


def visualize(nodes, edges, output, center=None, radius=None, result=None):
    """실제 도로 모양과 새 정점 ID, 선택한 최단 경로를 지도 위에 표시합니다."""
    import folium
    from branca.element import Element

    view = folium.Map(location=center or next(iter(nodes.values())), tiles="OpenStreetMap",
                      zoom_start=15, prefer_canvas=True, control_scale=True)
    view.get_root().html.add_child(Element('<script>document.documentElement.lang="en";</script>'))
    features = [{"type": "Feature", "id": str(row),
                 "properties": {"source": e.source, "target": e.target, "weight": e.weight, "row": row,
                                "road_use": e.road_use, "road_use_label": ROAD_USE_LABELS[e.road_use],
                                "mode": e.mode, "mode_label": MODE_LABELS[e.mode],
                                "speed_kph": e.speed_kph or DEFAULT_SPEEDS[e.mode],
                                "time_s": e.travel_time_s, "speed_limit_kph": e.speed_limit_kph},
                 "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in e.coordinates]}}
                for row, e in enumerate(edges, 1)]
    for mode in MODE_LABELS:
        mode_features = [f for f in features if f["properties"]["mode"] == mode]
        if not mode_features:
            continue
        folium.GeoJson(
            {"type": "FeatureCollection", "features": mode_features}, name=MODE_LABELS[mode],
            style_function=lambda f: {"color": MODE_COLORS[f["properties"]["mode"]], "weight": 3, "opacity": .65},
            highlight_function=lambda _: {"color": "#2563eb", "weight": 5},
            tooltip=folium.GeoJsonTooltip(fields=["source", "target", "weight", "mode_label"], aliases=["Source ID", "Target ID", "Distance (m)", "Mode"]),
            popup=folium.GeoJsonPopup(fields=["row", "source", "target", "weight", "mode_label", "speed_kph", "speed_limit_kph", "time_s"], aliases=["Edge row", "Source ID", "Target ID", "Distance (m)", "Mode", "Estimated speed (km/h)", "Tagged speed limit (km/h)", "Estimated time (s)"]),
        ).add_to(view)
    vertices = folium.FeatureGroup(name="Node IDs", show=result is None)
    labels = folium.FeatureGroup(name="Always show node labels", show=False)
    for node, coordinate in nodes.items():

        folium.CircleMarker(coordinate, radius=3.5, color="#0f766e", weight=1, fill=True,
                            fill_color="#5eead4", fill_opacity=1, tooltip=f"Node {node}",
                            popup=f"ID: {node}<br>Latitude: {coordinate[0]}<br>Longitude: {coordinate[1]}").add_to(vertices)
        folium.Marker(coordinate, interactive=False, icon=folium.DivIcon(
            icon_size=(40, 18), icon_anchor=(-5, 10),
            html=f'<span style="font:bold 12px system-ui;background:#fffd;padding:1px 3px">{node}</span>')).add_to(labels)
    vertices.add_to(view)
    labels.add_to(view)
    if center is not None and radius is not None:
        folium.Circle(center, radius=radius, color="#2563eb", weight=1, fill=False,
                      dash_array="5", tooltip=f"Extraction center · Radius {radius:g} m").add_to(view)
    message = f"{len(nodes):,} nodes · {len(edges):,} directed edges"
    if result is not None:
        for order, point in enumerate(result.get("waypoints", []), 1):
            node = point["id"]
            folium.Marker(nodes[node], tooltip=f"Waypoint {order}: Node {node}",
                          icon=folium.Icon(color="purple", icon="flag")).add_to(view)
            if point.get("input_coordinate") is not None:
                folium.CircleMarker(point["input_coordinate"], radius=5, color="purple",
                                    tooltip=f"Input waypoint {order}").add_to(view)
                folium.PolyLine([point["input_coordinate"], nodes[node]], color="purple", weight=2,
                                dash_array="5", tooltip="Waypoint connector: excluded from time and distance totals").add_to(view)
        route = folium.FeatureGroup(name="Selected route")
        for step, row in enumerate(result["edge_rows"], 1):
            edge = edges[row - 1]
            folium.PolyLine(edge.coordinates, color="#f97316", weight=10, opacity=.8).add_to(route)
            folium.PolyLine(edge.coordinates, color=MODE_COLORS[edge.mode], weight=6, opacity=1,
                            tooltip=f"Step {step}: {edge.source} → {edge.target} · {MODE_LABELS[edge.mode]} · {edge.weight:.2f} m · {edge.travel_time_s:.1f} s").add_to(route)
        route.add_to(view)
        for key, label, color in [("start", "Start", "green"), ("end", "End", "red")]:
            selection = result[key]
            node = selection["id"]
            # 출발=도착이면 두 마커가 겹치지 않도록 하나만 표시합니다.
            if key == "start" or result["start"]["id"] != result["end"]["id"]:
                marker_label = "Start = End" if result["start"]["id"] == result["end"]["id"] else label
                folium.Marker(nodes[node], tooltip=f"{marker_label}: {node}",
                              icon=folium.Icon(color=color, icon="flag")).add_to(view)
            if selection["input_coordinate"] is not None:
                point = selection["input_coordinate"]
                folium.CircleMarker(point, radius=5, color=color, tooltip=f"Input {label.lower()} coordinates").add_to(view)
                folium.PolyLine([point, nodes[node]], color=color, weight=2, dash_array="5",
                                tooltip="Node connector: excluded from route totals").add_to(view)
        message = (f'<b>{result["start"]["id"]} → {result["end"]["id"]}</b><br>'
                   f'<strong style="font-size:22px">{result["time_s"] / 60:,.2f} min · {result["distance_m"]:,.2f} m</strong><br>'
                   f'<small>Optimize: {"Estimated travel time" if result["objective"] == "time" else "Total distance"}</small><br>'
                   f'<b>{fare_description(result)}</b><br>'
                   '<small>SWING rates are reference assumptions; check the app.</small><br>'
                   '<div style="max-height:100px;overflow:auto;overflow-wrap:anywhere;margin:8px 0">'
                   + " → ".join(map(str, result["path"])) + "</div>"
                   '<small>Orange outline: selected route · Inner color: mode<br>Mode-switch penalty: 0 · Input connectors excluded</small><br>'
                   + '<br>'.join(f'{leg["source"]} → {leg["target"]}: {MODE_LABELS[leg["mode"]]} {leg["distance_m"]:.2f} m · {leg["time_s"] / 60:.2f} min'
                                 for leg in result.get("legs", [])))
    folium.LayerControl(collapsed=False).add_to(view)
    coordinates = list(nodes.values())
    view.fit_bounds([[min(p[0] for p in coordinates), min(p[1] for p in coordinates)],
                     [max(p[0] for p in coordinates), max(p[1] for p in coordinates)]])
    view.get_root().header.add_child(Element(
        '<title>Road Network and Shortest Routes</title>'
        '<meta name="referrer" content="strict-origin-when-cross-origin">'))
    view.get_root().html.add_child(Element(
        '<div style="position:fixed;bottom:28px;left:12px;z-index:999;background:#fffffff0;'
        'padding:14px;border-radius:9px;box-shadow:0 2px 12px #0003;max-width:320px;'
        'font:14px system-ui;color:#17283d"><b>Road Network · Dijkstra</b><br>' + message +
        '<br><small>Click a node or edge to view its properties.</small><br>' +
        '<br>'.join(f'<span style="color:{MODE_COLORS[k]}">●</span> {v}'
                    for k, v in MODE_LABELS.items()) + '</div>'))
    view.save(str(output))


def serve_map(path, port):
    handler = partial(SimpleHTTPRequestHandler, directory=str(path.resolve().parent))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as error:
        raise ValueError(f"포트 {port}에서 서버를 시작할 수 없습니다. --port 8001 등 다른 포트를 지정하세요.") from error
    with server:
        print(f"지도: http://127.0.0.1:{server.server_port}/{quote(path.name)}", flush=True)
        print("브라우저에서 위 주소를 여세요. 서버 종료: Ctrl+C", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n서버를 종료했습니다.")


def parse_args():
    parser = argparse.ArgumentParser(description="반경 1km 도로망 → 번호 그래프 → 다익스트라 → 지도")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--center", nargs=2, type=float, metavar=("LAT", "LON"))
    source.add_argument("--load-graph", type=Path, help="다운로드 대신 저장된 OSMnx GraphML 사용")
    parser.add_argument("--radius", type=float, default=1000, help="추출 반경(m), 기본 1000")
    parser.add_argument("--network-type", choices=["walk", "drive", "bike", "all"], default="all")
    parser.add_argument("--objective", choices=["time", "distance"], default="time", help="경로 최적화 기준. 기본 예상 소요시간")
    for mode, speed in DEFAULT_SPEEDS.items():
        parser.add_argument(f"--{mode}-speed", type=float, default=speed, help=f"{MODE_LABELS[mode]} 기본 속도(km/h), 기본 {speed:g}")
    parser.add_argument("--drive-factor", type=float, default=0.8, help="자동차 예상 속도 계수(0 초과 1 이하), 기본 0.8")
    for key, default in DEFAULT_FARES.items():
        parser.add_argument("--" + key.replace("_", "-"), type=float, default=default,
                            help="예상 요금 설정. 택시는 포항 주간 기준, 스윙 기본값은 참고용 가정값")
    parser.add_argument("--modes", nargs="+", choices=list(MODE_LABELS), default=list(MODE_LABELS),
                        help="경로 계산에서 섞어 사용할 수단. 기본 drive bike walk")
    for endpoint in ("start", "end"):
        group = parser.add_mutually_exclusive_group()
        group.add_argument(f"--{endpoint}", type=int, help="새 CSV 정점 ID")
        group.add_argument(f"--{endpoint}-coord", nargs=2, type=float, metavar=("LAT", "LON"))
    via = parser.add_mutually_exclusive_group()
    via.add_argument("--via", nargs="+", type=int, help="방문 순서대로 경유 정점 ID 입력")
    via.add_argument("--via-coord", nargs=2, type=float, action="append", metavar=("LAT", "LON"),
                     help="경유 좌표. 여러 번 지정하면 입력 순서대로 방문")
    parser.add_argument("--max-snap", type=float, default=500, help="입력 좌표에서 도로 정점까지 최대 허용 거리(m)")
    parser.add_argument("--output-dir", type=Path, default=BASE_DIR / "output/road_route",
                        help="지도·GraphML·경로 JSON 저장 폴더 (CSV는 스크립트 폴더에 저장)")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--overpass-url", help="Overpass 서버 기본 URL (/api까지)")
    parser.add_argument("--serve", action="store_true", help="로컬 지도 서버 실행")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    for key in DEFAULT_FARES:
        value = getattr(args, key)
        if value is not None and (not math.isfinite(value) or value < 0):
            parser.error("요금 설정은 유한한 0 이상 숫자여야 합니다.")
    if args.taxi_step_m <= 0:
        parser.error("--taxi-step-m은 0보다 커야 합니다.")
    if args.scooter_won_per_km is not None:
        args.scooter_won_per_minute = None
    if any(not math.isfinite(getattr(args, f"{mode}_speed")) or getattr(args, f"{mode}_speed") <= 0 for mode in MODE_LABELS):
        parser.error("이동수단 속도는 유한한 양수여야 합니다.")
    if not math.isfinite(args.drive_factor) or not 0 < args.drive_factor <= 1:
        parser.error("--drive-factor는 0 초과 1 이하여야 합니다.")
    if not math.isfinite(args.radius) or args.radius <= 0 or not math.isfinite(args.max_snap) or args.max_snap <= 0 or args.timeout <= 0:
        parser.error("반경·최대 연결 거리·제한 시간은 유한한 양수여야 합니다.")
    if not 1 <= args.port <= 65535:
        parser.error("포트는 1~65535 사이여야 합니다.")
    for coordinate in (args.center, args.start_coord, args.end_coord):
        if coordinate is not None:
            check_coordinate(coordinate)
    for coordinate in args.via_coord or []:
        check_coordinate(coordinate)
    has_start = args.start is not None or args.start_coord is not None
    has_end = args.end is not None or args.end_coord is not None
    if (args.via or args.via_coord) and not (has_start and has_end):
        parser.error("경유지를 사용하려면 출발과 도착도 지정하세요.")
    if has_start != has_end:
        parser.error("출발과 도착을 함께 지정하세요. 둘 다 생략하면 전체 그래프만 생성합니다.")
    if args.overpass_url and urlparse(args.overpass_url).scheme != "https":
        parser.error("--overpass-url은 https://로 시작해야 합니다.")
    return args


def main():
    args = parse_args()
    import osmnx as ox

    output = args.output_dir
    if args.load_graph:
        graph = ox.load_graphml(args.load_graph)
        center = json.loads(graph.graph["route_center"]) if "route_center" in graph.graph else None
        radius = float(graph.graph["route_radius_m"]) if "route_radius_m" in graph.graph else None
        print(f"저장된 도로망 사용: {args.load_graph}")
    else:
        center, radius = args.center, args.radius
        print(f"중심 {center}, 반경 {radius:g}m 원형 영역의 도로망을 추출합니다.", flush=True)
        graph = download_graph(center, radius, args.network_type, args.timeout, args.overpass_url)
    nodes, edges, mapping = number_graph(graph, {mode: getattr(args, f"{mode}_speed") for mode in MODE_LABELS}, args.drive_factor)
    save_numbered_csv(nodes, edges, BASE_DIR, args.objective)
    graph.graph.update(weight_objective=args.objective, weight_unit="s" if args.objective == "time" else "m")
    for _, _, data in graph.edges(data=True):
        data["weight"] = float(data["travel_time_s"]) if args.objective == "time" else float(data["length"])
    output.mkdir(parents=True, exist_ok=True)
    ox.save_graphml(graph, filepath=output / "roads.graphml")
    (output / "node_id_mapping.json").write_text(
        json.dumps({str(new): int(old) for old, new in mapping.items()}, indent=2), encoding="utf-8")
    map_path = output / "graph_map.html"
    visualize(nodes, edges, map_path, center, radius)
    print(f"정점 {len(nodes):,}개 / 간선 {len(edges):,}개")
    print(f"정점·간선 CSV 저장: {BASE_DIR}")
    print(f"지도·그래프 저장: {output.resolve()}")
    if args.start is not None or args.start_coord is not None:
        start = select_endpoint(nodes, args.start, args.start_coord, "출발", args.max_snap)
        end = select_endpoint(nodes, args.end, args.end_coord, "도착", args.max_snap)
        waypoints = [select_endpoint(nodes, node, None, f"경유 {index}", args.max_snap)
                     for index, node in enumerate(args.via or [], 1)]
        waypoints += [select_endpoint(nodes, None, coordinate, f"경유 {index}", args.max_snap)
                      for index, coordinate in enumerate(args.via_coord or [], 1)]
        map_path = save_route_comparison(nodes, edges, start, end, output,
                                         center, radius, args.modes, args.objective, waypoints,
                                         {key: getattr(args, key) for key in DEFAULT_FARES})
    else:
        print("graph_map.html에서 정점 ID를 확인한 뒤 --load-graph와 --start/--end로 경로를 계산할 수 있습니다.")
    print(f"지도 파일: {map_path.resolve()}")
    if args.serve:
        serve_map(map_path, args.port)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f"오류: {error}") from error
