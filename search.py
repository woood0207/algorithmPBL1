"""
2) 탐색 알고리즘 부분 (WeightedAstar_visualization/search.py 기반)
f(n) = g(n) + w * h(n)
  w = 0   -> UCS (Dijkstra)
  w = 1   -> A*   (h가 consistent하므로 최적해 보장)
  w > 1   -> Weighted A* (해 비용 <= w * 최적비용)

여기에 팀원들의 알고리즘을 같은 Trace 형식으로 합쳤다.
  repbl/path_algorithms   Greedy, BFS, 양방향 Dijkstra, Beam, Local Walk, Random Walk
  shortestpath_dijkstra   경유지 후보 중 최선을 고르는 Dijkstra 2회 (+ 같은 문제를 A*, Weighted A*로)
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
    h_to: object = None              # h_to(t) -> (v -> t까지의 추정 비용). 경유 A*가 목표를 바꿀 때 사용
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
    uphill: float = 8.0              # 업힐 회피 ALT: 1 m 오르막 = 평지 λ m
    landmarks: list | None = None    # 업힐 회피 ALT: 랜드마크 ID (없으면 farthest 4개)
    stairs: float = 1.0              # 업힐 회피 ALT: 계단 간선 거리 배수 (1 = 벌점 없음)
    vehicle_state: VehicleState = field(default_factory=VehicleState)
    bus_feed: dict | None = None

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


VIA_ALGORITHMS = ("via", "via_astar", "via_weighted")
_json_safe = lambda v: None if v == INF else v


def _check_waypoints(c):
    if not c.waypoints:
        raise ValueError("경유지 후보를 하나 이상 지정하세요.")
    for n in c.waypoints:
        if n not in c.graph.coords:
            raise ValueError(f"노드 {n}이(가) 노드 파일에 없습니다.")


def shortest_via(c):
    """start -> (waypoints 중 하나) -> goal 최단 경로."""
    g, trace = c.graph, Trace(c.start, c.goal)
    _check_waypoints(c)
    dist_s, par_s = _full_dijkstra(g, c.start, trace, False, "forward")   # 1회차: d(start, v)
    dist_t, par_t = _full_dijkstra(g, c.goal, trace, True, "backward")    # 2회차: d(v, goal)

    ranking = sorted((dist_s.get(x, INF) + dist_t.get(x, INF), x, dist_s.get(x, INF), dist_t.get(x, INF))
                     for x in c.waypoints)
    trace.extra["ranking"] = [dict(node=x, total=_json_safe(t), from_start=_json_safe(a), to_goal=_json_safe(b))
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


def shortest_via_astar(c, w):
    """start -> (waypoints 중 하나) -> goal 을 (Weighted) A*로 푼다.
    후보 x마다 A*(start -> x)와 A*(x -> goal)를 돌리고 총비용이 가장 작은 x를 고른다.
      - 1구간은 목표가 x이므로 휴리스틱도 x 기준(h_x)으로 다시 만든다.
      - 하한 h_x(start) + h(x)가 작은 후보부터 보고, 하한이 지금까지의 최선 이상이면 A*를 생략한다.
        1구간을 끝낸 뒤에도 d(start, x) + h(x) >= 최선이면 2구간을 생략한다.
    h가 consistent이면 w = 1에서 최적, w > 1이면 각 구간이 w배 이내라 총비용도 w × 최적 이내다.
    (생략된 후보는 하한이 이미 최선 이상이므로 이 보장을 깨지 않는다.)"""
    g, trace = c.graph, Trace(c.start, c.goal, weight=w)
    _check_waypoints(c)
    h_to = c.h_to or (lambda t: (lambda v: g.heuristic(v, t)))
    h_goal = c.h                                     # x -> goal 구간은 원래 goal 기준 휴리스틱
    h_leg1 = {x: h_to(x) for x in dict.fromkeys(c.waypoints)}
    bound = {x: h_leg1[x](c.start) + h_goal(x) for x in h_leg1}

    def run_leg(src, dst, h, label):
        sub = weighted_astar(g, src, dst, w, h)
        for s in sub.steps:                          # 여러 번의 탐색 기록을 한 trace에 이어 붙인다
            s.order, s.direction = len(trace.steps), label
            trace.steps.append(s)
        return sub

    best, best_legs, rows, runs = INF, None, {}, 0
    for x in sorted(h_leg1, key=lambda x: (bound[x], x)):
        if bound[x] >= best:
            rows[x] = (INF, INF, INF, "bound")
            continue
        first = run_leg(c.start, x, h_leg1[x], "to_via")
        runs += 1
        if not first.found:
            rows[x] = (INF, INF, INF, "unreachable")
            continue
        if first.cost + h_goal(x) >= best:
            rows[x] = (INF, first.cost, INF, "bound")
            continue
        second = run_leg(x, c.goal, h_goal, "from_via")
        runs += 1
        if not second.found:
            rows[x] = (INF, first.cost, INF, "unreachable")
            continue
        total = first.cost + second.cost
        rows[x] = (total, first.cost, second.cost, None)
        if total < best:
            best, best_legs = total, (x, first, second)

    ranking = sorted(((t, x, a, b, why) for x, (t, a, b, why) in rows.items()), key=lambda r: (r[0], bound[r[1]], r[1]))
    trace.extra["ranking"] = [dict(node=x, total=_json_safe(t), from_start=_json_safe(a), to_goal=_json_safe(b),
                                   lower_bound=bound[x], skipped=why) for t, x, a, b, why in ranking]
    trace.extra.update(astar_runs=runs, pruned=sum(r[3] == "bound" for r in rows.values()))
    trace.extra["via"] = best_legs[0] if best_legs else None
    if best_legs is None:
        return _finish(trace, g, False, [], reason="unreachable")
    _, first, second = best_legs
    _finish(trace, g, True, first.path + second.path[1:], cost=best)
    trace.segments = [dict(kind="to_via", edge_ids=first.edge_ids),
                      dict(kind="from_via", edge_ids=second.edge_ids)]
    return trace


# ---------------------------------------------------------------- walk drive walk

def vehicle_switch(c: Context):
    c.vehicle_state = VehicleState.from_list([Vehicle.WALK, Vehicle.DRIVE, Vehicle.WALK])
    # Init
    g = c.graph
    vs = c.vehicle_state
    center = c.drive_center or g.center
    radius = float(c.drive_radius)
    if not math.isfinite(radius) or radius < 0:
        raise ValueError("차량 구역 반경은 유한한 0 이상의 값이어야 합니다.")
    if center is None:
        raise ValueError("차량 구역 중심 좌표가 없습니다.")
    drive_nodes = {
        node for node, coordinate in g.coords.items()
        if haversine(*coordinate, *center) <= radius
    }
    roads_by_source = {}
    for road in g.roads:
        if road.u != road.v:
            roads_by_source.setdefault(road.u, []).append(road)

    def permits_drive(road):
        if c.drive_rule == "edge":
            return road.u in drive_nodes and road.v in drive_nodes
        if c.drive_rule == "node":
            return road.v in drive_nodes
        raise ValueError(f"알 수 없는 차량 구역 간선 기준: {c.drive_rule}")

    def permits_mode(road, vehicle):
        return road.permits(vehicle) and (vehicle != Vehicle.DRIVE or permits_drive(road))

    minutes = lambda meters, kmh: ((meters / 1000) / kmh) * 60
    speeds = {}
    speeds[Vehicle.WALK] = c.walk_kmh
    speeds[Vehicle.DRIVE] = c.drive_kmh

    def h_cost(u, v_state, u_state):
        multiplier = 1.0
        if v_state != u_state:
            multiplier = 2
        if c.h:
            return c.weight * minutes(c.h(u), speeds[vs.vehicle[u_state]]) * multiplier
        else:
            return 0.0

    cost = {(c.start, vs.initial_state): 0.0}          # true cost g
    prev = {(c.start, vs.initial_state): None}         # (이전 정점, 이전 상태, 사용한 Road.id)

    heap = [(h_cost(c.start, vs.initial_state, vs.initial_state), c.start, vs.initial_state)]

    def push_road_maybe(prev, cost, heap, g_time, u, u_state, v, v_state, road, step):
        if g_time < cost.get((u, u_state), INF):
            cost[(u, u_state)] = g_time
            prev[(u, u_state)] = (v, v_state, road.id)
            f_time = g_time + h_cost(u, v_state, u_state)
            heapq.heappush(heap, (f_time, u, u_state))
            step.pushed.append(u)
            step.pushed_edges.append(road.id)

    # Frontend
    trace = Trace(c.start, c.goal)
    trace.cost_unit = "min"            # 비용 = 길이(m) ÷ 상태별 속도 (분)
    trace.extra.update(walk_kmh=c.walk_kmh, drive_kmh=c.drive_kmh, drive_rule=c.drive_rule,
                       drive_nodes=sorted(drive_nodes), drive_center=list(center), drive_radius=radius)
    end_state = None
    while heap:
        f, v, v_state = heapq.heappop(heap)
        before = prev[(v, v_state)]
        p_state = before[1] if before else v_state
        time = cost[(v, v_state)]
        if f > time + h_cost(v, p_state, v_state):
            continue

        step = Step(len(trace.steps), v, time, 0.0, time, before[0] if before else None, state=v_state)
        trace.steps.append(step)

        if v == c.goal:
            end_state = v_state
            break

        for road in roads_by_source.get(v, ()):
            u = road.v
            length = road.length_m

            if permits_mode(road, vs.vehicle[v_state]):
                u_state = v_state
                total_time = time + minutes(length, speeds[vs.vehicle[u_state]])
                push_road_maybe(prev, cost, heap, total_time, u, u_state, v, v_state, road, step)

            for u_state in vs.transitions[v_state]:
                if permits_mode(road, vs.vehicle[u_state]):
                    total_time = time + minutes(length, speeds[vs.vehicle[u_state]])
                    push_road_maybe(prev, cost, heap, total_time, u, u_state, v, v_state, road, step)

    if end_state is None:
        return _finish(trace, g, False, [], reason="unreachable")

    states = []
    key = (c.goal, end_state)
    while key is not None:
        states.append(key)
        previous = prev[key]
        key = (previous[0], previous[1]) if previous is not None else None
    states.reverse()
    path = [v for v, _ in states]
    edge_ids = [prev[state][2] for state in states[1:]]
    _finish(trace, g, True, path, edge_ids, cost=cost[(c.goal, end_state)])

    names = {"0": "walk1", "1": "drive", "2": "walk2"}
    groups = []
    for e, (_, v_state) in zip(edge_ids, states[1:]):
        if not groups or groups[-1]["kind"] != names[v_state]:
            groups.append(dict(kind=names[v_state], edge_ids=[], meters=0.0, minutes=0.0))
        groups[-1]["edge_ids"].append(e)
        r = g.roads[e]
        groups[-1]["meters"] += r.length_m
        groups[-1]["minutes"] += minutes(r.length_m, speeds[vs.vehicle[v_state]])
    trace.segments = groups
    trace.extra["distance_m"] = sum(x["meters"] for x in groups)
    return trace


_STOP_SNAP_CACHE = {}


def _snap_stops(graph, stops, snap_limit):
    """정류장 -> 가장 가까운 도로 정점. 정류장 위치는 피드를 새로 받아도 같으므로 한 번만 계산한다.
    (예전에는 탐색할 때마다 정류장 수 × 정점 수만큼 haversine을 불러 한 번에 3초쯤 걸렸다.)"""
    key = (id(graph), snap_limit, tuple(sorted((sid, st["lat"], st["lon"]) for sid, st in stops.items())))
    if key not in _STOP_SNAP_CACHE:
        if len(_STOP_SNAP_CACHE) >= 8:
            _STOP_SNAP_CACHE.pop(next(iter(_STOP_SNAP_CACHE)))
        connector = {}
        for stop_id, stop in stops.items():
            road_node = min(graph.coords, key=lambda node: haversine(
                stop["lat"], stop["lon"], *graph.coords[node]))
            distance = haversine(stop["lat"], stop["lon"], *graph.coords[road_node])
            if distance <= snap_limit:
                connector[stop_id] = (road_node, distance)
        stops_at = {}
        for stop_id, (road_node, distance) in connector.items():
            stops_at.setdefault(road_node, []).append((stop_id, distance))
        _STOP_SNAP_CACHE[key] = (connector, stops_at)
    return _STOP_SNAP_CACHE[key]


def time_dependent_transit(c: Context, use_astar=False):
    """Road-walking plus bus routing; each boarding cost depends on arrival time at that stop."""
    from bus_api import estimate_bus_arrivals

    graph, feed = c.graph, c.bus_feed
    if not feed or not feed.get("routes"):
        raise ValueError("실시간 포항시 버스 데이터가 없습니다. API 연결 상태를 확인하세요.")
    walk_kmh = float(c.walk_kmh)
    if not math.isfinite(walk_kmh) or walk_kmh <= 0:
        raise ValueError("도보 속도는 유한한 양수여야 합니다.")

    stops = {stop["node_id"]: stop for stop in feed["stops"]}
    coords = dict(graph.coords)
    coords.update({node: (stop["lat"], stop["lon"]) for node, stop in stops.items()})
    snap_limit = float(getattr(c, "stop_snap_m", 500.0))
    connector, stops_at = _snap_stops(graph, stops, snap_limit)

    route_by_stop = {}
    live_arrivals = {}
    route_speeds = {}
    service_ended = bool(feed.get("service_ended")) or not any(
        route.get("buses") for route in feed["routes"])
    status = {"last_bus_missed": False}
    for route in feed["routes"]:
        route_stops = route["stops"]
        route_by_stop.update({stop["node_id"]: route for stop in route_stops})
        live_arrivals.update(estimate_bus_arrivals(route, route["buses"]))
        speeds = [bus["speed_kmh"] for bus in route["buses"] if bus.get("speed_kmh") and bus["speed_kmh"] > 0]
        route_speeds[route["route_id"]] = min(80.0, max(10.0, sum(speeds) / len(speeds))) if speeds else 25.0

    walk_mps = walk_kmh / 3.6
    feed_age = max(0.0, time.time() - float(feed.get("fetched_at", time.time())))
    start_clock = time.perf_counter()
    tie = itertools.count()
    best = {c.start: 0.0}
    parent = {c.start: None}
    closed = set()
    trace = Trace(c.start, c.goal)
    trace.cost_unit = "초"
    trace.extra.update(
        basis="시간 의존 도보·버스 이동 시간(초) · 실시간 위치 기반 대기 및 탑승 시간",
        feed_age_s=feed_age, stop_snap_m=snap_limit,
        service_ended=service_ended,
        service_message="현재 운행 중인 버스가 없습니다 (운행 종료)" if service_ended else None,
        last_bus_missed=False,
    )

    max_speed_mps = 80.0 / 3.6

    def heuristic(node):
        if not use_astar:
            return 0.0
        return haversine(*coords[node], *coords[c.goal]) / max_speed_mps

    def walk_edges(node):
        if node in graph.coords:
            for neighbor, _ in graph.neighbors(node):
                road = graph.roads[graph.edge_id[(node, neighbor)]]
                if road.permissions and not road.permits(Vehicle.WALK):
                    continue
                yield neighbor, road.length_m / walk_mps, {
                    "kind": "walk", "edge_id": road.id, "distance_m": road.length_m,
                    "coordinates": road.coords or [graph.coords[node], graph.coords[neighbor]],
                }
            for stop_id, distance in stops_at.get(node, ()):   # 이 도로 정점에 붙은 정류장만
                yield stop_id, distance / walk_mps, {
                    "kind": "walk", "distance_m": distance,
                    "coordinates": [graph.coords[node], coords[stop_id]],
                }
        elif node in connector:
            road_node, distance = connector[node]
            yield road_node, distance / walk_mps, {
                "kind": "walk", "distance_m": distance,
                "coordinates": [coords[node], graph.coords[road_node]],
            }

    def bus_edges(node, elapsed):
        route = route_by_stop.get(node)
        if route is None:
            return
        route_stops = route["stops"]
        index = next(i for i, stop in enumerate(route_stops) if stop["node_id"] == node)
        now_elapsed = elapsed
        arrivals = [arrival - feed_age for arrival in live_arrivals.get(node, [])]
        upcoming = [arrival for arrival in arrivals if arrival >= now_elapsed]
        if not upcoming:
            if route["buses"]:
                status["last_bus_missed"] = True
            return
        wait_s = min(upcoming) - now_elapsed
        speed_mps = route_speeds[route["route_id"]] / 3.6
        distance = 0.0
        for downstream_index in range(index + 1, len(route_stops)):
            previous = route_stops[downstream_index - 1]
            downstream = route_stops[downstream_index]
            distance += haversine(previous["lat"], previous["lon"], downstream["lat"], downstream["lon"])
            yield downstream["node_id"], wait_s + distance / speed_mps, {
                "kind": "bus", "route_no": route["route_no"], "route_id": route["route_id"],
                "from_stop": node, "to_stop": downstream["node_id"], "wait_s": wait_s,
                "ride_s": distance / speed_mps, "distance_m": distance,
                "stop_ids": [stop["node_id"] for stop in route_stops[index:
                    downstream_index + 1]],
            }

    open_heap = [(heuristic(c.start), 0.0, next(tie), c.start)]
    while open_heap:
        _, elapsed, _, node = heapq.heappop(open_heap)
        if elapsed != best.get(node) or node in closed:
            continue
        closed.add(node)
        previous = parent[node]
        step = Step(len(trace.steps), node, elapsed, heuristic(node), elapsed + heuristic(node),
                    previous[0] if previous else None)
        trace.steps.append(step)
        if node == c.goal:
            break
        transitions = list(walk_edges(node))
        transitions.extend(bus_edges(node, elapsed) or ())
        for neighbor, cost_s, transition in transitions:
            candidate = elapsed + cost_s
            if candidate >= best.get(neighbor, INF):
                continue
            best[neighbor] = candidate
            parent[neighbor] = (node, transition)
            if neighbor in closed:
                closed.remove(neighbor)
            heapq.heappush(open_heap, (candidate + heuristic(neighbor), candidate, next(tie), neighbor))
            step.pushed.append(neighbor)
            if transition.get("edge_id") is not None:
                step.pushed_edges.append(transition["edge_id"])

    if c.goal not in best:
        _finish(trace, graph, False, [], edge_ids=[], reason="unreachable")
        trace.extra.update(last_bus_missed=status["last_bus_missed"])
        return trace

    path, transitions = [], []
    node = c.goal
    while node is not None:
        path.append(node)
        previous = parent[node]
        if previous is not None:
            node, transition = previous
            transitions.append(transition)
        else:
            node = None
    path.reverse()
    transitions.reverse()
    trace.path = path
    trace.edge_ids = [item["edge_id"] for item in transitions if item.get("edge_id") is not None]
    trace.found = True
    trace.cost = best[c.goal]
    trace.reason = None

    segments, walk_segment = [], None
    walk_s, wait_s, ride_s = 0.0, 0.0, 0.0
    walk_distance_m, ride_distance_m = 0.0, 0.0
    for transition in transitions:
        if transition["kind"] == "walk":
            duration = transition["distance_m"] / walk_mps
            walk_s += duration
            walk_distance_m += transition["distance_m"]
            if walk_segment is None:
                walk_segment = {"kind": "walk", "edge_ids": [], "coordinates": [], "walk_s": 0.0}
                segments.append(walk_segment)
            if transition.get("edge_id") is not None:
                walk_segment["edge_ids"].append(transition["edge_id"])
            points = transition["coordinates"]
            if walk_segment["coordinates"] and points and walk_segment["coordinates"][-1] == points[0]:
                points = points[1:]
            walk_segment["coordinates"].extend(points)
            walk_segment["walk_s"] += duration
        else:
            walk_segment = None
            wait_s += transition["wait_s"]
            ride_s += transition["ride_s"]
            ride_distance_m += transition["distance_m"]
            segments.append({
                "kind": "bus", "route_no": transition["route_no"], "route_id": transition["route_id"],
                "stop_ids": transition["stop_ids"], "wait_s": transition["wait_s"],
                "ride_s": transition["ride_s"],
                "coordinates": [(coords[stop_id][0], coords[stop_id][1])
                                for stop_id in transition["stop_ids"]],
            })
    trace.segments = segments
    trace.extra.update(walk_s=walk_s, wait_s=wait_s, ride_s=ride_s,
                       walk_distance_m=walk_distance_m, ride_distance_m=ride_distance_m,
                       total_minutes=trace.cost / 60.0,
                       last_bus_missed=status["last_bus_missed"])
    trace.elapsed_ms = (time.perf_counter() - start_clock) * 1000
    return trace


def transit_dijkstra(c):
    return time_dependent_transit(c)


def transit_astar(c):
    return time_dependent_transit(c, use_astar=True)


def walk_drive_walk(c: Context):
    c.vehicle_state = VehicleState.from_list([Vehicle.WALK, Vehicle.DRIVE, Vehicle.WALK])
    vehicle_switch(c)

# ---------------------------------------------------------------- 업힐 회피 ALT (pbl1/alt_visualizer 확장)
_UPHILL_CACHE = {}


def _uphill_alt(graph, lam, stairs, landmarks):
    """(λ, 계단 배수, 랜드마크)별로 c' 그래프와 ALT 표를 한 번만 만든다."""
    from elevation import uphill_graph
    from landmarks import ALT, pick_landmarks

    key = (id(graph), lam, stairs, tuple(landmarks or ()))
    if key not in _UPHILL_CACHE:
        if len(_UPHILL_CACHE) >= 16:
            _UPHILL_CACHE.pop(next(iter(_UPHILL_CACHE)))
        pg = uphill_graph(graph, lam, stairs)
        _UPHILL_CACHE[key] = (pg, ALT(pg, landmarks or pick_landmarks(pg, 4, "farthest")))
    return _UPHILL_CACHE[key]


def alt_uphill(c):
    """도보 간선만, 비용 c'(u,v) = s·길이(m) + λ·max(0, z(v) - z(u)) 위에서 ALT A* (s는 계단 간선에만).
    ALT 표도 c' 그래프에서 계산하므로 h는 c' 기준 하한이고, 찾은 경로는 c' 최소다."""
    from elevation import UPHILL_MIN_M, climb, is_stairs, path_profile

    g, lam, stairs = c.graph, max(0.0, float(c.uphill)), max(1.0, float(c.stairs))
    pg, alt = _uphill_alt(g, lam, stairs, c.landmarks)
    trace = weighted_astar(pg, c.start, c.goal, 1, alt.heuristic(c.goal))
    trace.cost_unit = "m 환산"
    trace.extra["basis"] = (f"도보 간선만 · 길이(m)" + (f" × 계단 {stairs:g}배" if stairs != 1 else "")
                            + f" + {lam:g} × 오르막(m) · ALT 표도 이 비용으로 계산")
    trace.extra.update(uphill_lambda=lam, stairs_factor=stairs, landmarks=alt.landmarks, uphill_prep_ms=alt.prep_ms)
    if not trace.found:
        return trace

    dist, up, down, steps = path_profile(pg, trace.path)
    flat = _uphill_alt(g, 0.0, 1.0, c.landmarks)[0]                     # 업힐·계단 무시한 도보 최단 경로 (비교용)
    plain = weighted_astar(flat, c.start, c.goal, 0, lambda v: 0.0).path
    p_dist, p_up, _, p_steps = path_profile(flat, plain)
    trace.extra.update(distance_m=dist, climb_m=up, descent_m=down, stairs_m=steps,
                       shortest_distance_m=p_dist, shortest_climb_m=p_up, shortest_stairs_m=p_steps)

    groups = []
    for e, a, b in zip(trace.edge_ids, trace.path, trace.path[1:]):
        dz = climb(g, a, b)
        kind = "stairs" if is_stairs(pg, a, b) else "uphill" if dz >= UPHILL_MIN_M else "flat"
        if not groups or groups[-1]["kind"] != kind:
            groups.append(dict(kind=kind, edge_ids=[], meters=0.0, climb=0.0))
        groups[-1]["edge_ids"].append(e)
        groups[-1]["meters"] += g.roads[e].length if g.roads[e].length is not None else g.roads[e].w
        groups[-1]["climb"] += max(0.0, dz)
    trace.segments = groups
    return trace


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
register_algorithm("via_astar", "경유지 A*", lambda c: shortest_via_astar(c, 1), uses_heuristic=True, comparable=False,
                   default_weight=1, hint="후보마다 A*(출발→경유)·A*(경유→도착) · 하한이 최선 이상인 후보는 생략 · 최적")
register_algorithm("via_weighted", "경유지 Weighted A*", lambda c: shortest_via_astar(c, c.weight), uses_heuristic=True,
                   comparable=False, default_weight=3,
                   hint="후보마다 Weighted A* 2회 · 총비용 ≤ w × 최적 (w=0 Dijkstra, w=1 A*)")
register_algorithm("vehicle_switch", "도보-차량-도보", vehicle_switch, uses_heuristic=True, comparable=False, default_heuristic="zero",
                   hint="상태 3개짜리 Dijkstra · 이동 시간(분) 최소")
register_algorithm("transit_dijkstra", "실시간 버스 Dijkstra", transit_dijkstra, comparable=False,
                   default_heuristic="zero",
                   hint="정류장 도착 시각마다 실시간 버스 도착·배차 대기를 다시 계산하는 최적 탐색")
register_algorithm("transit_astar", "실시간 버스 A*", transit_astar, uses_heuristic=True, comparable=False,
                   default_heuristic="haversine",
                   hint="시간 의존 버스 대기·탑승을 반영하는 A* · 직선거리/80km/h 하한")
register_algorithm("alt_uphill", "ALT 업힐 회피", alt_uphill, comparable=False, default_heuristic="zero",
                   hint="간선 비용 = 거리(계단은 ×s) + λ × 오르막(m) · ALT 표도 이 비용으로 계산 · 지도: 주황=경사 3%↑ 도로, 보라 점선=계단, 경로 분홍=오르막 검정=계단 파랑=평지·내리막")


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
    elif spec.id == "via_weighted":
        label = f"{spec.label} (w={weight:g})"
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
    h_to = lambda target: build_heuristic(graph, name, target, alt)
    begin = time.perf_counter()
    trace = spec.run(Context(graph, start, goal, h, weight=weight, h_to=h_to, **options))
    trace.elapsed_ms = (time.perf_counter() - begin) * 1000
    trace.algorithm = algorithm
    trace.heuristic = name if spec.uses_heuristic else None
    trace.weight = weight if spec.default_weight is not None else None
    trace.label = make_label(spec, name, weight)
    if trace.cost_unit == "m" and algorithm != "alt_uphill":
        trace.cost_unit = graph.weight_unit          # 대부분의 알고리즘 비용 = weight 합
    trace.extra.setdefault("basis", graph.basis)
    if algorithm == "alt_uphill":
        x = trace.extra
        trace.label += f" (λ={x['uphill_lambda']:g}" + (f", 계단×{x['stairs_factor']:g})" if x["stairs_factor"] != 1 else ")")
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
