"""
4) 시각화 부분 (WeightedAstar_visualization/visualize.py 기반)
저장된 Trace(JSON)들을 읽어 나란히 애니메이션으로 보여준다.
- 회색 선: 도로망 (GraphML이 있으면 실제 도로 곡선)
- 점 색: 확장된 순서 (먼저=진한 파랑 -> 나중=노랑)
- 주황 점: 현재 open list(frontier)
- 빨간 선: 최종 경로 (도보-차량-도보, 경유지는 구간별 색)
모든 패널은 같은 '확장 횟수' 시계를 공유하므로, 어느 쪽이 더 적게 탐색하는지 바로 비교된다.
웹 뷰어(web/)도 같은 Trace와 같은 색 규칙을 쓴다.
"""
import math

import matplotlib
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.collections import LineCollection

SEGMENT_COLOR = {"walk1": "#d62728", "drive": "#0a35f3", "walk2": "#d62728",
                 "to_via": "#1f77b4", "from_via": "#d62728"}
FAILED_COLOR = "#f4b56b"

plt.rcParams["axes.unicode_minus"] = False
_installed_fonts = {f.name for f in font_manager.fontManager.ttflist}
for _font in ("Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR"):  # 라벨의 한글이 깨지지 않게
    if _font in _installed_fonts:
        plt.rcParams["font.family"] = _font
        break


def _projector(graph):
    # 경도 1도는 cos(위도)만큼 짧으므로 aspect 보정해서 실제 모양대로 그린다
    lat0 = sum(c[0] for c in graph.coords.values()) / len(graph.coords)
    k = math.cos(math.radians(lat0))
    return lambda lat, lon: (lon * k, lat)


def _xy(graph):
    project = _projector(graph)
    return {n: project(lat, lon) for n, (lat, lon) in graph.coords.items()}


def _label(tr):
    if tr.label:
        return tr.label
    w = tr.weight
    name = "UCS" if w == 0 else ("A*" if w == 1 else "Weighted A*")
    return f"{name} (w={w:g})"


def _cost_text(tr):
    if tr.cost is None:
        return f"failed ({tr.reason})" if tr.reason else "failed"
    return f"cost {tr.cost:.1f} {tr.cost_unit}"


def _road_line(graph, project, edge_id):
    return [project(lat, lon) for lat, lon in graph.roads[edge_id].coords]


def _draw_base(ax, graph, xy, tr):
    project = _projector(graph)
    segs = [[project(*p) for p in r.coords] for r in graph.roads if r.u < r.v]
    ax.add_collection(LineCollection(segs, colors="#cfcfcf", linewidths=0.6, zorder=1))
    if tr.algorithm == "vehicle_switch" and tr.extra.get("drive_nodes"):
        zone = set(tr.extra["drive_nodes"])
        blue = [[project(*p) for p in r.coords] for r in graph.roads if r.u < r.v and r.u in zone and r.v in zone]
        ax.add_collection(LineCollection(blue, colors="#a9b8fb", linewidths=1.0, zorder=1.5))
    ax.scatter(*zip(*xy.values()), s=2, c="#bbbbbb", zorder=2)
    ax.scatter(*xy[tr.start], s=90, c="limegreen", edgecolors="k", zorder=6, label="start")
    ax.scatter(*xy[tr.goal], s=130, c="red", marker="*", edgecolors="k", zorder=6, label="goal")
    via = tr.extra.get("via")
    if via is not None:
        ax.scatter(*xy[via], s=110, c="#ff7f0e", edgecolors="k", zorder=6, label="via")
        for n, name in ((tr.start, "S"), (via, "W"), (tr.goal, "T")):
            ax.annotate(f"{name} ({n})", xy[n], textcoords="offset points", xytext=(6, 6), fontsize=9, zorder=7)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.autoscale()


def _draw_result(ax, graph, xy, tr):
    """최종 경로. 곡선을 따라 그리고, 구간이 있으면 구간별 색으로 그린다."""
    project = _projector(graph)
    if tr.segments:
        for seg in tr.segments:
            lines = [_road_line(graph, project, e) for e in seg["edge_ids"]]
            ax.add_collection(LineCollection(lines, colors=SEGMENT_COLOR[seg["kind"]], linewidths=2.5, zorder=5))
    elif tr.edge_ids:
        lines = [_road_line(graph, project, e) for e in tr.edge_ids]
        ax.add_collection(LineCollection(lines, colors="red" if tr.found else FAILED_COLOR, linewidths=2.5,
                                         linestyles="solid" if tr.found else "dashed", zorder=5))
    elif tr.path:
        ax.plot(*zip(*[xy[n] for n in tr.path]), c="red", lw=2.5, zorder=5)


def draw_static(graph, traces, out_png):
    """최종 결과 한 장 (모든 확장 노드 + 경로)."""
    xy = _xy(graph)
    max_steps = max(t.expanded for t in traces)
    fig, axes = plt.subplots(1, len(traces), figsize=(6 * len(traces), 6.5))
    axes = [axes] if len(traces) == 1 else axes
    for ax, tr in zip(axes, traces):
        _draw_base(ax, graph, xy, tr)
        pts = [xy[s.node] for s in tr.steps]
        ax.scatter(*zip(*pts), c=range(len(pts)), cmap="viridis", s=14, zorder=3,
                   vmin=0, vmax=max(1, max_steps - 1))   # 애니메이션과 같은 색 기준
        _draw_result(ax, graph, xy, tr)
        ax.set_title(f"{_label(tr)}\nexpanded {tr.expanded} nodes | {_cost_text(tr)}", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(out_png, dpi=130)
    plt.close(fig)


def _partial_chain(done):
    """탐색 중에는 현재 노드까지의 부모 경로를 보여준다. 보행 알고리즘은 지나온 궤적."""
    if not done:
        return []
    if done[-1].move_edge is not None or (len(done) > 1 and done[1].move_edge is not None):
        return [s.node for s in done]
    par = {s.node: s.parent for s in done}
    chain, seen, c = [], set(), done[-1].node
    while c is not None and c not in seen:   # 양방향 탐색은 부모 포인터가 두 벌이라 순환을 막는다
        seen.add(c)
        chain.append(c)
        c = par.get(c)
    return chain


def animate(graph, traces, out_file=None, frames=150, fps=15, show=False):
    xy = _xy(graph)
    fig, axes = plt.subplots(1, len(traces), figsize=(6 * len(traces), 6.5))
    axes = [axes] if len(traces) == 1 else axes
    max_steps = max(t.expanded for t in traces)
    cmap = matplotlib.colormaps["viridis"]

    panels = []
    for ax, tr in zip(axes, traces):
        _draw_base(ax, graph, xy, tr)
        closed_sc = ax.scatter([], [], s=14, zorder=3)
        open_sc = ax.scatter([], [], s=14, c="orange", edgecolors="none", zorder=4)
        cur_sc = ax.scatter([], [], s=80, facecolors="none", edgecolors="k", lw=1.5, zorder=7)
        (path_ln,) = ax.plot([], [], c="red", lw=2.5, zorder=5)
        title = ax.set_title("", fontsize=11)
        panels.append((tr, closed_sc, open_sc, cur_sc, path_ln, title))

    def update(frame):
        k = math.ceil((frame + 1) / frames * max_steps)   # 이번 프레임까지의 확장 수
        artists = []
        for tr, closed_sc, open_sc, cur_sc, path_ln, title in panels:
            n = min(k, tr.expanded)
            done = tr.steps[:n]
            closed = [s.node for s in done]
            closed_set = set(closed)
            frontier = {v for s in done for v in s.pushed} - closed_set

            closed_sc.set_offsets([xy[c] for c in closed] or [[math.nan, math.nan]])
            closed_sc.set_color([cmap(i / max(1, max_steps - 1)) for i in range(n)] or "none")
            open_sc.set_offsets([xy[v] for v in frontier] or [[math.nan, math.nan]])
            cur_sc.set_offsets([xy[closed[-1]]] if closed else [[math.nan, math.nan]])

            finished = n == tr.expanded
            path_ln.set_color("red" if finished else "#d62728aa")
            if finished and tr.path:
                path_ln.set_data(*zip(*[xy[p] for p in tr.path]))
            else:
                chain = [xy[c] for c in _partial_chain(done)]
                path_ln.set_data(*zip(*chain)) if chain else path_ln.set_data([], [])

            status = _cost_text(tr) if finished else f"g={done[-1].g:.0f} f={done[-1].f:.0f}"
            title.set_text(f"{_label(tr)}\nexpanded {n}/{tr.expanded} | {status}")
            artists += [closed_sc, open_sc, cur_sc, path_ln, title]
        return artists

    total = frames + fps * 2  # 끝난 상태로 2초 정지
    anim = FuncAnimation(fig, lambda i: update(min(i, frames - 1)), frames=total,
                         interval=1000 / fps, blit=False)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    if out_file:
        anim.save(out_file, writer=PillowWriter(fps=fps))
    if show:
        plt.show()
    plt.close(fig)
