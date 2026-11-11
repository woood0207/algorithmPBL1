"""엔진 회귀 테스트. 실행: python -m unittest -v test_engine"""
import math
import random
import unittest
from pathlib import Path

from graph_data import distances, load_graph
from landmarks import ALT, METHODS, pick_landmarks
from search import ALGORITHMS, a_star, run_search
from trace_io import load_trace, save_trace, trace_from_dict, trace_to_dict

HERE = Path(__file__).parent
DATA = HERE / "data"


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = load_graph(DATA / "road_nodes.csv", DATA / "road_edges.csv", DATA / "roads.graphml")
        cls.alt = ALT(cls.g, pick_landmarks(cls.g, 6))
        rng = random.Random(7)
        cls.pairs = [tuple(rng.sample(cls.g.main_nodes, 2)) for _ in range(60)]

    def assert_valid(self, t):
        self.assertEqual(t.path[0], t.start)
        self.assertEqual(t.path[-1], t.goal)
        self.assertEqual(len(t.edge_ids), len(t.path) - 1)
        for i, e in enumerate(t.edge_ids):
            self.assertEqual((self.g.roads[e].u, self.g.roads[e].v), (t.path[i], t.path[i + 1]))


class TestGraph(Base):
    def test_load(self):
        self.assertEqual(self.g.stats["nodes"], 746)
        self.assertEqual(self.g.stats["csv_rows"], 1976)
        self.assertGreater(self.g.stats["curved_roads"], 900)
        self.assertEqual(self.g.network_type, "walk")

    def test_haversine_admissible(self):
        self.assertGreaterEqual(self.g.alpha, 1.0)


class TestExact(Base):
    def test_exact_algorithms_match_dijkstra(self):
        for s, t in self.pairs:
            d = distances(self.g, s).get(t)
            for algo, h in [("dijkstra", None), ("astar", "haversine"), ("astar", "geo"), ("astar", "alt"),
                            ("astar", "max"), ("bidirectional", None)]:
                r = run_search(self.g, algo, s, t, heuristic=h, alt=self.alt)
                self.assertEqual(r.found, d is not None)
                if d is not None:
                    self.assertAlmostEqual(r.cost, d, places=6, msg=f"{algo}/{h} {s}->{t}")
                    self.assert_valid(r)

    def test_weighted_bound(self):
        for s, t in self.pairs:
            d = distances(self.g, s)[t]
            for w in (1.5, 3, 5):
                r = run_search(self.g, "weighted", s, t, weight=w, heuristic="haversine")
                self.assertLessEqual(r.cost, w * d + 1e-6)
                self.assert_valid(r)

    def test_alt_is_lower_bound(self):
        for _, t in self.pairs[:10]:
            true = distances(self.g, t, reverse=True)
            for v, d in true.items():
                self.assertLessEqual(self.alt.bound(v, t)[0], d + 1e-6)


class TestOthers(Base):
    def test_every_algorithm_runs(self):
        s, t = self.g.default_endpoints()
        for algo in ALGORITHMS:
            r = run_search(self.g, algo, s, t, alt=self.alt, waypoints=[100, 200, 300], budget=500)
            self.assertGreater(r.expanded, 0, algo)
            self.assertIn(r.found, (True, False))
            if r.found:
                self.assert_valid(r)

    def test_failure_has_null_cost(self):
        s, t = self.g.default_endpoints()
        r = run_search(self.g, "random", s, t, budget=3, seed=1)
        self.assertFalse(r.found)
        self.assertIsNone(r.cost)
        self.assertEqual(r.reason, "budget")

    def test_unreachable(self):
        lonely = next(n for n in self.g.coords if n not in set(self.g.main_nodes))
        s = self.g.main_nodes[0]
        for algo in ("dijkstra", "astar", "bfs", "bidirectional", "greedy"):
            r = run_search(self.g, algo, s, lonely)
            self.assertFalse(r.found, algo)
            self.assertEqual(r.reason, "unreachable")

    def test_start_equals_goal(self):
        s = self.g.main_nodes[0]
        for algo in ("dijkstra", "astar", "bfs", "bidirectional", "greedy", "beam", "local", "random"):
            r = run_search(self.g, algo, s, s, alt=self.alt)
            self.assertTrue(r.found, algo)
            self.assertEqual(r.cost, 0, algo)

    def test_via_matches_two_dijkstras(self):
        for s, t in self.pairs[:20]:
            cands = random.Random(s).sample(self.g.main_nodes, 4)
            r = run_search(self.g, "via", s, t, waypoints=cands)
            ds, dt = distances(self.g, s), distances(self.g, t, reverse=True)
            best = min(ds[c] + dt[c] for c in cands if c in ds and c in dt)
            self.assertAlmostEqual(r.cost, best, places=6)
            self.assert_valid(r)
            self.assertIn(r.extra["via"], r.path)

    def test_vehicle_switch_never_slower_than_walking(self):
        for s, t in self.pairs[:30]:
            r = run_search(self.g, "vehicle_switch", s, t, drive_radius=500)
            d = distances(self.g, s)[t] / 1000 / 5 * 60
            self.assertLessEqual(r.cost, d + 1e-6)
            self.assert_valid(r)
            kinds = [x["kind"] for x in r.segments]
            self.assertEqual(kinds, sorted(kinds, key=["walk1", "drive", "walk2"].index))
            self.assertEqual(kinds.count("drive"), min(1, kinds.count("drive")))
            self.assertAlmostEqual(sum(x["minutes"] for x in r.segments), r.cost, places=6)

    def test_drive_only_inside_zone(self):
        s, t = self.g.default_endpoints()
        r = run_search(self.g, "vehicle_switch", s, t, drive_radius=400)
        zone = set(r.extra["drive_nodes"])
        for seg in r.segments:
            if seg["kind"] == "drive":
                for e in seg["edge_ids"]:
                    self.assertTrue(self.g.roads[e].u in zone and self.g.roads[e].v in zone)

    def test_landmark_methods(self):
        for m in METHODS:
            self.assertEqual(len(set(pick_landmarks(self.g, 5, m, seed=3))), 5)

    def test_generic_a_star(self):
        graph = {"A": [("B", 1), ("C", 4)], "B": [("C", 2), ("D", 5)], "C": [("D", 1)], "D": []}
        self.assertEqual(a_star(graph, "A", "D", lambda n, g: 0), (["A", "B", "C", "D"], 4))


class TestTrace(Base):
    def test_round_trip(self):
        s, t = self.g.default_endpoints()
        r = run_search(self.g, "bidirectional", s, t)
        back = trace_from_dict(trace_to_dict(r))
        self.assertEqual(back.path, r.path)
        self.assertEqual(back.steps[3], r.steps[3])

    def test_reads_original_format(self):
        path = HERE / "output" / "_old_format.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text('{"start":1,"goal":2,"weight":1.0,"steps":[{"order":0,"node":1,"g":0,"h":1,"f":1,'
                        '"parent":null,"pushed":[2]}],"path":[1,2],"cost":3.0,"elapsed_ms":0.1}')
        t = load_trace(path)
        path.unlink()
        self.assertTrue(t.found)
        self.assertEqual(t.expanded, 1)

    def test_save(self):
        s, t = self.g.default_endpoints()
        r = run_search(self.g, "astar", s, t)
        path = HERE / "output" / "_t.json"
        save_trace(r, path)
        self.assertEqual(load_trace(path).cost, r.cost)
        path.unlink()


if __name__ == "__main__":
    unittest.main()
