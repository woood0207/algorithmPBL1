"""웹 뷰어용 로컬 서버. 표준 라이브러리만 쓴다.
알고리즘은 전부 Python 엔진에서 돌고, 브라우저는 Trace JSON을 받아 그리기만 한다.
"""
import argparse
import heapq
import json
import math
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from analysis import ANALYSIS_ALGOS, compare_all, run_analysis
from bus_api import BusAPIError, fetch_bus_feed
from graph_data import distances, load_graph
from graph_data import Vehicle, haversine
from landmarks import ALT, METHODS, pick_landmarks
from search import ALGORITHMS, HEURISTICS, run_search
from trace_io import trace_to_dict

WEB_DIR = Path(__file__).resolve().parent / "web"
OFFLINE_MAP = Path(__file__).resolve().parent / "localOSM" / "map_data.js"   # redduk의 오프라인 지도 데이터
OPTION_KEYS = ("beam_width", "budget", "seed", "no_revisit", "waypoints", "drive_radius", "walk_kmh", "drive_kmh", "drive_rule", "uphill", "stairs")


class App:
    def __init__(self, graph, dataset):
        self.graph = graph
        self.dataset = dataset
        self.alt_cache = {}
        self.lock = threading.Lock()
        self.bus_lock = threading.Lock()
        self.bus_cache = None
        self.job = {"state": "idle", "done": 0, "total": 0, "result": None, "error": None}

    def bus_feed(self, force_refresh=False):
        with self.bus_lock:
            if not force_refresh and self.bus_cache and self.bus_cache["fetched_at"] > time.time() - 30:
                return self.bus_cache
            if force_refresh:
                self.bus_cache = None
            self.bus_cache = fetch_bus_feed()
            return self.bus_cache

    def meta(self):
        g = self.graph
        start, goal = g.default_endpoints()
        return dict(
            dataset=self.dataset, stats=g.stats, center=g.center, radius=g.radius, network_type=g.network_type,
            nodes=[[n, *c] for n, c in g.coords.items()],
            roads=[[r.id, r.u, r.v, r.w, r.name, r.highway, r.coords, r.length] for r in g.roads],
            elevation=g.elevation or None, weight_unit=g.weight_unit, basis=g.basis,
            main_nodes=g.main_nodes, default_start=start, default_goal=goal,
            algorithms=[dict(id=a.id, label=a.label, uses_heuristic=a.uses_heuristic, hint=a.hint,
                             default_heuristic=a.default_heuristic, default_weight=a.default_weight,
                             comparable=a.comparable) for a in ALGORITHMS.values()],
            heuristics=HEURISTICS, landmark_methods=METHODS, analysis_algos=ANALYSIS_ALGOS,
        )

    def alt(self, landmarks):
        key = tuple(landmarks)
        with self.lock:
            if key not in self.alt_cache:
                if len(self.alt_cache) >= 32:
                    self.alt_cache.pop(next(iter(self.alt_cache)))
                self.alt_cache[key] = ALT(self.graph, key)
            return self.alt_cache[key]

    def options(self, body):
        given = body.get("options") or {}
        out = {k: given[k] for k in OPTION_KEYS if given.get(k) is not None}
        if given.get("drive_center"):
            out["drive_center"] = tuple(given["drive_center"])
        return out

    def landmarks(self, body):
        ids = body.get("ids")
        if ids is None:
            ids = pick_landmarks(self.graph, int(body.get("k", 4)), body.get("method", "farthest"), body.get("seed", 0))
        if not ids:
            raise ValueError("랜드마크가 최소 하나는 있어야 합니다.")
        alt = self.alt(ids)
        return dict(landmarks=alt.landmarks, prep_ms=alt.prep_ms, dijkstra_runs=2 * len(alt.landmarks),
                    stored=alt.stored_distances)

    def run(self, body):
        alt = self.alt(body["landmarks"]) if body.get("landmarks") else None
        options = self.options(body)
        traces = []
        for panel in body["panels"]:
            spec = ALGORITHMS[panel["algorithm"]]
            if panel["algorithm"] in ("transit_dijkstra", "transit_astar"):
                kw = {"bus_feed": self.bus_feed(), "walk_kmh": options.get("walk_kmh", 5.0)}
            elif panel["algorithm"] == "via":
                kw = {"waypoints": options.get("waypoints", [])}
            elif panel["algorithm"] == "alt_uphill":
                kw = {"uphill": panel.get("uphill", options.get("uphill", 8.0)), "stairs": panel.get("stairs", options.get("stairs", 1.0)),
                      "landmarks": body.get("landmarks") or None}
            elif panel["algorithm"] == "vehicle_switch":
                kw = {k: options[k] for k in ("drive_center", "drive_radius", "walk_kmh", "drive_kmh", "drive_rule") if k in options}
            else:
                kw = {k: options[k] for k in ("beam_width", "budget", "seed", "no_revisit") if k in options}
            trace = run_search(self.graph, panel["algorithm"], int(body["start"]), int(body["goal"]),
                               heuristic=panel.get("heuristic") if spec.uses_heuristic else None,
                               weight=panel.get("weight"), alt=alt, **kw)
            traces.append(trace_to_dict(trace))
        return dict(traces=traces, prep_ms=alt.prep_ms if alt else 0)

    def route_comparison(self, body):
        start, goal = int(body["start"]), int(body["goal"])
        if start not in self.graph.coords or goal not in self.graph.coords:
            raise ValueError(f"출발 {start} 또는 도착 {goal}이(가) 정점 목록에 없습니다.")
        options = self.options(body)
        walk_kmh = float(options.get("walk_kmh", 5.0))
        if not math.isfinite(walk_kmh) or walk_kmh <= 0:
            raise ValueError("도보 속도는 유한한 양수여야 합니다.")

        roads_by_source = {}
        for road in self.graph.roads:
            if road.u != road.v and (not road.permissions or road.permits(Vehicle.WALK)):
                roads_by_source.setdefault(road.u, []).append(road)
        walk_mps = walk_kmh / 3.6
        walk_dist = {start: 0.0}
        walk_parent = {start: None}
        queue = [(0.0, start)]
        while queue:
            elapsed, node = heapq.heappop(queue)
            if elapsed != walk_dist[node]:
                continue
            if node == goal:
                break
            for road in roads_by_source.get(node, ()):
                candidate = elapsed + road.length_m / walk_mps
                if candidate < walk_dist.get(road.v, math.inf):
                    walk_dist[road.v] = candidate
                    walk_parent[road.v] = (node, road.id)
                    heapq.heappush(queue, (candidate, road.v))

        walking = None
        if goal in walk_dist:
            path_edges = []
            node = goal
            while walk_parent[node] is not None:
                node, edge_id = walk_parent[node]
                path_edges.append(edge_id)
            distance_m = sum(self.graph.roads[edge_id].length_m for edge_id in path_edges)
            walking = {"time_s": walk_dist[goal], "distance_m": distance_m}

        transit = None
        transit_error = None
        service_ended = False
        last_bus_missed = False
        try:
            feed = self.bus_feed()
            service_ended = bool(feed.get("service_ended"))
            transit_trace = run_search(self.graph, "transit_dijkstra", start, goal,
                                        bus_feed=feed, walk_kmh=walk_kmh)
            service_ended = bool(transit_trace.extra.get("service_ended", service_ended))
            last_bus_missed = bool(transit_trace.extra.get("last_bus_missed"))
        except BusAPIError as error:
            transit_error = str(error)
        else:
            if transit_trace.found:
                segments = transit_trace.segments
                bus_indexes = [i for i, segment in enumerate(segments) if segment["kind"] == "bus"]
                if bus_indexes:
                    first_bus, last_bus = bus_indexes[0], bus_indexes[-1]
                    access_walk_s = sum(segment.get("walk_s", 0.0) for segment in segments[:first_bus])
                    egress_walk_s = sum(segment.get("walk_s", 0.0) for segment in segments[last_bus + 1:])
                    transfer_walks = [
                        sum(segment.get("walk_s", 0.0) for segment in segments[left + 1:right])
                        for left, right in zip(bus_indexes, bus_indexes[1:])
                    ]
                else:
                    access_walk_s = transit_trace.extra["walk_s"]
                    egress_walk_s = 0.0
                    transfer_walks = []
                transit = {
                    "time_s": transit_trace.cost,
                    "walk_s": transit_trace.extra["walk_s"],
                    "wait_s": transit_trace.extra["wait_s"],
                    "ride_s": transit_trace.extra["ride_s"],
                    "access_walk_s": access_walk_s,
                    "egress_walk_s": egress_walk_s,
                    "transfer_walks_s": transfer_walks,
                    "walk_distance_m": transit_trace.extra["walk_distance_m"],
                    "ride_distance_m": transit_trace.extra["ride_distance_m"],
                    "service_ended": service_ended,
                    "service_message": transit_trace.extra.get("service_message"),
                    "last_bus_missed": last_bus_missed,
                    "buses": [{"route_no": segment["route_no"], "wait_s": segment["wait_s"],
                               "ride_s": segment["ride_s"]}
                              for segment in segments if segment["kind"] == "bus"],
                }
        delta_s = walking["time_s"] - transit["time_s"] if walking and transit else None
        return {"walking": walking, "transit": transit, "delta_s": delta_s,
                "start": start, "goal": goal, "walk_kmh": walk_kmh, "transit_error": transit_error,
                "service_ended": service_ended, "last_bus_missed": last_bus_missed}

    def compare(self, body):
        alt = self.alt(body["landmarks"])
        options = {k: v for k, v in self.options(body).items() if k in ("beam_width", "budget", "seed", "no_revisit")}
        start, goal = int(body["start"]), int(body["goal"])
        results = compare_all(self.graph, start, goal, alt, repeats=int(body.get("repeats", 3)), **options)
        return dict(start=start, goal=goal, config=options, landmarks=alt.landmarks, alt_prep_ms=alt.prep_ms,
                    heuristic_scale=self.graph.alpha, weight_unit=self.graph.weight_unit, basis=self.graph.basis,
                    note="Median of repeated runs after one warm-up, including Trace creation; excludes ALT preprocessing.",
                    results=results)

    def heuristic_table(self, body):
        g, goal = self.graph, int(body["goal"])
        alt = self.alt(body["landmarks"])
        true = distances(g, goal, reverse=True)
        out = {}
        for n in g.coords:
            h, li = alt.bound(n, goal)
            out[n] = dict(true=true.get(n), alt=h, li=li, hav=g.heuristic(n, goal), geo=g.geographic(n, goal))
        return dict(goal=goal, alpha=g.alpha, table=out)

    def start_analysis(self, body):
        with self.lock:
            if self.job["state"] == "running":
                return self.job_status()
            self.job = {"state": "running", "done": 0, "total": int(body.get("pairs", 300)), "result": None, "error": None}

        def progress(done, total):
            self.job.update(done=done, total=total)

        def work():
            try:
                rows, ov, by_bin, pairs = run_analysis(
                    self.graph, algos=body.get("algos") or None, pairs=int(body.get("pairs", 300)),
                    bins=int(body.get("bins", 5)), seed=int(body.get("seed", 42)), repeats=int(body.get("repeats", 3)),
                    budget=int(body.get("budget", 2000)), beam_width=int(body.get("beam_width", 5)),
                    walk_seeds=int(body.get("walk_seeds", 10)), progress=progress)
                self.job.update(state="done", result=dict(rows=rows, overall=ov, by_bin=by_bin, pairs=pairs))
            except Exception as error:  # 작업 스레드의 오류를 화면에 보여 준다
                self.job.update(state="error", error=str(error))

        threading.Thread(target=work, daemon=True).start()
        return self.job_status()

    def job_status(self):
        return dict(self.job)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, app=None, **kwargs):
        self.app = app
        super().__init__(*args, **kwargs)

    def log_message(self, *args):
        pass

    def send_json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self):
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        parsed_path = urlsplit(self.path)
        if parsed_path.path == "/api/meta":
            return self.send_json(self.app.meta())
        if parsed_path.path == "/api/bus":
            try:
                query = parse_qs(parsed_path.query)
                force_refresh = query.get("force_refresh", ["false"])[0].lower() == "true"
                return self.send_json(self.app.bus_feed(force_refresh=force_refresh))
            except BusAPIError as error:
                return self.send_json(dict(error=str(error)), 502)
        if parsed_path.path == "/api/analysis":
            return self.send_json(self.app.job_status())
        if parsed_path.path == "/offline/map_data.js":
            if not OFFLINE_MAP.exists():
                return self.send_json(dict(error="localOSM/map_data.js가 없습니다."), 404)
            data = OFFLINE_MAP.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return None
        return super().do_GET()

    def do_POST(self):
        routes = {"/api/landmarks": self.app.landmarks, "/api/run": self.app.run, "/api/compare": self.app.compare,
                  "/api/heuristic": self.app.heuristic_table, "/api/analysis": self.app.start_analysis,
                  "/api/route-comparison": self.app.route_comparison}
        if self.path not in routes:
            return self.send_json(dict(error="없는 주소입니다."), 404)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            self.send_json(routes[self.path](body))
        except BusAPIError as error:
            self.send_json(dict(error=str(error)), 502)
        except (ValueError, KeyError, TypeError) as error:
            self.send_json(dict(error=str(error)), 400)


def serve(graph, dataset, port=8000):
    handler = partial(Handler, app=App(graph, dataset), directory=str(WEB_DIR))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as error:
        raise ValueError(f"포트 {port}에서 서버를 시작할 수 없습니다. --port 8001 등 다른 포트를 지정하세요.") from error
    with server:
        print(f"웹 뷰어: http://127.0.0.1:{server.server_port}/  (종료: Ctrl+C)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n서버를 종료했습니다.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="경로 탐색 웹 뷰어 서버")
    parser.add_argument("--serve", action="store_true", help="서버를 실행합니다 (기본 동작)")
    parser.add_argument("--port", type=int, default=8000, help="로컬 HTTP 포트 (기본값: 8000)")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent / "data",
                        help="road_nodes.csv와 road_edges.csv가 있는 폴더")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port는 0~65535 사이여야 합니다.")

    from main import dataset_paths

    nodes, edges, graphml = dataset_paths(args.data_dir)
    graph = load_graph(nodes, edges, graphml)
    serve(graph, str(args.data_dir), args.port)


if __name__ == "__main__":
    main()
