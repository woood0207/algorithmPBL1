"""웹 뷰어용 로컬 서버. 표준 라이브러리만 쓴다.
알고리즘은 전부 Python 엔진에서 돌고, 브라우저는 Trace JSON을 받아 그리기만 한다.
"""
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from analysis import ANALYSIS_ALGOS, compare_all, run_analysis
from graph_data import distances
from landmarks import ALT, METHODS, pick_landmarks
from search import ALGORITHMS, HEURISTICS, run_search
from trace_io import trace_to_dict

WEB_DIR = Path(__file__).resolve().parent / "web"
OPTION_KEYS = ("beam_width", "budget", "seed", "no_revisit", "waypoints", "drive_radius", "walk_kmh", "drive_kmh", "drive_rule")


class App:
    def __init__(self, graph, dataset):
        self.graph = graph
        self.dataset = dataset
        self.alt_cache = {}
        self.lock = threading.Lock()
        self.job = {"state": "idle", "done": 0, "total": 0, "result": None, "error": None}

    def meta(self):
        g = self.graph
        start, goal = g.default_endpoints()
        return dict(
            dataset=self.dataset, stats=g.stats, center=g.center, radius=g.radius, network_type=g.network_type,
            nodes=[[n, *c] for n, c in g.coords.items()],
            roads=[[r.id, r.u, r.v, r.w, r.name, r.highway, r.coords] for r in g.roads],
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
            if panel["algorithm"] == "via":
                kw = {"waypoints": options.get("waypoints", [])}
            elif panel["algorithm"] == "walk_drive_walk":
                kw = {k: options[k] for k in ("drive_center", "drive_radius", "walk_kmh", "drive_kmh", "drive_rule") if k in options}
            else:
                kw = {k: options[k] for k in ("beam_width", "budget", "seed", "no_revisit") if k in options}
            trace = run_search(self.graph, panel["algorithm"], int(body["start"]), int(body["goal"]),
                               heuristic=panel.get("heuristic") if spec.uses_heuristic else None,
                               weight=panel.get("weight"), alt=alt, **kw)
            traces.append(trace_to_dict(trace))
        return dict(traces=traces, prep_ms=alt.prep_ms if alt else 0)

    def compare(self, body):
        alt = self.alt(body["landmarks"])
        options = {k: v for k, v in self.options(body).items() if k in ("beam_width", "budget", "seed", "no_revisit")}
        start, goal = int(body["start"]), int(body["goal"])
        results = compare_all(self.graph, start, goal, alt, repeats=int(body.get("repeats", 3)), **options)
        return dict(start=start, goal=goal, config=options, landmarks=alt.landmarks, alt_prep_ms=alt.prep_ms,
                    heuristic_scale=self.graph.alpha, weight_unit="m",
                    note="Median of repeated runs after one warm-up, including Trace creation; excludes ALT preprocessing.",
                    results=results)

    def heuristic_table(self, body):
        g, goal = self.graph, int(body["goal"])
        alt = self.alt(body["landmarks"])
        true = distances(g, goal, reverse=True)
        out = {}
        for n in g.coords:
            h, li = alt.bound(n, goal)
            out[n] = dict(true=true.get(n), alt=h, li=li, hav=g.heuristic(n, goal))
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
        if self.path == "/api/meta":
            return self.send_json(self.app.meta())
        if self.path == "/api/analysis":
            return self.send_json(self.app.job_status())
        return super().do_GET()

    def do_POST(self):
        routes = {"/api/landmarks": self.app.landmarks, "/api/run": self.app.run, "/api/compare": self.app.compare,
                  "/api/heuristic": self.app.heuristic_table, "/api/analysis": self.app.start_analysis}
        if self.path not in routes:
            return self.send_json(dict(error="없는 주소입니다."), 404)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            self.send_json(routes[self.path](body))
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
