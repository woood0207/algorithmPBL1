"""
실행 스크립트: 데이터 로드 -> 탐색 -> trace JSON 저장 -> 시각화

  python main.py                                  # 웹 뷰어 (기본)
  python main.py compare --weights 0 1 3          # w별 탐색 -> PNG, GIF
  python main.py compare --algos dijkstra astar greedy --heuristic alt
  python main.py compare --viz-only               # 저장된 traces/*.json 만으로 다시 그리기
  python main.py route --start 1 --end 100        # road_route처럼 경로 JSON과 지도 HTML
  python main.py route --start-coord 36.01 129.33 --end-coord 36.00 129.32 --solver vehicle_switch
  python main.py via --start 1 --end 500 --waypoints 100 250 320
  python main.py benchmark --start 1 --goal 60 --repeats 5
  python main.py analyze --pairs 300 --bins 5
  python main.py build --center 36.01243647 129.32180209 --radius 1000
  python main.py elevation                        # 정점 고도를 받아 data/road_elevation.csv 저장
  python main.py route --start 1 --end 100 --solver alt_uphill --uphill 8 --stairs 3
  python main.py selftest
"""
import argparse
import glob
import json
import math
import os
import sys
import unittest
from pathlib import Path
from urllib.parse import urlparse

from analysis import ANALYSIS_ALGOS, compare_all, print_report, run_analysis, save_analysis
from graph_data import load_graph
from landmarks import ALT, METHODS, pick_landmarks
from search import ALGORITHMS, HEURISTICS, run_search
from trace_io import load_trace, save_trace

HERE = Path(__file__).resolve().parent


def dataset_paths(data_dir):
    d = Path(data_dir)
    for suffix in ("", "_numbered"):
        nodes, edges = d / f"road_nodes{suffix}.csv", d / f"road_edges{suffix}.csv"
        if nodes.exists() and edges.exists():
            graphml = d / "roads.graphml"
            return nodes, edges, graphml if graphml.exists() else None
    raise ValueError(f"{d}에서 road_nodes.csv / road_edges.csv를 찾을 수 없습니다.")


def open_graph(args):
    nodes, edges, graphml = dataset_paths(args.data_dir)
    graph = load_graph(args.nodes or nodes, args.edges or edges, graphml if not (args.nodes or args.edges) else None)
    print("graph:", graph.stats)
    return graph


def make_alt(graph, args, needed):
    if not needed:
        return None
    return ALT(graph, args.landmarks or pick_landmarks(graph, args.k, args.landmark_method))


def endpoint_or_default(graph, start, goal):
    default_start, default_goal = graph.default_endpoints()
    return (default_start if start is None else start), (default_goal if goal is None else goal)


# ---------------------------------------------------------------- compare
def compare_table(traces):
    found = [t.cost for t in traces if t.cost is not None]
    best = min(found) if found else None
    print(f"\n{'algorithm':<34} {'expanded':>9} {'cost':>10} {'cost/best':>9} {'path len':>8} {'status':>14}")
    for t in traces:
        cost = "-" if t.cost is None else f"{t.cost:.1f}"
        ratio = "-" if t.cost is None or not best else f"{t.cost / best:.4f}"
        print(f"{t.label:<34} {t.expanded:>9} {cost:>10} {ratio:>9} {len(t.path):>8} {t.reason or 'ok':>14}")


def cmd_compare(args):
    from visualize import animate, draw_static

    out = Path(args.out)
    (out / "traces").mkdir(parents=True, exist_ok=True)
    graph = open_graph(args)

    if args.viz_only:
        files = sorted(glob.glob(str(out / "traces" / "*.json")))
        if not files:
            raise ValueError(f"{out / 'traces'}에 저장된 trace가 없습니다.")
        traces = sorted((load_trace(f) for f in files), key=lambda t: (t.weight is None, t.weight))
    else:
        for old in glob.glob(str(out / "traces" / "*.json")):
            os.remove(old)
        start, goal = endpoint_or_default(graph, args.start, args.goal)
        alt = make_alt(graph, args, args.heuristic in ("alt", "max"))
        panels = [("weighted", w) for w in args.weights] if not args.algos else [(a, None) for a in args.algos]
        options = dict(beam_width=args.beam_width, budget=args.budget, seed=args.seed, no_revisit=args.no_revisit)
        traces = []
        for i, (algorithm, weight) in enumerate(panels):
            kw = options if algorithm not in ("via", "vehicle_switch", "dijkstra", "astar", "weighted") else {}
            if algorithm == "via":
                kw = dict(waypoints=args.waypoints or [])
            if algorithm == "vehicle_switch":
                kw = dict(drive_radius=args.drive_radius, walk_kmh=args.walk_kmh, drive_kmh=args.drive_kmh)
            trace = run_search(graph, algorithm, start, goal, heuristic=args.heuristic, weight=weight, alt=alt, **kw)
            name = f"trace_w{weight:g}.json" if weight is not None else f"trace_{i}_{algorithm}.json"
            save_trace(trace, out / "traces" / name)
            traces.append(trace)

    compare_table(traces)
    draw_static(graph, traces, out / "result.png")
    animate(graph, traces, out / "search.gif", frames=args.frames, show=args.show)
    print("\nsaved:", out / "result.png", "/", out / "search.gif")


# ---------------------------------------------------------------- route (road_route)
def cmd_route(args):
    from road_map import edges_of, map_html, select_endpoint, serve_map

    graph = open_graph(args)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    start = select_endpoint(graph.coords, args.start, args.start_coord, "출발", args.max_snap)
    end = select_endpoint(graph.coords, args.end, args.end_coord, "도착", args.max_snap)
    options = {}
    if args.solver == "vehicle_switch":
        options = dict(drive_radius=args.drive_radius, walk_kmh=args.walk_kmh, drive_kmh=args.drive_kmh,
                       drive_rule=args.drive_rule)
        if args.drive_center:
            options["drive_center"] = tuple(args.drive_center)
    if args.solver == "via":
        options = dict(waypoints=args.waypoints or [])
    if args.solver == "alt_uphill":
        options = dict(uphill=args.uphill, stairs=args.stairs, landmarks=args.landmarks)
    alt = make_alt(graph, args, args.heuristic in ("alt", "max"))
    trace = run_search(graph, args.solver, start["id"], end["id"], heuristic=args.heuristic, alt=alt, **options)
    if not trace.found:
        raise ValueError("출발에서 도착까지 경로를 찾지 못했습니다. "
                         f"({trace.reason}) 추출 범위 밖으로 우회해야 한다면 build --radius를 늘려 다시 받으세요.")

    edges = edges_of(graph)
    rows = [e + 1 for e in trace.edge_ids]
    result = {
        "start": start, "end": end, "algorithm": trace.label, "cost": trace.cost, "cost_unit": trace.cost_unit,
        "distance_m": sum(edges[r - 1].weight for r in rows), "path": trace.path, "edge_rows": rows,
        "segments": [{"edge_row": r, "source": edges[r - 1].source, "target": edges[r - 1].target,
                      "weight_m": edges[r - 1].weight} for r in rows],
        "route_segments": [dict(kind=s["kind"], edge_rows=[e + 1 for e in s["edge_ids"]],
                                **{k: v for k, v in s.items() if k in ("meters", "minutes")}) for s in trace.segments],
        "via": trace.extra.get("via"), "ranking": trace.extra.get("ranking"),
    }
    stem = f"route_{start['id']}_{end['id']}"
    (output / f"{stem}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("경로:", " → ".join(map(str, trace.path)))
    print(f"{trace.label}: {trace.cost:,.2f} {trace.cost_unit}, 도로 거리 {result['distance_m']:,.2f} m")
    if "climb_m" in trace.extra:
        x = trace.extra
        result.update({k: x[k] for k in ("uphill_lambda", "stairs_factor", "climb_m", "descent_m", "stairs_m",
                                         "shortest_distance_m", "shortest_climb_m", "shortest_stairs_m")})
        print(f"오르막 {x['climb_m']:.0f} m, 계단 {x['stairs_m']:.0f} m "
              f"(최단 경로 {x['shortest_distance_m']:,.0f} m는 오르막 {x['shortest_climb_m']:.0f} m, 계단 {x['shortest_stairs_m']:.0f} m)")
    for seg in result["route_segments"]:
        if "minutes" in seg:
            print(f"  {seg['kind']}: {seg['meters']:.1f} m, {seg['minutes']:.2f} 분")
    print("JSON:", output / f"{stem}.json")
    try:
        map_html(graph.coords, edges, output / f"{stem}.html", graph.center, graph.radius, result,
                 trace.extra.get("drive_nodes"))
    except ImportError:
        print("folium이 없어 지도 HTML은 건너뜁니다. (pip install folium)")
        return
    print("지도:", output / f"{stem}.html")
    if args.serve:
        serve_map(output / f"{stem}.html", args.port)


def cmd_via(args):
    from visualize import draw_static

    graph = open_graph(args)
    start, goal = endpoint_or_default(graph, args.start, args.end)
    trace = run_search(graph, "via", start, goal, waypoints=args.waypoints)
    print(f"\n{'순위':>4} {'경유지':>7} {'총거리':>10} {'출발→경유':>10} {'경유→도착':>10}")
    for i, r in enumerate(trace.extra["ranking"], 1):
        fmt = lambda v: "∞" if v is None else f"{v:.1f}"
        print(f"{i:>4} {r['node']:>7} {fmt(r['total']):>10} {fmt(r['from_start']):>10} {fmt(r['to_goal']):>10}")
    if not trace.found:
        raise ValueError("어떤 경유지로도 도착할 수 없습니다.")
    print(f"\n선택: {trace.extra['via']}  총거리 {trace.cost:.2f} m")
    print("경로:", trace.path)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    save_trace(trace, out / "via_trace.json")
    draw_static(graph, [trace], out / "via_route.png")
    print("그림 저장:", out / "via_route.png")


def cmd_benchmark(args):
    graph = open_graph(args)
    alt = ALT(graph, args.landmarks or pick_landmarks(graph, 6, "geo_farthest"))
    results = compare_all(graph, args.start, args.goal, alt, repeats=args.repeats, budget=args.budget, seed=args.seed)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(dict(start=args.start, goal=args.goal, alt_prep_ms=alt.prep_ms, landmarks=alt.landmarks,
                       seed=args.seed, budget=args.budget, results=results), f, ensure_ascii=False, indent=2)
    print(f"\n{'algorithm':<36} {'cost':>10} {'ratio':>7} {'expanded':>9} {'median ms':>10}")
    for r in results:
        cost = "-" if r["cost"] is None else f"{r['cost']:.1f}"
        ratio = "-" if r["cost_ratio"] is None else f"{r['cost_ratio']:.3f}"
        print(f"{r['label']:<36} {cost:>10} {ratio:>7} {r['expanded']:>9} {r['median_ms']:>10.3f}")
    print("Saved:", args.output)


def cmd_analyze(args):
    graph = open_graph(args)
    rows, ov, by_bin, pairs = run_analysis(graph, args.algos, args.pairs, args.bins, args.seed, args.repeats,
                                           args.budget, args.beam_width, args.walk_seeds)
    print(f"노드 {len(graph.coords)}개, 표본 쌍 {len(pairs)}개 (seed={args.seed})")
    print_report(ov, by_bin)
    save_analysis(rows, ov, by_bin, {k: v for k, v in vars(args).items() if k != "func"}, args.outdir)


def cmd_build(args):
    import osmnx as ox
    from road_map import check_coordinate, download_graph, edges_of, map_html, number_graph, save_numbered_csv, serve_map

    data = Path(args.data_dir)
    output = Path(args.output_dir)
    data.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    if args.load_graph:
        graph = ox.load_graphml(args.load_graph)
        center = json.loads(graph.graph["route_center"]) if "route_center" in graph.graph else None
        radius = float(graph.graph["route_radius_m"]) if "route_radius_m" in graph.graph else None
        print(f"저장된 도로망 사용: {args.load_graph}")
    else:
        check_coordinate(args.center)
        center, radius = args.center, args.radius
        print(f"중심 {center}, 반경 {radius:g}m 원형 영역의 도로망을 추출합니다.", flush=True)
        graph = download_graph(center, radius, args.network_type, args.timeout, args.overpass_url, data / "cache")
    nodes, edges, mapping = number_graph(graph)
    save_numbered_csv(nodes, edges, data)
    ox.save_graphml(graph, filepath=data / "roads.graphml")
    (data / "node_id_mapping.json").write_text(
        json.dumps({str(new): int(old) for old, new in mapping.items()}, indent=2), encoding="utf-8")
    map_path = output / "graph_map.html"
    map_html(nodes, edges, map_path, center, radius)
    print(f"정점 {len(nodes):,}개 / 간선 {len(edges):,}개")
    print(f"정점·간선 CSV, GraphML 저장: {data.resolve()}")
    print(f"지도 파일: {map_path.resolve()}")
    if args.serve:
        serve_map(map_path, args.port)


def cmd_serve(args):
    from server import serve

    graph = open_graph(args)
    serve(graph, str(args.data_dir), args.port)


def cmd_elevation(args):
    from elevation import elevation_path, fetch_elevation

    nodes, edges, graphml = dataset_paths(args.data_dir)
    graph = load_graph(nodes, edges)
    path = elevation_path(nodes)
    z = fetch_elevation(graph.coords, path)
    print(f"저장: {path} (정점 {len(z)}개, 고도 {min(z.values()):.0f}~{max(z.values()):.0f} m)")


def cmd_selftest(args):
    sys.argv = sys.argv[:1]
    os.chdir(HERE)
    suite = unittest.defaultTestLoader.loadTestsFromName("test_engine")
    if not unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful():
        raise SystemExit(1)


# ---------------------------------------------------------------- 인자
def common(parser):
    parser.add_argument("--data-dir", default=str(HERE / "data"),
                        help="CSV 폴더. 예전 830개 그래프: data/legacy_830")
    parser.add_argument("--nodes", help="정점 CSV를 직접 지정")
    parser.add_argument("--edges", help="간선 CSV를 직접 지정")
    return parser


def alt_options(parser):
    parser.add_argument("--k", type=int, default=4, help="랜드마크 수")
    parser.add_argument("--landmark-method", choices=METHODS, default="farthest")
    parser.add_argument("--landmarks", type=int, nargs="+", help="랜드마크 ID를 직접 지정")


def drive_options(parser):
    parser.add_argument("--drive-radius", type=float, default=200, help="차량 구역 반경(m)")
    parser.add_argument("--drive-center", type=float, nargs=2, metavar=("LAT", "LON"), help="차량 구역 중심")
    parser.add_argument("--walk-kmh", type=float, default=5.0)
    parser.add_argument("--drive-kmh", type=float, default=50.0)
    parser.add_argument("--drive-rule", choices=["edge", "node"], default="edge",
                        help="edge: 양 끝이 구역 안, node: 도착 정점만 구역 안(원본 코드)")


def parse_args():
    ap = argparse.ArgumentParser(description="PBL 통합 경로 탐색 시각화")
    sub = ap.add_subparsers(dest="command")

    p = common(sub.add_parser("serve", help="웹 뷰어 (기본)"))
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = common(sub.add_parser("compare", help="w별 또는 알고리즘별 탐색 후 PNG, GIF 저장"))
    p.add_argument("--start", type=int)
    p.add_argument("--goal", type=int)
    p.add_argument("--weights", type=float, nargs="+", default=[0, 1, 3])
    p.add_argument("--algos", nargs="+", choices=list(ALGORITHMS), help="지정하면 --weights 대신 이 알고리즘들을 비교")
    p.add_argument("--heuristic", choices=list(HEURISTICS), default="haversine")
    p.add_argument("--out", default=str(HERE / "output"))
    p.add_argument("--frames", type=int, default=150)
    p.add_argument("--show", action="store_true", help="창으로 애니메이션 보기")
    p.add_argument("--viz-only", action="store_true", help="저장된 trace만으로 다시 그리기")
    p.add_argument("--beam-width", type=int, default=5)
    p.add_argument("--budget", type=int, default=2000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--no-revisit", action="store_true")
    p.add_argument("--waypoints", type=int, nargs="+")
    alt_options(p)
    p.add_argument("--drive-radius", type=float, default=200)
    p.add_argument("--walk-kmh", type=float, default=5.0)
    p.add_argument("--drive-kmh", type=float, default=50.0)
    p.set_defaults(func=cmd_compare)

    p = common(sub.add_parser("route", help="ID 또는 좌표로 경로를 찾아 JSON과 지도 HTML 저장"))
    for endpoint in ("start", "end"):
        group = p.add_mutually_exclusive_group(required=True)
        group.add_argument(f"--{endpoint}", type=int)
        group.add_argument(f"--{endpoint}-coord", type=float, nargs=2, metavar=("LAT", "LON"))
    p.add_argument("--solver", choices=list(ALGORITHMS), default="dijkstra")
    p.add_argument("--heuristic", choices=list(HEURISTICS), default="haversine")
    p.add_argument("--waypoints", type=int, nargs="+")
    p.add_argument("--max-snap", type=float, default=500, help="입력 좌표에서 도로 정점까지 최대 허용 거리(m)")
    p.add_argument("--output-dir", default=str(HERE / "output" / "road_route"))
    p.add_argument("--serve", action="store_true")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--uphill", type=float, default=8.0, help="alt_uphill: 1 m 오르막 = 평지 몇 m (λ)")
    p.add_argument("--stairs", type=float, default=1.0, help="alt_uphill: 계단 간선 거리 배수 (1 = 벌점 없음)")
    alt_options(p)
    drive_options(p)
    p.set_defaults(func=cmd_route)

    p = sub.add_parser("elevation", help="정점 고도를 Open-Meteo에서 받아 road_elevation.csv 저장 (인터넷 필요)")
    p.add_argument("--data-dir", default=str(HERE / "data"))
    p.set_defaults(func=cmd_elevation)

    p = common(sub.add_parser("via", help="경유지 후보 중 최선을 고르는 최단 경로"))
    p.add_argument("--start", type=int)
    p.add_argument("--end", type=int)
    p.add_argument("--waypoints", type=int, nargs="+", required=True)
    p.add_argument("--out", default=str(HERE / "output"))
    p.set_defaults(func=cmd_via)

    p = common(sub.add_parser("benchmark", help="모든 알고리즘을 반복 측정해 results.json 저장"))
    p.add_argument("--start", type=int, required=True)
    p.add_argument("--goal", type=int, required=True)
    p.add_argument("--output", default=str(HERE / "output" / "results.json"))
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--budget", type=int, default=2000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--landmarks", type=int, nargs="+")
    p.set_defaults(func=cmd_benchmark)

    p = common(sub.add_parser("analyze", help="비최적 알고리즘 정량 분석"))
    p.add_argument("--algos", nargs="+", default=ANALYSIS_ALGOS,
                   choices=["greedy", "beam", "local", "random", "bfs", "astar", "bidirectional"])
    p.add_argument("--pairs", type=int, default=300)
    p.add_argument("--bins", type=int, default=5)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--budget", type=int, default=2000)
    p.add_argument("--beam-width", type=int, default=5)
    p.add_argument("--walk-seeds", type=int, default=10)
    p.add_argument("--outdir", default=str(HERE / "output" / "analysis_out"))
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("build", help="OSM에서 도로망을 받아 data/를 새로 만든다 (osmnx 필요)")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--center", nargs=2, type=float, metavar=("LAT", "LON"))
    source.add_argument("--load-graph", type=Path, help="다운로드 대신 저장된 OSMnx GraphML 사용")
    p.add_argument("--data-dir", default=str(HERE / "data"))
    p.add_argument("--radius", type=float, default=1000)
    p.add_argument("--network-type", choices=["walk", "drive", "bike", "all"], default="walk")
    p.add_argument("--output-dir", default=str(HERE / "output" / "road_route"))
    p.add_argument("--timeout", type=int, default=180)
    p.add_argument("--overpass-url", help="Overpass 서버 기본 URL (/api까지)")
    p.add_argument("--serve", action="store_true")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("selftest", help="엔진 회귀 테스트")
    p.set_defaults(func=cmd_selftest)

    args = ap.parse_args(sys.argv[1:] or ["serve"])
    if not args.command:
        return ap.parse_args(["serve"] + sys.argv[1:])
    if args.command == "build":
        if not math.isfinite(args.radius) or args.radius <= 0 or args.timeout <= 0:
            ap.error("반경과 제한 시간은 유한한 양수여야 합니다.")
        if args.overpass_url and urlparse(args.overpass_url).scheme != "https":
            ap.error("--overpass-url은 https://로 시작해야 합니다.")
    if getattr(args, "port", 8000) and not 1 <= getattr(args, "port", 8000) <= 65535:
        ap.error("포트는 1~65535 사이여야 합니다.")
    return args


def main():
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f"오류: {error}") from error
