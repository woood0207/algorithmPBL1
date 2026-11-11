"""Serve the offline map and Python routing API on this computer only."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit
import webbrowser

if __package__:
    from .routing import RoadGraph
else:
    from routing import RoadGraph

ROOT = Path(__file__).resolve().parent


class MapHandler(SimpleHTTPRequestHandler):
    extensions_map = dict(SimpleHTTPRequestHandler.extensions_map, **{'.js': 'text/javascript; charset=utf-8'})

    def _json(self, status, payload):
        data = json.dumps(payload, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def _local_request(self):
        port = self.server.server_port
        allowed = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if self.headers.get('Host') not in allowed:
            self._json(403, {'error': 'Local host required'})
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://{host}' for host in allowed}:
            self._json(403, {'error': 'Same-origin requests required'})
            return False
        return True

    def do_GET(self):
        if not self._local_request():
            return
        if urlsplit(self.path).path == '/api/health':
            graph = self.server.graph
            self._json(200, dict(backend='python', algorithm='dijkstra', nodes=len(graph.nodes),
                                 edges=len(graph.targets), graph_format=graph.source_format,
                                 modes=['mixed', 'walk', 'bike', 'drive']))
        elif urlsplit(self.path).path.startswith('/api/'):
            self._json(404, {'error': 'Unknown API endpoint'})
        else:
            super().do_GET()

    def do_POST(self):
        if not self._local_request():
            return
        if urlsplit(self.path).path != '/api/route':
            self._json(404, {'error': 'Unknown API endpoint'})
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 16384:
                raise ValueError('Request body must be between 1 and 16384 bytes')
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict) or 'start' not in payload or 'end' not in payload:
                raise ValueError('A start point and an end point are required')
            result = self.server.graph.route(payload['start'], payload['end'], payload.get('mode', 'mixed'))
        except (ValueError, TypeError, UnicodeDecodeError) as error:
            self._json(400, {'error': str(error)})
            return
        try:
            self._json(200, result)
        except (BrokenPipeError, ConnectionResetError):
            pass  # The browser may cancel a superseded route request.


def create_server(port=8765, graph=None, *, nodes_path=None, edges_path=None):
    server = ThreadingHTTPServer(('127.0.0.1', port), partial(MapHandler, directory=str(ROOT)))
    try:
        server.graph = graph if graph is not None else RoadGraph.load(nodes_path=nodes_path, edges_path=edges_path)
    except Exception:
        server.server_close()
        raise
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--nodes', type=Path, help='Node CSV; defaults to road_nodes.csv beside server.py')
    parser.add_argument('--edges', type=Path, help='Edge CSV; defaults to road_edges.csv beside server.py')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('Port must be between 0 and 65535')
    print('Loading the local CSV routing graph…', flush=True)
    try:
        server = create_server(args.port, nodes_path=args.nodes, edges_path=args.edges)
    except OSError as error:
        parser.exit(1, f'Unable to start the server: {error}\nTry a different port with --port 8766.\n')
    url = f'http://127.0.0.1:{server.server_port}/index.html'
    print(f'Python routing ready: {url}\nKeep this terminal open. Press Ctrl+C to stop.', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
