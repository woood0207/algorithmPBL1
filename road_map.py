r"""지역 다운로드부터 번호 그래프, 경로, 지도(HTML)까지 (road_route/road_route.py 기반).

download_graph와 number_graph는 osmnx, map_html은 folium이 필요하다.
둘 다 함수 안에서 import하므로 웹 뷰어와 탐색 엔진은 이 패키지들 없이 돌아간다.
walk drive walk의 차량 구역(파란 간선) 표시를 map_html에 합쳤다.
"""
from __future__ import annotations

import csv
import json
import math
import re
import time
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

from graph_data import haversine

ROUTE_COLOR = {"walk1": "#f97316", "drive": "#0a35f3", "walk2": "#f97316", "to_via": "#1f77b4", "from_via": "#d62728"}


@dataclass
class Edge:
    source: int
    target: int
    weight: float  # 원본 OSMnx 도로 길이(m)
    coordinates: list[tuple[float, float]]  # 지도용 도로 굴곡: (위도, 경도)


def edges_of(graph):
    return [Edge(r.u, r.v, r.w, r.coords) for r in graph.roads]


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


def download_graph(center, radius=1000, network_type="walk", timeout=180, server=None, cache_dir=None):
    """OSM 도로망 다운로드. 연결 실패/502/503 등은 대기 후 대체 서버에서 시도합니다."""
    import osmnx as ox
    import requests
    from osmnx._errors import ResponseStatusCodeError

    boundary = make_boundary(center, radius)
    ox.settings.use_cache = True
    if cache_dir:
        ox.settings.cache_folder = str(cache_dir)
    ox.settings.log_console = True
    ox.settings.requests_timeout = timeout
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
                                                  simplify=True, retain_all=True, truncate_by_edge=False)
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


def number_graph(graph):
    """OSM ID 오름차순으로 1부터 번호를 부여하고 방향·평행 간선을 보존합니다."""
    if not graph or graph.number_of_edges() == 0:
        raise ValueError("이 지역에 추출 가능한 도로망이 없습니다.")
    mapping = {osm_id: i for i, osm_id in enumerate(sorted(graph.nodes, key=int), 1)}
    nodes = {mapping[osm_id]: (float(graph.nodes[osm_id]["y"]), float(graph.nodes[osm_id]["x"]))
             for osm_id in mapping}
    edges = []
    for u, v, key, data in sorted(graph.edges(keys=True, data=True), key=lambda e: (int(e[0]), int(e[1]), int(e[2]))):
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
        edges.append(Edge(source, target, weight, coordinates))
    return nodes, edges, mapping


def save_numbered_csv(nodes, edges, output_dir):
    """요청한 두 CSV 형식을 그대로 저장합니다."""
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "road_nodes.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["id", "latitude", "longitude"])
        writer.writerows((node, lat, lon) for node, (lat, lon) in nodes.items())
    with (output_dir / "road_edges.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["source", "target", "weight"])
        writer.writerows((edge.source, edge.target, edge.weight) for edge in edges)


def select_endpoint(nodes, node_id, coordinate, label, max_snap):
    if node_id is not None:
        if node_id not in nodes:
            raise ValueError(f"{label} ID {node_id}가 없습니다. 새로 생성된 CSV/graph_map.html을 확인하세요.")
        return {"id": node_id, "input_coordinate": None, "snap_distance_m": 0.0}
    node_id = min(nodes, key=lambda node: haversine(*coordinate, *nodes[node]))
    distance = haversine(*coordinate, *nodes[node_id])
    if distance > max_snap:
        raise ValueError(f"{label} 좌표에서 가장 가까운 도로 정점까지 {distance:.1f}m입니다. "
                         "중심·반경·좌표를 확인하거나 --max-snap을 조정하세요.")
    print(f"{label} 좌표 → 정점 {node_id}, 정점까지 직선 거리 {distance:.1f}m")
    return {"id": node_id, "input_coordinate": list(coordinate), "snap_distance_m": distance}


def map_html(nodes, edges, output, center=None, radius=None, result=None, drive_nodes=None):
    """실제 도로 모양과 새 정점 ID, 선택한 경로를 지도 위에 표시합니다."""
    import folium
    from branca.element import Element

    drive_nodes = set(drive_nodes or ())
    view = folium.Map(location=center or next(iter(nodes.values())), tiles="OpenStreetMap",
                      zoom_start=15, prefer_canvas=True, control_scale=True)
    features = [{"type": "Feature", "id": str(row),
                 "properties": {"source": e.source, "target": e.target, "weight": e.weight, "row": row,
                                "drive": e.source in drive_nodes and e.target in drive_nodes},
                 "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in e.coordinates]}}
                for row, e in enumerate(edges, 1)]
    folium.GeoJson(
        {"type": "FeatureCollection", "features": features}, name="전체 도로 간선",
        style_function=lambda f: {"color": "#0a35f3" if f["properties"]["drive"] else "#64748b",
                                  "weight": 3, "opacity": .65},
        highlight_function=lambda _: {"color": "#2563eb", "weight": 5},
        tooltip=folium.GeoJsonTooltip(fields=["source", "target", "weight"], aliases=["출발 ID", "도착 ID", "거리(m)"]),
        popup=folium.GeoJsonPopup(fields=["row", "source", "target", "weight"], aliases=["간선 순번", "출발 ID", "도착 ID", "거리(m)"]),
    ).add_to(view)
    vertices = folium.FeatureGroup(name="정점 ID", show=result is None)
    labels = folium.FeatureGroup(name="정점 번호 항상 표시", show=False)
    for node, coordinate in nodes.items():
        folium.CircleMarker(coordinate, radius=3.5, color="#0f766e", weight=1, fill=True,
                            fill_color="#5eead4", fill_opacity=1, tooltip=f"정점 {node}",
                            popup=f"ID: {node}<br>위도: {coordinate[0]}<br>경도: {coordinate[1]}").add_to(vertices)
        folium.Marker(coordinate, interactive=False, icon=folium.DivIcon(
            icon_size=(40, 18), icon_anchor=(-5, 10),
            html=f'<span style="font:bold 12px system-ui;background:#fffd;padding:1px 3px">{node}</span>')).add_to(labels)
    vertices.add_to(view)
    labels.add_to(view)
    if center is not None and radius is not None:
        folium.Circle(center, radius=radius, color="#2563eb", weight=1, fill=False,
                      dash_array="5", tooltip=f"추출 중심 · 반경 {radius:g}m").add_to(view)
    message = f"{len(nodes):,}개 정점 · {len(edges):,}개 방향 간선"
    if result is not None:
        route = folium.FeatureGroup(name="최단 경로")
        kinds = {row: seg["kind"] for seg in result.get("route_segments", []) for row in seg["edge_rows"]}
        for step, row in enumerate(result["edge_rows"], 1):
            edge = edges[row - 1]
            folium.PolyLine(edge.coordinates, color=ROUTE_COLOR.get(kinds.get(row), "#f97316"), weight=7, opacity=.95,
                            tooltip=f"{step}번째: {edge.source} → {edge.target} · {edge.weight:.2f}m").add_to(route)
        route.add_to(view)
        for key, label, color in [("start", "출발", "green"), ("end", "도착", "red")]:
            selection = result[key]
            node = selection["id"]
            # 출발=도착이면 두 마커가 겹치지 않도록 하나만 표시합니다.
            if key == "start" or result["start"]["id"] != result["end"]["id"]:
                marker_label = "출발=도착" if result["start"]["id"] == result["end"]["id"] else label
                folium.Marker(nodes[node], tooltip=f"{marker_label}: {node}",
                              icon=folium.Icon(color=color, icon="flag")).add_to(view)
            if selection["input_coordinate"] is not None:
                point = selection["input_coordinate"]
                folium.CircleMarker(point, radius=5, color=color, tooltip=f"입력한 {label} 좌표").add_to(view)
                folium.PolyLine([point, nodes[node]], color=color, weight=2, dash_array="5",
                                tooltip="도로 정점 연결: 최단 거리에 포함하지 않음").add_to(view)
        if result.get("via") is not None:
            folium.Marker(nodes[result["via"]], tooltip=f"경유: {result['via']}",
                          icon=folium.Icon(color="orange", icon="flag")).add_to(view)
        message = (f'<b>{result["start"]["id"]} → {result["end"]["id"]}</b> · {result["algorithm"]}<br>'
                   f'<strong style="font-size:22px">{result["cost"]:,.2f} {result["cost_unit"]}</strong><br>'
                   '<div style="max-height:100px;overflow:auto;overflow-wrap:anywhere;margin:8px 0">'
                   + " → ".join(map(str, result["path"])) + "</div>"
                   '<small>입력 좌표와 도로 정점 사이 거리는 합계에서 제외합니다.</small>')
    folium.LayerControl(collapsed=False).add_to(view)
    coordinates = list(nodes.values())
    view.fit_bounds([[min(p[0] for p in coordinates), min(p[1] for p in coordinates)],
                     [max(p[0] for p in coordinates), max(p[1] for p in coordinates)]])
    view.get_root().header.add_child(Element(
        '<title>도로망과 최단 경로</title>'
        '<meta name="referrer" content="strict-origin-when-cross-origin">'))
    view.get_root().html.add_child(Element(
        '<div style="position:fixed;bottom:28px;left:12px;z-index:999;background:#fffffff0;'
        'padding:14px;border-radius:9px;box-shadow:0 2px 12px #0003;max-width:320px;'
        'font:14px system-ui;color:#17283d"><b>도로망 · 경로</b><br>' + message +
        '<br><small>정점·간선을 클릭하면 속성을 표시합니다.</small></div>'))
    view.save(str(output))


def serve_map(path, port):
    handler = partial(SimpleHTTPRequestHandler, directory=str(Path(path).resolve().parent))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as error:
        raise ValueError(f"포트 {port}에서 서버를 시작할 수 없습니다. --port 8001 등 다른 포트를 지정하세요.") from error
    with server:
        print(f"지도: http://127.0.0.1:{server.server_port}/{quote(Path(path).name)}", flush=True)
        print("브라우저에서 위 주소를 여세요. 서버 종료: Ctrl+C", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n서버를 종료했습니다.")
