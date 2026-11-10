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


def fetch_elevation(coords, path, batch=100):
    """Open-Meteo에서 정점 고도를 받아 CSV로 저장한다. 한 요청에 최대 100개."""
    ids = sorted(coords)
    out = {}
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        lat = ",".join(f"{coords[n][0]:.6f}" for n in chunk)
        lon = ",".join(f"{coords[n][1]:.6f}" for n in chunk)
        req = urllib.request.Request(f"{API}?latitude={lat}&longitude={lon}", headers={"User-Agent": "pbl-route"})
        for attempt in range(6):
            try:
                with urllib.request.urlopen(req, timeout=30) as res:
                    values = json.load(res)["elevation"]
                break
            except urllib.error.HTTPError as error:   # 429: 요청이 많으면 잠시 쉬었다 다시
                if error.code != 429 or attempt == 5:
                    raise
                time.sleep(5 * (attempt + 1))
        out.update(zip(chunk, values))
        print(f"고도 {min(i + batch, len(ids))}/{len(ids)}", flush=True)
        time.sleep(1.0)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "elevation_m"])
        for n in ids:
            w.writerow([n, out[n]])
    return out


def climb(graph, u, v):
    z = graph.elevation
    return z[v] - z[u]


def is_stairs(graph, u, v):
    return "steps" in graph.roads[graph.edge_id[(u, v)]].highway


def uphill_graph(graph, lam, stairs=1.0):
    """간선 비용을 s·w + λ·max(0, Δz)로 바꾼 그래프 (s는 계단에만). 정점, 간선 번호, 좌표는 원래 그래프와 같다."""
    if not graph.elevation:
        raise ValueError("고도 데이터가 없습니다. 먼저 `python main.py elevation`을 실행하세요.")
    if stairs < 1:
        raise ValueError("계단 배수는 1 이상이어야 합니다 (1 = 벌점 없음).")
    adj, radj = {u: {} for u in graph.adj}, {u: {} for u in graph.radj}
    for u, v, w in graph.edges():
        c = (stairs if is_stairs(graph, u, v) else 1.0) * w + lam * max(0.0, climb(graph, u, v))
        adj[u][v] = c
        radj[v][u] = c
    return replace(graph, adj=adj, radj=radj)


def path_profile(graph, path):
    """경로의 도로 거리, 총 오르막, 총 내리막, 계단 거리(m)."""
    dist = up = down = steps = 0.0
    for a, b in zip(path, path[1:]):
        dist += graph.adj[a][b]
        steps += graph.adj[a][b] if is_stairs(graph, a, b) else 0.0
        dz = climb(graph, a, b)
        up += max(0.0, dz)
        down += max(0.0, -dz)
    return dist, up, down, steps
