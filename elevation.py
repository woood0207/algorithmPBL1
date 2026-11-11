"""
업힐 회피 ALT용 고도 데이터와 오르막 가중치.

정점 고도 z(v)는 data/road_elevation.csv (id, elevation_m)에 저장한다.
없으면 `python main.py elevation`으로 Open-Meteo Elevation API(Copernicus DEM 90 m)에서 받는다.

오르막 가중치는 정점이 아니라 '방향 있는 간선'에 붙인다.
  c'(u, v) = w(u, v) + λ · max(0, z(v) - z(u))
같은 도로라도 u→v는 오르막, v→u는 내리막이므로 정점 하나에 값을 주면 방향을 구분할 수 없다.
λ = 1 m 오를 때 평지 몇 m 걷는 것과 같은지. 기본 8은 Naismith 규칙(600 m 오르기 ≈ 평지 5 km)에서 온 값.
c' ≥ w 이므로 직선거리 휴리스틱은 그대로 하한이고, ALT 표는 c' 그래프에서 다시 계산해야 하한이 된다.

계단 벌점: OSM highway=steps 간선은 거리에 s배를 곱한다 (s = 1이면 끔).
  c'(u, v) = s · w(u, v) + λ · max(0, z(v) - z(u))
90 m DEM은 계단 고도차를 거의 못 잡고(고도차 0으로 나오는 계단이 많다), 계단의 오르는 방향도 OSM에 없어서 양방향 모두에 준다.
"""
import csv
import json
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

API = "https://api.open-meteo.com/v1/elevation"
DEFAULT_LAMBDA = 8.0
UPHILL_MIN_M = 0.5        # 이보다 적게 오르면 지도에서 평지로 칠한다 (DEM 해상도 때문에)


def elevation_path(nodes_csv):
    return Path(nodes_csv).with_name("road_elevation.csv")


def load_elevation(path):
    path = Path(path)
    if not path.exists():
        return None
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {int(r["id"]): float(r["elevation_m"]) for r in csv.DictReader(f)}


def fetch_elevation(coords, path, batch=100, pause=6.0):
    """Open-Meteo에서 정점 고도를 받아 CSV로 저장한다. 한 요청에 최대 100개.
    받는 대로 `path.partial`에 덧붙이므로 중간에 끊겨도 다시 실행하면 이어서 받는다.
    무료 API는 좌표 하나를 호출 하나로 세는 듯해 분당 요청 수가 작다. 그래서 pause초씩 쉬고, 429면 길게 기다린다."""
    path = Path(path)
    partial = path.with_name(path.name + ".partial")
    ids = sorted(coords)
    out = load_elevation(partial) or {}
    todo = [n for n in ids if n not in out]
    with open(partial, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not out:
            w.writerow(["id", "elevation_m"])
        for i in range(0, len(todo), batch):
            chunk = todo[i:i + batch]
            lat = ",".join(f"{coords[n][0]:.6f}" for n in chunk)
            lon = ",".join(f"{coords[n][1]:.6f}" for n in chunk)
            req = urllib.request.Request(f"{API}?latitude={lat}&longitude={lon}", headers={"User-Agent": "pbl-route"})
            for attempt in range(8):
                try:
                    with urllib.request.urlopen(req, timeout=30) as res:
                        values = json.load(res)["elevation"]
                    break
                except (urllib.error.URLError, TimeoutError) as error:   # 429나 일시적 네트워크 오류면 쉬었다 다시
                    if (isinstance(error, urllib.error.HTTPError) and error.code != 429) or attempt == 7:
                        raise
                    time.sleep(30 * (attempt + 1))
            out.update(zip(chunk, values))
            w.writerows(zip(chunk, values))
            f.flush()
            print(f"고도 {len(out)}/{len(ids)}", flush=True)
            time.sleep(pause)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "elevation_m"])
        for n in ids:
            w.writerow([n, out[n]])
    partial.unlink()
    return out


def climb(graph, u, v):
    z = graph.elevation
    return z[v] - z[u]


def is_stairs(graph, u, v):
    return "steps" in graph.roads[graph.edge_id[(u, v)]].highway


def walkable(road):
    """도보 가능 간선. 수단 정보가 없는 예전 CSV는 모두 도보로 본다."""
    return not road.permissions or road.permissions.get("walk", False)


def uphill_graph(graph, lam, stairs=1.0):
    """도보 간선만 남기고 비용을 s·길이(m) + λ·max(0, Δz)로 바꾼 그래프 (s는 계단에만).
    CSV weight가 시간(초)이어도 길이(m) 기준으로 계산한다. 정점과 좌표는 원래 그래프와 같고,
    edge_id는 (u, v)마다 이 비용이 가장 작은 도보 간선을 가리킨다."""
    if not graph.elevation:
        raise ValueError("고도 데이터가 없습니다. 먼저 `python main.py elevation`을 실행하세요.")
    if stairs < 1:
        raise ValueError("계단 배수는 1 이상이어야 합니다 (1 = 벌점 없음).")
    adj, radj, edge_id = {u: {} for u in graph.coords}, {u: {} for u in graph.coords}, {}
    for r in graph.roads:
        if r.u == r.v or not walkable(r):
            continue
        length = r.length if r.length is not None else r.w
        c = (stairs if "steps" in r.highway else 1.0) * length + lam * max(0.0, climb(graph, r.u, r.v))
        if c < adj[r.u].get(r.v, float("inf")):
            adj[r.u][r.v] = c
            radj[r.v][r.u] = c
            edge_id[(r.u, r.v)] = r.id
    return replace(graph, adj=adj, radj=radj, edge_id=edge_id)


def path_profile(graph, path):
    """경로의 도로 거리, 총 오르막, 총 내리막, 계단 거리(m). graph는 uphill_graph가 만든 것 (edge_id가 도보 간선)."""
    dist = up = down = steps = 0.0
    for a, b in zip(path, path[1:]):
        r = graph.roads[graph.edge_id[(a, b)]]
        length = r.length if r.length is not None else r.w
        dist += length
        steps += length if "steps" in r.highway else 0.0
        dz = climb(graph, a, b)
        up += max(0.0, dz)
        down += max(0.0, -dz)
    return dist, up, down, steps
