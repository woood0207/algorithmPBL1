"""Serve the offline map and Python routing API on this computer only."""
import argparse
import gzip
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser

if __package__:
    from .routing import RoadGraph
    from .nationwide import DEFAULT_DATA, NationalGraph
    from .transit import DEFAULT_TRANSIT, TransitStore
    from .multimodal import MultiModalRouter, TRANSIT_MODES
    from .english import EnglishPresentation
    from .console_english import error_text
else:
    from routing import RoadGraph
    from nationwide import DEFAULT_DATA, NationalGraph
    from transit import DEFAULT_TRANSIT, TransitStore
    from multimodal import MultiModalRouter, TRANSIT_MODES
    from english import EnglishPresentation
    from console_english import error_text

ROOT = Path(__file__).resolve().parent


class MapHandler(SimpleHTTPRequestHandler):
    extensions_map = dict(SimpleHTTPRequestHandler.extensions_map, **{'.js': 'text/javascript; charset=utf-8'})

    def end_headers(self):
        if not urlsplit(self.path).path.startswith('/api/'):
            self.send_header('Cache-Control','no-store')
        super().end_headers()

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # A canceled browser request is a normal client disconnect.
            pass

    def _json(self, status, payload):
        payload = self.server.english.payload(payload)
        data = json.dumps(payload, allow_nan=False, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        compressed = len(data) > 16384 and 'gzip' in self.headers.get('Accept-Encoding', '')
        if compressed:
            data = gzip.compress(data, compresslevel=1)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        if compressed:
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Vary', 'Accept-Encoding')
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
        path = urlsplit(self.path).path
        graph = self.server.graph
        if path.startswith('/api/transit/'):
            query=parse_qs(urlsplit(self.path).query)
            try:
                if path=='/api/transit/status':
                    result=self.server.transit.status()
                elif path=='/api/transit/stops':
                    matches={};truncated=False
                    for term in self.server.english.search_aliases(query.get('q',[''])[0]):
                        found=self.server.transit.stops(term,query.get('kind',['all'])[0])
                        for stop in found['stops']:matches.setdefault(stop['id'],stop)
                        truncated=truncated or found['truncated']
                    result=dict(stops=list(matches.values())[:30],truncated=truncated or len(matches)>30)
                elif path=='/api/transit/map':
                    bounds=[float(x) for x in query.get('bbox',[''])[0].split(',')]
                    if len(bounds)!=4:
                        raise ValueError('bbox requires west,south,east,north')
                    result=self.server.transit.map_data(bounds,float(query.get('zoom',['8'])[0]))
                else:
                    self._json(404,{'error':'Unknown transit endpoint'})
                    return
                self._json(200,result)
            except (ValueError,TypeError) as error:
                self._json(400,{'error':str(error)})
        elif path == '/api/health':
            graph = self.server.graph
            self._json(200, dict(backend='python', algorithm='bidirectional_dijkstra' if isinstance(graph, NationalGraph) else 'dijkstra', nodes=len(graph.nodes),
                                 edges=len(graph.targets), graph_format=graph.source_format,
                                 objectives=graph.metadata['objectives'] if isinstance(graph,NationalGraph) else ['distance'],
                                 modes=['mixed', 'walk', 'bike', 'drive']+(list(TRANSIT_MODES) if isinstance(graph,NationalGraph) and self.server.transit.available else [])))
        elif path in {'/api/map', '/api/features', '/api/search'} and isinstance(graph, NationalGraph):
            query = parse_qs(urlsplit(self.path).query)
            try:
                if path == '/api/map':
                    result = graph.metadata
                elif path == '/api/features':
                    bounds = [float(x) for x in query.get('bbox', [''])[0].split(',')]
                    if len(bounds) != 4:
                        raise ValueError('bbox requires west,south,east,north')
                    result = graph.features(bounds, float(query.get('zoom', ['8'])[0]))
                else:
                    matches={}
                    for term in self.server.english.search_aliases(query.get('q',[''])[0]):
                        for place in graph.search_places(term):
                            matches.setdefault((place['name'],tuple(place['coordinate'])),place)
                    result=dict(results=list(matches.values())[:20])
                self._json(200, result)
            except (ValueError, TypeError) as error:
                self._json(400, {'error': str(error)})
        elif path.startswith('/api/'):
            self._json(404, {'error': 'Unknown API endpoint'})
        else:
            if path in {'/', '/index.html'} and isinstance(graph, NationalGraph):
                self.path = '/national.html'
            super().do_GET()

    def do_POST(self):
        if not self._local_request():
            return
        path=urlsplit(self.path).path
        if path not in {'/api/route','/api/transit/route'}:
            self._json(404, {'error': 'Unknown API endpoint'})
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 16384:
                raise ValueError('Request body must be between 1 and 16384 bytes')
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict) or 'start' not in payload or 'end' not in payload:
                raise ValueError('A start point and an end point are required')
        except (ValueError, TypeError, UnicodeDecodeError) as error:
            self._json(400, {'error': str(error)})
            return
        if path=='/api/transit/route':
            try:
                result=self.server.transit.plan(payload['start'],payload['end'],payload.get('departure'),
                    min_transfer_s=payload.get('min_transfer_s',600),boarding_s=payload.get('boarding_s',300),
                    modes=payload.get('modes','all'))
                self._json(200,result)
            except (ValueError,TypeError) as error:
                self._json(400,{'error':str(error)})
            except (BrokenPipeError,ConnectionResetError):
                pass
            return
        # Bound peak memory when several tabs submit long national routes.
        if not self.server.route_lock.acquire(blocking=False):
            self._json(429, {'error': '다른 경로를 계산 중입니다. 잠시 후 다시 탐색해 주세요.'})
            return
        try:
            if payload.get('mode') in TRANSIT_MODES:
                if not isinstance(self.server.graph,NationalGraph):
                    raise ValueError('Door-to-door transit routing requires the national timed road graph')
                result=self.server.door_router.route(payload['start'],payload['end'],payload['mode'],payload.get('departure'),
                    access_mode=payload.get('access_mode','walk'),candidate_count=payload.get('candidate_count',1),
                    min_transfer_s=payload.get('min_transfer_s',600),boarding_s=payload.get('boarding_s',300),
                    objective=payload.get('objective','time'),stop_policy=payload.get('stop_policy','served'),
                    routing_model=payload.get('routing_model','network'),rail_speed_kmh=payload.get('rail_speed_kmh',100))
            elif isinstance(self.server.graph,NationalGraph):
                result = self.server.graph.route(payload['start'],payload['end'],payload.get('mode','mixed'),objective=payload.get('objective','distance'))
            else:
                if payload.get('objective','distance') != 'distance':
                    raise ValueError('Time routing requires a national graph with speed data')
                result = self.server.graph.route(payload['start'], payload['end'], payload.get('mode', 'mixed'))
        except (ValueError, TypeError) as error:
            self._json(400, {'error': str(error)})
            return
        finally:
            self.server.route_lock.release()
        try:
            self._json(200, result)
        except (BrokenPipeError, ConnectionResetError):
            pass  # The browser may cancel a superseded route request.


def create_server(port=8765, graph=None, *, nodes_path=None, edges_path=None, national_dir=None, speed_dir=None, transit_dir=None, local=False):
    server = ThreadingHTTPServer(('127.0.0.1', port), partial(MapHandler, directory=str(ROOT)))
    server.english=EnglishPresentation()
    try:
        if graph is not None:
            server.graph = graph
        elif national_dir is not None or (not local and nodes_path is None and edges_path is None and (DEFAULT_DATA / 'manifest.json').exists()):
            server.graph = NationalGraph(national_dir or DEFAULT_DATA,speed_directory=speed_dir)
        else:
            server.graph = RoadGraph.load(nodes_path=nodes_path, edges_path=edges_path)
        server.route_lock = threading.Lock()
        server.transit=TransitStore(transit_dir or DEFAULT_TRANSIT)
        server.door_router=MultiModalRouter(server.graph,server.transit)
    except Exception:
        server.server_close()
        raise
    server.daemon_threads = True
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--national-dir', type=Path, help='National offline dataset (auto-detected by default)')
    parser.add_argument('--speed-dir', type=Path, help='Optional speed dataset override')
    parser.add_argument('--transit-dir',type=Path,help='Optional offline transit dataset override')
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error('Port must be between 0 and 65535')
    print('Loading offline routing data...', flush=True)
    try:
        if __package__:
            from .dataset_bundle import ensure_data
        else:
            from dataset_bundle import ensure_data
        ensure_data()
        server = create_server(args.port,national_dir=args.national_dir or DEFAULT_DATA,
                               speed_dir=args.speed_dir,transit_dir=args.transit_dir)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Unable to start the server: {error_text(error)}\nCheck the data folders, or use --port 8766 if the default port is busy.\n')
    page='national.html?lang=en&ui=english-20261116-final' if isinstance(server.graph,NationalGraph) else 'index.html'
    url = f'http://127.0.0.1:{server.server_port}/{page}'
    print(f'Python routing ready: {url}\nKeep this terminal open. Press Ctrl+C to stop.', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nMap server stopped.',flush=True)
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
