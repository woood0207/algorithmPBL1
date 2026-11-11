"""
2) 탐색 알고리즘 부분 (WeightedAstar_visualization/search.py 기반)
f(n) = g(n) + w * h(n)
  w = 0   -> UCS (Dijkstra)
  w = 1   -> A*   (h가 consistent하므로 최적해 보장)
  w > 1   -> Weighted A* (해 비용 <= w * 최적비용)

여기에 팀원들의 알고리즘을 같은 Trace 형식으로 합쳤다.
  repbl/path_algorithms   Greedy, BFS, 양방향 Dijkstra, Beam, Local Walk, Random Walk
  shortestpath_dijkstra   경유지 후보 중 최선을 고르는 Dijkstra 2회
  walk drive walk         상태를 가진 Dijkstra (도보 -> 차량 -> 도보)
  walk drive walk/a_star  임의 인접 리스트용 A*
  pbl1/alt_visualizer     ALT 휴리스틱 (landmarks.py)

새 알고리즘은 register_algorithm으로 등록하면 CLI, 전체 비교, 웹 뷰어에 자동으로 나온다.
"""
import heapq
import itertools
import math
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List

from graph_data import Graph, Road, Vehicle, haversine, load_graph
from trace_io import Step, Trace

INF = float("inf")
HEURISTICS = {
    "haversine": "직선거리",
    "geo": "직선거리 × α",
    "alt": "ALT",
    "max": "max(직선거리 × α, ALT)",
    "zero": "h = 0",
}

@dataclass
class VehicleState:
    initial_state: str = "start"
    vehicle: Dict[str, Vehicle] = field(default_factory={"start": Vehicle.WALK})
    transitions: Dict[str, List[str]] = field(default_factory={"start": {}})

    def __init__(self, initial_state: str = "start", initial_vehicle: Vehicle = Vehicle.WALK):
        self.initial_state = initial_state
        self.vehicle = {}
        self.vehicle[initial_state] = initial_vehicle
        self.transitions = {}
        self.transitions[initial_state] = []

    def from_list(vehicles: List[Vehicle]):
        if len(vehicles) == 0:
            raise ValueError("vehicle list cannot be empty")
        if len(vehicles) == 1:
            return VehicleState("0", vehicles[0])

        vs = VehicleState("0", vehicles[0])
        for i, v in enumerate(vehicles[1:], 1):
            vs.insert_state(str(i), v)
        for j in range(1, len(vehicles)):
            vs.insert_transition(str(j-1), str(j))

        return vs

    def insert_state(self, name: str, vehicle: Vehicle):
        self.vehicle[name] = vehicle
        self.transitions[name] = [] 

    def insert_transition(self, source: str, target: str):
        self.transitions[source].append(target) 

@dataclass
class Context:
    graph: Graph
    start: int
    goal: int
    h: object                        # h(v): v에서 goal까지의 추정 비용
    weight: float = 1.0
    beam_width: int = 5
    budget: int = 2000
    seed: int = 42
    no_revisit: bool = False
    waypoints: list = field(default_factory=list)
    drive_center: tuple | None = None
    drive_radius: float = 200.0
    walk_kmh: float = 5.0
    drive_kmh: float = 50.0
    drive_rule: str = "edge"         # edge: 양 끝이 구역 안, node: 도착 정점만 구역 안 (원본 코드)
    vehicle_state: VehicleState = field(default_factory=VehicleState)

@dataclass
class Algorithm:
    id: str
    label: str
    run: object
    uses_heuristic: bool = False
    hint: str = ""
    comparable: bool = True          # 전체 비교에 넣을지 (같은 문제를 푸는 알고리즘만)
    default_heuristic: str = "haversine"
    default_weight: float | None = None


ALGORITHMS = {}


def register_algorithm(id, label, run, **kwargs):
    ALGORITHMS[id] = Algorithm(id, label, run, **kwargs)


def _finish(trace, graph, found, path, edge_ids=None, cost=None, reason=None):
    trace.found = found
    trace.path = path
    trace.edge_ids = graph.path_edges(path) if edge_ids is None else edge_ids
    trace.reason = reason
    trace.cost = cost if not found or cost is not None else sum(graph.roads[e].w for e in trace.edge_ids)
    if not found:
        trace.cost = None
    return trace


def _walk_back(parent, node):
    path = []
    while node is not None:
        path.append(node)
        node = parent[node]
    return path[::-1]


# ---------------------------------------------------------------- f = g + w*h
def weighted_astar(graph, start, goal, w, h=None):
    t0 = time.perf_counter()
    if h is None:
        h = lambda n: graph.heuristic(n, goal)

    g_cost = {start: 0.0}
    parent = {start: None}
    closed = set()
    tie = itertools.count()              # f가 같을 때 heap 비교용
    open_heap = [(w * h(start), next(tie), start)]

    trace = Trace(start=start, goal=goal, weight=w)

    while open_heap:
        f, _, u = heapq.heappop(open_heap)
        if u in closed:                  # 더 나은 경로로 이미 처리된 오래된 항목
            continue
        closed.add(u)

        step = Step(order=len(trace.steps), node=u, g=g_cost[u], h=h(u), f=f,
                    parent=parent[u])

        if u == goal:
            trace.steps.append(step)
            break

        for v, c in graph.neighbors(u):
            if v in closed:              # 재오픈 없음 (graph search)
                continue
            ng = g_cost[u] + c
            if ng < g_cost.get(v, INF):
                g_cost[v] = ng
                parent[v] = u
                heapq.heappush(open_heap, (ng + w * h(v), next(tie), v))
                step.pushed.append(v)    # 이번 확장에서 open에 들어가거나 갱신된 노드
                step.pushed_edges.append(graph.edge_id[(u, v)])
        trace.steps.append(step)

    if goal in closed:
        _finish(trace, graph, True, _walk_back(parent, goal), cost=g_cost[goal])
    else:
        _finish(trace, graph, False, [], reason="unreachable")
    trace.elapsed_ms = (time.perf_counter() - t0) * 1000
    return trace


def a_star(graph, start, goal, h):
    """walk drive walk/a_star.py의 인터페이스. graph: {node: [(이웃, 비용), ...]}"""
    open_set = [(h(start, goal), 0, next(tie := itertools.count()), start, [start])]
    visited = {}
    while open_set:
        _, g, _, node, path = heapq.heappop(open_set)
        if node == goal:
            return path, g
        if node in visited and visited[node] <= g:
            continue
        visited[node] = g
        for neighbor, cost in graph.get(node, []):
            new_g = g + cost
            if neighbor not in visited or new_g < visited[neighbor]:
                heapq.heappush(open_set, (new_g + h(neighbor, goal), new_g, next(tie), neighbor, path + [neighbor]))
    return None, INF


# ---------------------------------------------------------------- repbl
def greedy_best_first(c):
    g, trace = c.graph, Trace(c.start, c.goal)
    seq = itertools.count()
    dist, parent = {c.start: 0.0}, {c.start: None}
    heap = [(c.h(c.start), next(seq), c.start, 0.0)]
    while heap:
        key, _, u, d = heapq.heappop(heap)
        if d != dist.get(u):
            continue
        step = Step(len(trace.steps), u, d, c.h(u), key, parent[u])
        trace.steps.append(step)
        if u == c.goal:
            return _finish(trace, g, True, _walk_back(parent, u), cost=d)
        for v, w in g.neighbors(u):
            nd = d + w
            if nd < dist.get(v, INF):
                dist[v], parent[v] = nd, u
                heapq.heappush(heap, (c.h(v), next(seq), v, nd))
                step.pushed.append(v)
                step.pushed_edges.append(g.edge_id[(u, v)])
    return _finish(trace, g, False, [], reason="unreachable")


def bfs(c):
    g, trace = c.graph, Trace(c.start, c.goal)
    queue, seen, parent, dist = deque([c.start]), {c.start}, {c.start: None}, {c.start: 0.0}
    while queue:
        u = queue.popleft()
        step = Step(len(trace.steps), u, dist[u], 0.0, dist[u], parent[u])
        trace.steps.append(step)
        if u == c.goal:
            return _finish(trace, g, True, _walk_back(parent, u), cost=dist[u])
        for v, w in g.neighbors(u):
            if v not in seen:
                seen.add(v)
                parent[v], dist[v] = u, dist[u] + w
                queue.append(v)
                step.pushed.append(v)
                step.pushed_edges.append(g.edge_id[(u, v)])
    return _finish(trace, g, False, [], reason="unreachable")


def bidirectional(c):
    g, trace = c.graph, Trace(c.start, c.goal)
    if c.start == c.goal:
        trace.steps.append(Step(0, c.start, 0.0, 0.0, 0.0, None, direction="forward"))
        return _finish(trace, g, True, [c.start], cost=0.0)
    ds = [{c.start: 0.0}, {c.goal: 0.0}]
    parents = [{c.start: None}, {c.goal: None}]
    heaps = [[(0.0, c.start)], [(0.0, c.goal)]]
    settled = [set(), set()]
    best, meet = INF, None

    def connect(v):
        nonlocal best, meet
        if v in ds[0] and v in ds[1] and ds[0][v] + ds[1][v] < best:
            best, meet = ds[0][v] + ds[1][v], v

    while True:
        for i in (0, 1):
            while heaps[i] and (heaps[i][0][0] != ds[i].get(heaps[i][0][1]) or heaps[i][0][1] in settled[i]):
                heapq.heappop(heaps[i])
        if not heaps[0] or not heaps[1] or heaps[0][0][0] + heaps[1][0][0] >= best:
            break
        i = 0 if heaps[0][0][0] <= heaps[1][0][0] else 1
        d, u = heapq.heappop(heaps[i])
        settled[i].add(u)
        connect(u)
        step = Step(len(trace.steps), u, d, 0.0, d, parents[i][u], direction="forward" if i == 0 else "backward")
        trace.steps.append(step)
        for v, w in (g.neighbors(u) if i == 0 else g.reverse_neighbors(u)):
            nd = d + w
            if nd < ds[i].get(v, INF):
                ds[i][v], parents[i][v] = nd, u
                heapq.heappush(heaps[i], (nd, v))
                connect(v)
                step.pushed.append(v)
                step.pushed_edges.append(g.edge_id[(u, v) if i == 0 else (v, u)])
    if meet is None:
        return _finish(trace, g, False, [], reason="unreachable")
    path = _walk_back(parents[0], meet)
    v = parents[1][meet]
    while v is not None:
        path.append(v)
        v = parents[1][v]
    trace.extra["meet"] = meet
    return _finish(trace, g, True, path, cost=best)


def beam(c):
    g, trace = c.graph, Trace(c.start, c.goal)
    frontier, seen, parent, pruned = [(c.start, 0.0)], {c.start}, {c.start: None}, 0
    while frontier:
        candidates, step = {}, None
        for u, d in frontier:
            if len(trace.steps) >= c.budget:
                trace.extra["pruned"] = pruned
                return _finish(trace, g, False, [], reason="budget")
            step = Step(len(trace.steps), u, d, c.h(u), c.h(u), parent[u])
            trace.steps.append(step)
            if u == c.goal:
                trace.extra["pruned"] = pruned
                return _finish(trace, g, True, _walk_back(parent, u), cost=d)
            for v, w in g.neighbors(u):
                if v in seen:
                    continue
                nd = d + w
                if v not in candidates or nd < candidates[v][0]:
                    candidates[v] = (nd, u, g.edge_id[(u, v)])
                    step.pushed.append(v)
                    step.pushed_edges.append(g.edge_id[(u, v)])
        ranked = sorted(candidates, key=lambda v: (c.h(v), candidates[v][0], v))
        keep = ranked[:max(1, c.beam_width)]
        dropped = ranked[len(keep):]
        pruned += len(dropped)
        if step is not None:
            step.pruned = dropped
        frontier = []
        for v in keep:
            d, u, _ = candidates[v]
            seen.add(v)
            parent[v] = u
            frontier.append((v, d))
    trace.extra["pruned"] = pruned
    return _finish(trace, g, False, [], reason="beam_exhausted")


def _walk(c, randomly):
    g, trace = c.graph, Trace(c.start, c.goal)
    rng = random.Random(c.seed)
    u, seen, path, edges, cost, revisits, prev_edge = c.start, {c.start}, [c.start], [], 0.0, 0, None
    while True:
        trace.steps.append(Step(len(trace.steps), u, cost, c.h(u), c.h(u),
                                path[-2] if len(path) > 1 else None, move_edge=prev_edge))
        trace.extra["revisits"] = revisits
        if u == c.goal:
            return _finish(trace, g, True, path, edges, cost=cost)
        if len(edges) >= c.budget:
            return _finish(trace, g, False, path, edges, reason="budget")
        choices = [(v, w, g.edge_id[(u, v)]) for v, w in g.neighbors(u) if not c.no_revisit or v not in seen]
        if not choices:
            return _finish(trace, g, False, path, edges, reason="dead_end")
        v, w, e = rng.choice(choices) if randomly else min(choices, key=lambda x: (c.h(x[0]), x[1], x[2]))
        revisits += int(v in seen)
        seen.add(v)
        path.append(v)
        edges.append(e)
        cost += w
        prev_edge, u = e, v


# ---------------------------------------------------------------- shortestpath_dijkstra
def _full_dijkstra(graph, src, trace, reverse, label):
    dist, parent = {src: 0.0}, {src: None}
    heap, done = [(0.0, src)], set()
    while heap:
        d, u = heapq.heappop(heap)
        if u in done:                    # 이미 확정된 정점이면 건너뜀 (lazy deletion)
            continue
        done.add(u)
        step = Step(len(trace.steps), u, d, 0.0, d, parent[u], direction=label)
        trace.steps.append(step)
        for v, w in (graph.reverse_neighbors(u) if reverse else graph.neighbors(u)):
            nd = d + w
            if nd < dist.get(v, INF):
                dist[v], parent[v] = nd, u
                heapq.heappush(heap, (nd, v))
                step.pushed.append(v)
                step.pushed_edges.append(graph.edge_id[(v, u) if reverse else (u, v)])
    return dist, parent


def shortest_via(c):
    """start -> (waypoints 중 하나) -> goal 최단 경로."""
    g, trace = c.graph, Trace(c.start, c.goal)
    if not c.waypoints:
        raise ValueError("경유지 후보를 하나 이상 지정하세요.")
    for n in c.waypoints:
        if n not in g.coords:
            raise ValueError(f"노드 {n}이(가) 노드 파일에 없습니다.")
    dist_s, par_s = _full_dijkstra(g, c.start, trace, False, "forward")   # 1회차: d(start, v)
    dist_t, par_t = _full_dijkstra(g, c.goal, trace, True, "backward")    # 2회차: d(v, goal)

    ranking = sorted((dist_s.get(x, INF) + dist_t.get(x, INF), x, dist_s.get(x, INF), dist_t.get(x, INF))
                     for x in c.waypoints)
    json_safe = lambda v: None if v == INF else v
    trace.extra["ranking"] = [dict(node=x, total=json_safe(t), from_start=json_safe(a), to_goal=json_safe(b))
                              for t, x, a, b in ranking]
    total, best, _, _ = ranking[0]
    trace.extra["via"] = None if total == INF else best
    if total == INF:
        return _finish(trace, g, False, [], reason="unreachable")
    first = _walk_back(par_s, best)
    second = _walk_back(par_t, best)[::-1]
    _finish(trace, g, True, first + second[1:], cost=total)
    trace.segments = [dict(kind="to_via", edge_ids=g.path_edges(first)),
                      dict(kind="from_via", edge_ids=g.path_edges(second))]
    return trace


# ---------------------------------------------------------------- walk drive walk
# def drive_zone(roads: List[Road]):
#     uniq = set()
#     for r in filter(lambda r: r.permits(Vehicle.DRIVE), roads):
#         uniq.add(r.u)
#         uniq.add(r.v)
#     return list(uniq)

def vehicle_switch(c: Context):
    c.vehicle_state = VehicleState.from_list([Vehicle.WALK, Vehicle.DRIVE, Vehicle.WALK])
    
    # Init
    g = c.graph
    vs = c.vehicle_state
    minutes = lambda meters, kmh: ((meters / 1000) / kmh) * 60
    cost = {(c.start, vs.initial_state): 0.0}
    prev = {(c.start, vs.initial_state): None}


    heap = [(0.0, c.start, vs.initial_state)]
    speeds = {}
    speeds[Vehicle.WALK] = c.walk_kmh
    speeds[Vehicle.DRIVE] = c.drive_kmh

    def push_road_maybe(prev, cost, heap, time, u, u_state, v, v_state, step):
        if time < cost.get((u, u_state), INF):
            cost[(u, u_state)] = time
            prev[(u, u_state)] = (v, v_state)
            heapq.heappush(heap, (time, u, u_state))
            step.pushed.append(u)
            step.pushed_edges.append(g.edge_id[(v, u)])

    # Frontend
    trace = Trace(c.start, c.goal)
    trace.cost_unit = "min"
    trace.extra.update(walk_kmh=c.walk_kmh, drive_kmh=c.drive_kmh, drive_rule=c.drive_rule)
    
    end_state = None
    while heap:
        time, v, v_state = heapq.heappop(heap)
        if time > cost[(v, v_state)]:
            continue

        before = prev[(v, v_state)]
        step = Step(len(trace.steps), v, time, 0.0, time, before[0] if before else None, state=v_state)
        trace.steps.append(step)

        if v == c.goal:
            end_state = v_state
            break

        for u, _ in g.neighbors(v):
            road = g.roads[g.edge_id[(v, u)]]
            length = road.length_m

            if road.permits(vs.vehicle[v_state]):
                u_state = v_state
                total_time = time + minutes(length, speeds[vs.vehicle[u_state]])
                push_road_maybe(prev, cost, heap, total_time, u, u_state, v, v_state, step)

            for u_state in vs.transitions[v_state]:
                if road.permits(vs.vehicle[u_state]):
                    total_time = time + minutes(length, speeds[vs.vehicle[u_state]])
                    push_road_maybe(prev, cost, heap, total_time, u, u_state, v, v_state, step)

    if end_state is None:
        return _finish(trace, g, False, [], reason="unreachable")

    states = []
    key = (c.goal, end_state)
    while key is not None:
        states.append(key)
        key = prev[key]
    states.reverse()
    path = [v for v, _ in states]
    edge_ids = g.path_edges(path)
    _finish(trace, g, True, path, edge_ids, cost=cost[(c.goal, end_state)])

    names = {"0": "walk1", "1": "drive", "2": "walk2"}
    groups = []
    for e, (_, v_state) in zip(edge_ids, states[1:]):
        if not groups or groups[-1]["kind"] != names[v_state]:
            groups.append(dict(kind=names[v_state], edge_ids=[], meters=0.0, minutes=0.0))
        groups[-1]["edge_ids"].append(e)
        groups[-1]["meters"] += g.roads[e].w
        groups[-1]["minutes"] += minutes(g.roads[e].w, c.drive_kmh if v_state == 1 else c.walk_kmh)
    trace.segments = groups
    trace.extra["distance_m"] = sum(x["meters"] for x in groups)
    return trace

def walk_drive_walk(c: Context):
    c.vehicle_state = VehicleState.from_list([Vehicle.WALK, Vehicle.DRIVE, Vehicle.WALK])
    vehicle_switch(c)

# ---------------------------------------------------------------- 등록
register_algorithm("dijkstra", "Dijkstra", lambda c: weighted_astar(c.graph, c.start, c.goal, 0, c.h),
                   hint="누적 비용 기준 · 최적 비용 기준선", default_weight=0, default_heuristic="zero")
register_algorithm("astar", "A*", lambda c: weighted_astar(c.graph, c.start, c.goal, 1, c.h), uses_heuristic=True,
                   hint="누적 비용 + 휴리스틱 하한", default_weight=1)
register_algorithm("weighted", "Weighted A*", lambda c: weighted_astar(c.graph, c.start, c.goal, c.weight, c.h),
                   uses_heuristic=True, comparable=False, default_weight=3,
                   hint="f = g + w·h · 해 비용 ≤ w × 최적 비용 (w=0 UCS, w=1 A*)")
register_algorithm("greedy", "Greedy Best-First", greedy_best_first, uses_heuristic=True,
                   hint="휴리스틱만 사용 · 최적성 보장 없음", default_heuristic="geo")
register_algorithm("bfs", "BFS", bfs, hint="간선 수 최소 · 가중치 최단 경로 보장 없음", default_heuristic="zero")
register_algorithm("bidirectional", "Bidirectional Dijkstra", bidirectional,
                   hint="정방향·역방향 탐색 · 하한 기반 종료", default_heuristic="zero")
register_algorithm("beam", "Beam Search", beam, uses_heuristic=True,
                   hint="깊이별 상위 k개 후보만 유지 · 실패 가능", default_heuristic="geo")
register_algorithm("local", "Local Greedy Walk", lambda c: _walk(c, False), uses_heuristic=True,
                   hint="현재 이웃만 보고 이동 · 재방문 허용 시 순환 가능", default_heuristic="geo")
register_algorithm("random", "Random Walk", lambda c: _walk(c, True),
                   hint="시드가 고정된 무작위 이동 · 예산 내 성공 보장 없음", default_heuristic="zero")
register_algorithm("via", "경유지 Dijkstra", shortest_via, comparable=False, default_heuristic="zero",
                   hint="출발·도착에서 Dijkstra 2회 · 후보 중 총거리 최소인 경유지 선택")
register_algorithm("vehicle_switch", "도보-차량-도보", vehicle_switch, comparable=False, default_heuristic="zero",
                   hint="상태 3개짜리 Dijkstra · 이동 시간(분) 최소")


def build_heuristic(graph, name, goal, alt=None):
    if name == "zero":
        return lambda v: 0.0
    if name == "haversine":
        return lambda v: graph.heuristic(v, goal)
    if name == "geo":
        return lambda v: graph.geographic(v, goal)
    if name in ("alt", "max"):
        if alt is None:
            raise ValueError("ALT 휴리스틱에는 랜드마크가 필요합니다.")
        alt_h = alt.heuristic(goal)
        if name == "alt":
            return alt_h
        return lambda v: max(graph.geographic(v, goal), alt_h(v))
    raise ValueError(f"알 수 없는 휴리스틱: {name}")


def make_label(spec, heuristic, weight):
    if spec.id == "weighted":
        name = "UCS" if weight == 0 else ("A*" if weight == 1 else "Weighted A*")
        label = f"{name} (w={weight:g})"
    else:
        label = spec.label
    if spec.uses_heuristic and weight != 0 and heuristic:
        label += f" / {HEURISTICS[heuristic]}"
    return label


def run_search(graph, algorithm, start, goal, heuristic=None, weight=None, alt=None, h=None, **options):
    if start not in graph.coords or goal not in graph.coords:
        raise ValueError(f"출발 {start} 또는 도착 {goal}이(가) 정점 목록에 없습니다.")
    spec = ALGORITHMS[algorithm]
    name = (heuristic or spec.default_heuristic) if spec.uses_heuristic else "zero"
    if weight is None:
        weight = spec.default_weight if spec.default_weight is not None else 1.0
    if h is None:
        h = build_heuristic(graph, name, goal, alt)
    begin = time.perf_counter()
    trace = spec.run(Context(graph, start, goal, h, weight=weight, **options))
    trace.elapsed_ms = (time.perf_counter() - begin) * 1000
    trace.algorithm = algorithm
    trace.heuristic = name if spec.uses_heuristic else None
    trace.weight = weight if spec.default_weight is not None else None
    trace.label = make_label(spec, name, weight)
    return trace


def find_route(start, end, waypoints, nodes_path="data/road_nodes.csv", edges_path="data/road_edges.csv"):
    """shortestpath_dijkstra.py의 외부 호출용 함수.
    반환: distance(없으면 None), path, via, ranking[(총거리, 노드, 출발->경유, 경유->도착), ...]"""
    graph = load_graph(nodes_path, edges_path)
    trace = run_search(graph, "via", start, end, waypoints=waypoints)
    inf = lambda v: INF if v is None else v
    return {"distance": trace.cost, "path": trace.path, "via": trace.extra["via"],
            "ranking": [(inf(r["total"]), r["node"], inf(r["from_start"]), inf(r["to_goal"]))
                        for r in trace.extra["ranking"]]}
