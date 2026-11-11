import json
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

if __package__:
    from .routing import RoadGraph
    from .server import create_server
else:
    from routing import RoadGraph
    from server import create_server


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = RoadGraph(dict(nodes=[[36, 129], [36.001, 129], [36.002, 129]],
                                  edges=[[0, 1, 10, 1], [1, 2, 20, 4]]))
        cls.server = create_server(0, cls.graph)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def post(self, payload, headers=None):
        return urlopen(Request(self.url + '/api/route', json.dumps(payload).encode(),
                               headers={'Content-Type': 'application/json', **(headers or {})}), timeout=10)

    def test_health_static_map_and_python_route(self):
        with urlopen(self.url + '/api/health', timeout=10) as response:
            self.assertEqual(json.load(response)['backend'], 'python')
        with urlopen(self.url + '/index.html', timeout=10) as response:
            html = response.read().decode()
            self.assertIn("fetch('/api/route'", html)
            self.assertNotIn('src="assets/local_router.js"', html)
            self.assertNotIn('src="route_data.js"', html)
        with self.post(dict(start=[36, 129], end=[36.002, 129], mode='mixed')) as response:
            result = json.load(response)
        self.assertEqual(result['backend'], 'python')
        self.assertEqual(result['distance_m'], 30)
        self.assertEqual([leg['mode'] for leg in result['legs']], ['walk', 'drive'])

    def test_invalid_input_and_cross_origin_requests(self):
        for payload in ({}, [], dict(start=[91, 129], end=[36, 129]),
                        dict(start=[36, 129], end=[36, 129], mode='boat')):
            with self.subTest(payload=payload), self.assertRaises(HTTPError) as caught:
                self.post(payload)
            self.assertEqual(caught.exception.code, 400)
            caught.exception.close()
        with self.assertRaises(HTTPError) as caught:
            self.post(dict(start=[36, 129], end=[36, 129]), {'Origin': 'https://example.com'})
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()


if __name__ == '__main__':
    unittest.main()
