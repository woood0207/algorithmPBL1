"""Offline tests for the TAGO bus API parser and time-dependent transit search."""
import json
import os
import unittest
from datetime import time as clock_time
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import bus_api
import server
from bus_api import _items, estimate_bus_arrivals, fetch_bus_feed, load_env, parse_response
from graph_data import Graph, Road, Vehicle, haversine
from search import run_search
from server import App

ROOT = Path(__file__).parent


class FakeResponse:
    status = 200
    headers = {"Content-Type": "application/json; charset=utf-8"}

    def __init__(self, data):
        self.payload = json.dumps(data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


class TestBusAPI(unittest.TestCase):
    def test_root_env_loads_key_without_printing_it(self):
        previous = os.environ.pop("TAGO_SERVICE_KEY", None)
        try:
            load_env(ROOT / ".env")
            expected = next(line.split("=", 1)[1] for line in (ROOT / ".env").read_text().splitlines()
                            if line.startswith("TAGO_SERVICE_KEY="))
            self.assertEqual(os.environ.get("TAGO_SERVICE_KEY"), expected)
        finally:
            os.environ.pop("TAGO_SERVICE_KEY", None)
            if previous is not None:
                os.environ["TAGO_SERVICE_KEY"] = previous

    def test_discovers_tago_routes_and_parses_response_items(self):
        calls = []

        def opener(request, timeout):
            parsed = urlparse(request.full_url)
            params = parse_qs(parsed.query)
            endpoint = parsed.path
            calls.append((endpoint, params))
            if endpoint.endswith("/getRouteNoList"):
                number = params["routeNo"][0]
                items = [{"routeid": f"PHB-{number}", "routeno": number, "routenm": f"{number}번"}]
            elif endpoint.endswith("/getRouteAcctoThrghSttnList"):
                route_id = params["routeId"][0]
                items = [{"nodeid": f"{route_id}-s{sequence}", "nodenm": "시청",
                          "nodeord": str(sequence), "gpslati": f"36.0{sequence}",
                          "gpslong": "129.35"} for sequence in (1, 2, 3)]
            else:
                items = [{"vehicleno": "bus-1", "nodeord": "2", "gpslati": "36.021",
                          "gpslong": "129.351", "routeid": params["routeId"][0]}]
            return FakeResponse({"response": {"header": {"resultCode": "00"},
                                               "body": {"items": {"item": items}}}})

        feed = fetch_bus_feed(service_key="test-key", base_url="https://example.test/1613000", opener=opener,
                              current_time=clock_time(12, 0))
        self.assertEqual([route["route_no"] for route in feed["routes"]], ["207", "306"])
        self.assertEqual([route["route_id"] for route in feed["routes"]], ["PHB-207", "PHB-306"])
        self.assertEqual(len(feed["stops"]), 6)
        self.assertEqual(len(feed["buses"]), 2)
        self.assertFalse(feed["service_ended"])
        self.assertEqual([stop["stop_id"] for stop in feed["stops"]],
                         ["PHB-207-s1", "PHB-207-s2", "PHB-207-s3",
                          "PHB-306-s1", "PHB-306-s2", "PHB-306-s3"])
        self.assertEqual([bus["sequence"] for bus in feed["buses"]], [2.0, 2.0])
        self.assertEqual([bus["route_no"] for bus in feed["buses"]], ["207", "306"])
        endpoints = [call[0].rsplit("/", 1)[-1] for call in calls]
        self.assertEqual(endpoints, [
            "getRouteNoList", "getRouteNoList",
            "getRouteAcctoThrghSttnList", "getRouteAcctoBusLcList",
            "getRouteAcctoThrghSttnList", "getRouteAcctoBusLcList",
        ])
        self.assertTrue(all(call[1]["serviceKey"] == ["test-key"] for call in calls))
        self.assertTrue(all(call[1]["cityCode"] == ["37010"] for call in calls))
        self.assertTrue(all(call[1]["_type"] == ["json"] for call in calls))
        route_queries = [call[1]["routeNo"][0] for call in calls
                         if call[0].endswith("/getRouteNoList")]
        self.assertEqual(route_queries, ["207", "306"])
        detail_route_ids = [call[1]["routeId"][0] for call in calls
                            if not call[0].endswith("/getRouteNoList")]
        self.assertEqual(detail_route_ids, ["PHB-207", "PHB-207", "PHB-306", "PHB-306"])
        self.assertEqual([call[1]["numOfRows"] for call in calls],
                         [["100"], ["100"], ["500"], ["100"], ["500"], ["100"]])
        xml = parse_response(b"<response><header><resultCode>00</resultCode></header></response>",
                             "application/xml")
        self.assertEqual(xml["response"]["header"]["resultCode"], "00")
        self.assertEqual(_items(xml), [])

    def test_api_normal_result_codes(self):
        for code in ("0", "00"):
            with self.subTest(code=code):
                response = {"response": {"header": {"resultCode": code},
                                         "body": {"items": {"item": []}}}}
                self.assertEqual(_items(response), [])

    def test_feed_marks_zero_active_vehicles_as_service_ended(self):
        def opener(request, timeout):
            endpoint = urlparse(request.full_url).path.rsplit("/", 1)[-1]
            params = parse_qs(urlparse(request.full_url).query)
            if endpoint == "getRouteNoList":
                number = params["routeNo"][0]
                rows = [{"routeid": f"PHB-{number}", "routeno": number}]
            elif endpoint == "getRouteAcctoThrghSttnList":
                rows = [{"nodeid": "stop", "nodenm": "시청", "nodeord": "1",
                         "gpslati": "36.02", "gpslong": "129.35"}]
            else:
                rows = []
            return FakeResponse({"response": {"header": {"resultCode": "00"},
                                               "body": {"items": {"item": rows}}}})

        feed = fetch_bus_feed(service_key="no-buses-key", base_url="https://empty.test/1613000",
                              opener=opener, current_time=clock_time(12, 0))
        self.assertEqual(feed["buses"], [])
        self.assertTrue(feed["service_ended"])
        self.assertEqual(feed["service_message"], "현재 운행 중인 버스가 없습니다 (운행 종료)")
        self.assertTrue(all(route["service_ended"] for route in feed["routes"]))

    def test_night_and_terminal_buses_are_filtered_with_simulation_override(self):
        def opener(request, timeout):
            endpoint = urlparse(request.full_url).path.rsplit("/", 1)[-1]
            params = parse_qs(urlparse(request.full_url).query)
            if endpoint == "getRouteNoList":
                number = params["routeNo"][0]
                rows = [{"routeid": f"PHB-{number}", "routeno": number}]
            elif endpoint == "getRouteAcctoThrghSttnList":
                rows = [{"nodeid": f"stop-{sequence}", "nodeord": str(sequence),
                         "gpslati": f"36.0{sequence}", "gpslong": "129.35"}
                        for sequence in (1, 2, 3)]
            else:
                rows = [{"vehicleno": "parked", "nodeord": "1", "gpslati": "36.01",
                         "gpslong": "129.35"},
                        {"vehicleno": "running", "nodeord": "2", "gpslati": "36.02",
                         "gpslong": "129.35"}]
            return FakeResponse({"response": {"header": {"resultCode": "00"},
                                               "body": {"items": {"item": rows}}}})

        args = {"service_key": "service-hours-key", "base_url": "https://hours.test/1613000",
                "opener": opener}
        daytime = fetch_bus_feed(**args, current_time=clock_time(12, 0))
        self.assertEqual([bus["bus_id"] for bus in daytime["buses"]], ["running", "running"])
        self.assertFalse(daytime["service_ended"])
        night = fetch_bus_feed(**args, current_time=clock_time(0, 30))
        self.assertEqual(night["buses"], [])
        self.assertTrue(night["service_ended"])
        demo = fetch_bus_feed(**args, current_time=clock_time(0, 30), force_service_active=True)
        self.assertEqual(len(demo["buses"]), 4)
        self.assertTrue(demo["simulation_override"])

    def test_bus_feed_force_refresh_bypasses_server_cache(self):
        app = App(Graph(), "test")
        current = {"fetched_at": __import__("time").time(), "version": 1}
        refreshed = {"fetched_at": __import__("time").time(), "version": 2}
        app.bus_cache = current
        with patch.object(server, "fetch_bus_feed", return_value=refreshed) as fetch:
            self.assertIs(app.bus_feed(), current)
            self.assertIs(app.bus_feed(force_refresh=True), refreshed)
        fetch.assert_called_once_with()

    def test_paginates_tago_route_results_and_caches_route_ids(self):
        calls = []
        bus_api._ROUTE_ID_CACHE.clear()

        def opener(request, timeout):
            parsed = urlparse(request.full_url)
            params = parse_qs(parsed.query)
            endpoint = parsed.path.rsplit("/", 1)[-1]
            calls.append((endpoint, params))
            if endpoint == "getRouteNoList":
                number = params["routeNo"][0]
                page = params["pageNo"][0]
                items = ([{"routeid": f"ignored-{number}", "routeno": "999"}] * 100
                         if page == "1" else
                         [{"routeid": f"PHB-{number}", "routeno": number}])
                total_count = 101
            elif endpoint == "getRouteAcctoThrghSttnList":
                items = [{"nodeid": f'{params["routeId"][0]}-stop',
                          "nodeord": "1", "gpslati": "36.02", "gpslong": "129.35"}]
                total_count = 1
            else:
                items, total_count = [], 0
            return FakeResponse({"response": {"header": {"resultCode": "00"},
                                               "body": {"totalCount": total_count,
                                                        "items": {"item": items}}}})

        api_base = "https://paging.test/1613000"
        first = fetch_bus_feed(service_key="pagination-key", base_url=api_base, opener=opener)
        second = fetch_bus_feed(service_key="pagination-key", base_url=api_base, opener=opener)
        route_calls = [params for endpoint, params in calls if endpoint == "getRouteNoList"]
        self.assertEqual([params["pageNo"] for params in route_calls], [["1"], ["2"], ["1"], ["2"]])
        self.assertTrue(all(params["numOfRows"] == ["100"] for params in route_calls))
        self.assertEqual([route["route_id"] for route in first["routes"]],
                         ["PHB-207", "PHB-306"])
        self.assertEqual([route["route_id"] for route in second["routes"]],
                         ["PHB-207", "PHB-306"])
        detail_calls = [(endpoint, params) for endpoint, params in calls
                        if endpoint in ("getRouteAcctoThrghSttnList", "getRouteAcctoBusLcList")]
        self.assertTrue(all(params["routeId"][0] in ("PHB-207", "PHB-306")
                            for _, params in detail_calls))
        self.assertEqual(len(detail_calls), 8)
        self.assertEqual(
            [params["numOfRows"][0] for endpoint, params in detail_calls
             if endpoint == "getRouteAcctoThrghSttnList"],
            ["500"] * 4,
        )
        self.assertEqual(
            [params["numOfRows"][0] for endpoint, params in detail_calls
             if endpoint == "getRouteAcctoBusLcList"],
            ["100"] * 4,
        )


class TestTimeDependentTransit(unittest.TestCase):
    def setUp(self):
        self.graph = Graph()
        self.graph.coords = {1: (36.0, 129.0), 2: (36.03, 129.0)}
        self.graph.adj = {1: {2: 5000.0}, 2: {}}
        self.graph.radj = {1: {}, 2: {1: 5000.0}}
        self.graph.edge_id = {(1, 2): 0}
        self.graph.roads = [Road(0, 1, 2, 5000.0, permissions={Vehicle.WALK: True}, length=5000.0)]
        self.stop_a = {"node_id": -1, "stop_id": "A", "sequence": 1,
                       "lat": 36.0, "lon": 129.001, "name": "A"}
        self.stop_b = {"node_id": -2, "stop_id": "B", "sequence": 2,
                       "lat": 36.03, "lon": 129.001, "name": "B"}

    def feed(self, bus_speed):
        return {
            "fetched_at": __import__("time").time(),
            "stops": [self.stop_a, self.stop_b],
            "routes": [{
                "route_id": "route-207", "route_no": "207",
                "stops": [self.stop_a, self.stop_b],
                "buses": ([{"route_id": "route-207", "bus_id": "bus-1",
                            "lat": 36.0015, "lon": 129.001, "speed_kmh": bus_speed}]
                          if bus_speed is not None else []),
            }],
        }

    def test_late_arrival_misses_live_bus_without_headway_fallback(self):
        feed = self.feed(25.0)
        route = feed["routes"][0]
        t_bus = haversine(36.0015, 129.001, self.stop_a["lat"], self.stop_a["lon"]) / (25 / 3.6)
        t_arrival = haversine(*self.graph.coords[1], self.stop_a["lat"], self.stop_a["lon"]) / (5 / 3.6)
        self.assertLess(t_bus, t_arrival)

        dijkstra = run_search(self.graph, "transit_dijkstra", 1, 2, bus_feed=feed)
        astar = run_search(self.graph, "transit_astar", 1, 2, bus_feed=feed)
        self.assertTrue(dijkstra.found)
        self.assertEqual(dijkstra.cost_unit, "초")
        self.assertEqual(dijkstra.extra["wait_s"], 0)
        self.assertTrue(dijkstra.extra["last_bus_missed"])
        self.assertFalse(dijkstra.extra["service_ended"])
        self.assertAlmostEqual(dijkstra.cost, astar.cost, places=6)
        self.assertNotIn(-2, dijkstra.path)
        self.assertEqual(dijkstra.extra["ride_distance_m"], 0)

    def test_future_live_bus_uses_remaining_arrival_time(self):
        feed = self.feed(3.0)
        route = feed["routes"][0]
        expected_arrival = haversine(36.0015, 129.001, self.stop_a["lat"], self.stop_a["lon"]) / (3 / 3.6)
        t_arrival = haversine(*self.graph.coords[1], self.stop_a["lat"], self.stop_a["lon"]) / (5 / 3.6)
        self.assertGreater(expected_arrival, t_arrival)
        trace = run_search(self.graph, "transit_dijkstra", 1, 2, bus_feed=feed)
        self.assertAlmostEqual(trace.extra["wait_s"], expected_arrival - t_arrival, delta=0.2)

    def test_without_live_vehicles_disables_bus_edges_and_marks_service_ended(self):
        trace = run_search(self.graph, "transit_dijkstra", 1, 2, bus_feed=self.feed(None))
        self.assertTrue(trace.found)
        self.assertEqual(trace.extra["wait_s"], 0)
        self.assertTrue(trace.extra["service_ended"])
        self.assertEqual(trace.extra["service_message"], "현재 운행 중인 버스가 없습니다 (운행 종료)")
        self.assertEqual(trace.extra["ride_distance_m"], 0)
        self.assertNotIn(-2, trace.path)

    def test_tago_bus_node_order_selects_route_progress_for_arrival_estimate(self):
        route = {
            "route_id": "route-207",
            "stops": [
                dict(self.stop_a, sequence=1),
                dict(self.stop_b, sequence=2),
            ],
        }
        bus = {
            "route_id": "route-207",
            "lat": self.stop_b["lat"],
            "lon": self.stop_b["lon"],
            "speed_kmh": 25.0,
            "sequence": 1.0,
        }
        arrivals = estimate_bus_arrivals(route, [bus])
        expected_first_stop = haversine(bus["lat"], bus["lon"], self.stop_a["lat"], self.stop_a["lon"]) / (25 / 3.6)
        self.assertAlmostEqual(arrivals[-1][0], expected_first_stop)
        self.assertGreater(arrivals[-2][0], arrivals[-1][0])

    def test_server_route_comparison_returns_walk_and_transit_breakdown(self):
        app = App(self.graph, "test")
        app.bus_feed = lambda: self.feed(3.0)
        result = app.route_comparison({"start": 1, "goal": 2, "options": {"walk_kmh": 5}})
        self.assertIsNotNone(result["walking"])
        self.assertIsNotNone(result["transit"])
        self.assertGreater(result["walking"]["distance_m"], 0)
        self.assertEqual(result["transit"]["buses"][0]["route_no"], "207")
        self.assertAlmostEqual(
            result["transit"]["time_s"],
            result["transit"]["walk_s"] + result["transit"]["wait_s"] + result["transit"]["ride_s"],
        )
        self.assertAlmostEqual(
            result["delta_s"],
            result["walking"]["time_s"] - result["transit"]["time_s"],
        )

    def test_server_keeps_walking_comparison_when_bus_api_fails(self):
        app = App(self.graph, "test")

        def fail_bus_feed():
            from bus_api import BusAPIError
            raise BusAPIError("테스트 API 오류")

        app.bus_feed = fail_bus_feed
        result = app.route_comparison({"start": 1, "goal": 2})
        self.assertIsNotNone(result["walking"])
        self.assertIsNone(result["transit"])
        self.assertEqual(result["transit_error"], "테스트 API 오류")

    def test_server_comparison_exposes_service_end_and_missed_last_bus(self):
        app = App(self.graph, "test")
        app.bus_feed = lambda: self.feed(None)
        ended = app.route_comparison({"start": 1, "goal": 2})
        self.assertTrue(ended["service_ended"])
        self.assertTrue(ended["transit"]["service_ended"])
        self.assertEqual(ended["transit"]["ride_distance_m"], 0)

        app.bus_feed = lambda: self.feed(25.0)
        missed = app.route_comparison({"start": 1, "goal": 2})
        self.assertFalse(missed["service_ended"])
        self.assertTrue(missed["last_bus_missed"])
        self.assertEqual(missed["transit"]["ride_distance_m"], 0)


if __name__ == "__main__":
    unittest.main()
