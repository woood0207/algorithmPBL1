"""
ALT(A* + Landmarks + Triangle inequality) 전처리와 휴리스틱.

h(v) = max_L max( d(L,t) - d(L,v),  d(v,L) - d(t,L) )  <=  d(v,t)

랜드마크 고르는 방법
  farthest     : 도로 거리 기준 farthest-point (pbl1/alt_visualizer의 Farthest)
  random       : 가장 큰 성분에서 무작위 (pbl1/alt_visualizer의 Random)
  geo_farthest : 가장 작은 ID부터 직선거리 farthest-point (repbl/path_lab의 6 landmarks)
"""
import math
import random
import time

from graph_data import distances, haversine

METHODS = ("farthest", "random", "geo_farthest")


def pick_landmarks(graph, k=4, method="farthest", seed=0):
    rng = random.Random(seed)
    main = graph.main_nodes
    k = max(1, min(k, len(main)))
    if method == "random":
        return rng.sample(main, k)
    if method == "geo_farthest":
        ids = sorted(graph.coords)
        chosen, nearest, nxt = [], {v: math.inf for v in ids}, ids[0]
        for _ in range(min(k, len(ids))):
            chosen.append(nxt)
            la, lo = graph.coords[nxt]
            for v in ids:
                nearest[v] = min(nearest[v], haversine(*graph.coords[v], la, lo))
            candidates = [v for v in ids if v not in chosen]
            if candidates:
                nxt = max(candidates, key=lambda v: (nearest[v], -v))
        return chosen
    if method != "farthest":
        raise ValueError(f"알 수 없는 랜드마크 방식: {method}")

    far = lambda d: max(main, key=lambda v: d[v] if math.isfinite(d[v]) else -1)
    first = distances(graph, rng.choice(main))
    cur = far({v: first.get(v, math.inf) for v in main})
    chosen, nearest = [], {v: math.inf for v in main}
    for _ in range(k):
        chosen.append(cur)
        d = distances(graph, cur)
        for v in main:
            nearest[v] = min(nearest[v], d.get(v, math.inf))
        cur = far(nearest)
    return chosen


class ALT:
    def __init__(self, graph, landmarks):
        begin = time.perf_counter()
        self.graph = graph
        self.landmarks = list(landmarks)
        self.tables = [(distances(graph, l), distances(graph, l, reverse=True)) for l in self.landmarks]
        self.prep_ms = (time.perf_counter() - begin) * 1000

    @property
    def stored_distances(self):
        return len(self.landmarks) * 2 * len(self.graph.coords)

    def bound(self, v, target):
        """(하한, 그 하한을 준 랜드마크 번호). 어떤 랜드마크도 도움이 안 되면 (0, -1)."""
        best, index = 0.0, -1
        for i, (forward, backward) in enumerate(self.tables):
            if target in forward and v in forward and forward[target] - forward[v] > best:
                best, index = forward[target] - forward[v], i
            if v in backward and target in backward and backward[v] - backward[target] > best:
                best, index = backward[v] - backward[target], i
        return best, index

    def heuristic(self, target):
        return lambda v: self.bound(v, target)[0]
