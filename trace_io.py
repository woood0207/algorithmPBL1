"""
3) 탐색 기록 데이터 부분 (WeightedAstar_visualization/trace_io.py 기반)
알고리즘이 노드를 확장한 순서를 Step 리스트로 저장하고 JSON으로 읽고 쓴다.
시각화(PNG/GIF, 웹 뷰어)는 이 JSON만 있으면 되므로 알고리즘 부분과 독립적이다.

JSON 형식:
{
  "start": 1, "goal": 500, "weight": 1.0,
  "cost": 1234.5, "path": [1, 60, ...], "elapsed_ms": 0.8,
  "steps": [ {"order":0, "node":1, "g":0, "h":..., "f":..., "parent":null, "pushed":[60,344]}, ... ],

  // 아래는 병합하면서 추가한 필드. 원래 형식의 JSON도 그대로 읽을 수 있다.
  "algorithm": "astar", "heuristic": "alt", "label": "A* / ALT",
  "found": true, "reason": null, "edge_ids": [...], "cost_unit": "m",
  "segments": [{"kind": "drive", "edge_ids": [...]}], "extra": {...}
}
"""
import json
from dataclasses import dataclass, field, asdict


@dataclass
class Step:
    order: int          # 몇 번째로 확장(closed)되었는지
    node: int
    g: float
    h: float
    f: float            # g + w*h (heap에서 꺼낼 때의 값)
    parent: int | None
    pushed: list = field(default_factory=list)        # 이번 확장에서 open에 들어간 노드
    pushed_edges: list = field(default_factory=list)  # pushed와 같은 순서의 Road.id
    pruned: list = field(default_factory=list)        # Beam에서 이번에 버려진 후보
    direction: str | None = None                      # 양방향 탐색: forward / backward
    state: int | None = None                          # 도보-차량-도보: 0, 1, 2
    move_edge: int | None = None                      # 보행 알고리즘: 이 칸에 올 때 쓴 간선


@dataclass
class Trace:
    start: int
    goal: int
    weight: float | None = None
    steps: list = field(default_factory=list)
    path: list = field(default_factory=list)
    cost: float | None = None
    elapsed_ms: float = 0.0
    algorithm: str = "weighted"
    heuristic: str | None = None
    label: str = ""
    found: bool | None = None
    reason: str | None = None       # budget / beam_exhausted / dead_end / unreachable
    edge_ids: list = field(default_factory=list)
    cost_unit: str = "m"
    segments: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.found is None:
            self.found = self.cost is not None

    @property
    def expanded(self):
        return len(self.steps)


def trace_to_dict(trace, include_steps=True):
    d = asdict(trace)
    d["expanded"] = trace.expanded
    if not include_steps:
        d["steps"] = []
    return d


def save_trace(trace, filename):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(trace_to_dict(trace), f, ensure_ascii=False)


def trace_from_dict(d):
    d = dict(d)
    d.pop("expanded", None)
    d["steps"] = [Step(**s) for s in d["steps"]]
    return Trace(**d)


def load_trace(filename):
    with open(filename, encoding="utf-8") as f:
        return trace_from_dict(json.load(f))
