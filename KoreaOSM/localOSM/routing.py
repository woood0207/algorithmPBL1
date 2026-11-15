"""Standalone offline shortest-distance routing using only Python's standard library.

Import RoadGraph or run:
  python routing.py --start 36.0122587 129.3218056 --end 36.0035966 129.3193556 --mode mixed --output result.json
"""
from array import array
import argparse
import csv
import heapq
import json
import math
from pathlib import Path

MODES = {'walk': 1, 'bike': 2, 'drive': 4, 'mixed': 7}
MODE_NAMES = ('walk', 'bike', 'drive')


def mode_mask(value):
    if isinstance(value, bool):
        raise ValueError('Invalid transport mode')
    if isinstance(value, str):
        value = {'walking': 'walk', 'bicycle': 'bike', 'car': 'drive'}.get(value.lower(), value.lower())
        value = MODES.get(value, value)
        if isinstance(value, str) and value in {'1', '2', '4', '7'}:
            value = int(value)
    if value not in (1, 2, 4, 7):
        raise ValueError('Mode must be mixed, walk, bike or drive')
    return int(value)


def coordinate(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError('Coordinates must be [latitude, longitude]')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        raise ValueError('Coordinates must contain finite numbers')
    lat, lon = value
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError('Coordinate is outside the valid latitude/longitude range')
    return [float(lat), float(lon)]


def distance_m(a, b):
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 12742018 * math.asin(min(1, math.sqrt(h)))


def empty_result():
    return dict(reachable=False, distance_m=None, path=[], coordinates=[], segments=[], legs=[],
                distance_by_mode_m=dict.fromkeys(MODE_NAMES, 0.0), mode_changes=0)


class RoadGraph:
    """CSR graph. Route calls keep all search state local to the call."""

    def __init__(self, data):
        self.nodes = [coordinate(p) for p in data['nodes']]
        n = len(self.nodes)
        self.node_ids = list(data.get('node_ids', range(n)))
        if len(self.node_ids) != n or any(type(i) is not int for i in self.node_ids) or len(set(self.node_ids)) != n:
            raise ValueError('Node IDs must be unique integers')
        self.source_format = data.get('source_format', 'memory')
        self.offsets = array('I', [0]) * (n + 1)
        self.incident = bytearray(n)
        edges = data['edges']
        for edge in edges:
            if not isinstance(edge, (list, tuple)) or len(edge) != 4:
                raise ValueError('Each edge must contain source, target, distance and mode mask')
            u, v, length, mask = edge
            if (type(u) is not int or type(v) is not int or not 0 <= u < n or not 0 <= v < n
                    or isinstance(length, bool) or not isinstance(length, (int, float))
                    or not math.isfinite(length) or length < 0 or type(mask) is not int or not 1 <= mask <= 7):
                raise ValueError('Invalid routing edge')
            self.offsets[u + 1] += 1
            self.incident[u] |= mask
            self.incident[v] |= mask
        for i in range(1, n + 1):
            self.offsets[i] += self.offsets[i - 1]
        self.targets = array('I', [0]) * len(edges)
        self.lengths = array('d', [0]) * len(edges)
        self.masks = bytearray(len(edges))
        positions = self.offsets[:]
        for u, v, length, mask in edges:
            i = positions[u]
            positions[u] += 1
            self.targets[i], self.lengths[i], self.masks[i] = v, length, mask

    @classmethod
    def load(cls, path=None, *, nodes_path=None, edges_path=None):
        """Default to road_nodes.csv and road_edges.csv beside this module.

        Explicit JSON/JS paths remain supported for importing older graphs.
        A directory or node CSV path can also be passed as `path`.
        """
        if path is not None and (nodes_path is not None or edges_path is not None):
            raise ValueError('Use either a graph path or CSV paths, not both')
        if path is None:
            base = Path(nodes_path or edges_path).parent if nodes_path or edges_path else Path(__file__).parent
            return cls.from_csv(nodes_path or base / 'road_nodes.csv', edges_path or base / 'road_edges.csv')
        path = Path(path)
        if path.is_dir():
            return cls.from_csv(path / 'road_nodes.csv', path / 'road_edges.csv')
        if path.suffix.lower() == '.csv':
            return cls.from_csv(path, path.with_name('road_edges.csv'))
        content = path.read_text(encoding='utf-8-sig').strip()
        prefix = 'window.LOCAL_ROUTE_DATA='
        source_format = 'json'
        if content.startswith(prefix):
            content = content[len(prefix):].removesuffix(';').strip()
            source_format = 'js'
        data = json.loads(content)
        data['source_format'] = source_format
        return cls(data)

    @classmethod
    def from_csv(cls, nodes_path, edges_path):
        """Read nodes id/latitude/longitude and directed, mode-filtered edges.

        Edges use length_m or a meter-valued weight, and mode_mask or mode.
        Arbitrary integer node IDs are mapped to contiguous internal indexes.
        """
        nodes, node_ids, indexes, edges = [], [], {}, []
        with Path(nodes_path).open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream)
            if not {'id', 'latitude', 'longitude'} <= set(reader.fieldnames or []):
                raise ValueError('Node CSV requires id,latitude,longitude columns')
            for row in reader:
                try:
                    node_id = int(row['id'])
                    if node_id in indexes:
                        raise ValueError(f'Duplicate node ID {node_id}')
                    point = coordinate([float(row['latitude']), float(row['longitude'])])
                    indexes[node_id] = len(nodes)
                    node_ids.append(node_id)
                    nodes.append(point)
                except (TypeError, ValueError) as error:
                    raise ValueError(f'Node CSV line {reader.line_num}: {error}') from error
        with Path(edges_path).open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream)
            fields = set(reader.fieldnames or [])
            if not {'source', 'target'} <= fields or not fields & {'length_m', 'weight'} or not fields & {'mode_mask', 'mode'}:
                raise ValueError('Edge CSV requires source,target, length_m or weight, and mode_mask or mode')
            for row in reader:
                try:
                    source_id, target_id = int(row['source']), int(row['target'])
                    if source_id not in indexes or target_id not in indexes:
                        raise ValueError(f'Unknown endpoint ID {source_id} or {target_id}')
                    if (row.get('length_m') or '').strip():
                        length = float(row['length_m'])
                    else:
                        if (row.get('weight_unit') or '').strip() not in {'', 'm'}:
                            raise ValueError('Distance routing requires a meter-valued weight or length_m')
                        length = float(row['weight'])
                    mask = int(row['mode_mask']) if (row.get('mode_mask') or '').strip() else mode_mask((row.get('mode') or '').strip())
                    if not math.isfinite(length) or length < 0 or not 1 <= mask <= 7:
                        raise ValueError('Invalid distance or mode mask')
                    edges.append([indexes[source_id], indexes[target_id], length, mask])
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError(f'Edge CSV line {reader.line_num}: {error}') from error
        return cls(dict(nodes=nodes, node_ids=node_ids, edges=edges, source_format='csv'))

    def export_csv(self, directory):
        """Export lossless distance weights, direction and allowed mode masks."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / 'road_nodes.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['id', 'latitude', 'longitude'])
            writer.writerows((node_id, *point) for node_id, point in zip(self.node_ids, self.nodes))
        with (directory / 'road_edges.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['source', 'target', 'weight', 'mode_mask', 'weight_unit'])
            for source in range(len(self.nodes)):
                for i in range(self.offsets[source], self.offsets[source + 1]):
                    writer.writerow([self.node_ids[source], self.node_ids[self.targets[i]], self.lengths[i], self.masks[i], 'm'])

    def nearest(self, point, mode='mixed', max_distance=500):
        point, mask = coordinate(point), mode_mask(mode)
        if isinstance(max_distance, bool) or not isinstance(max_distance, (int, float)) or not math.isfinite(max_distance) or max_distance < 0:
            raise ValueError('Maximum snap distance must be a finite non-negative number')
        best = None
        for i, position in enumerate(self.nodes):
            if self.incident[i] & mask:
                meters = distance_m(point, position)
                if meters <= max_distance and (best is None or meters < best['distance_m']):
                    best = dict(node=i, coordinate=position, distance_m=meters)
        return best

    def shortest_path(self, start, end, mode='mixed'):
        """Dijkstra over non-negative distance weights, preserving direction/modes."""
        mask = mode_mask(mode)
        n = len(self.nodes)
        if type(start) is not int or type(end) is not int or not 0 <= start < n or not 0 <= end < n:
            raise ValueError('Unknown endpoint node')
        costs = array('d', [math.inf]) * n
        previous = array('i', [-1]) * n
        previous_edge = array('i', [-1]) * n
        costs[start] = 0
        queue = [(0.0, start)]
        while queue:
            cost, node = heapq.heappop(queue)
            if cost != costs[node]:
                continue
            if node == end:
                path, edges = [], []
                cursor = end
                while cursor != -1:
                    path.append(cursor)
                    if previous_edge[cursor] != -1:
                        edges.append(previous_edge[cursor])
                    cursor = previous[cursor]
                path.reverse()
                edges.reverse()
                return dict(reachable=True, distance_m=cost, path=path,
                            coordinates=[self.nodes[i] for i in path], **self._describe_modes(path, edges, mask))
            for i in range(self.offsets[node], self.offsets[node + 1]):
                if self.masks[i] & mask:
                    target = self.targets[i]
                    candidate = cost + self.lengths[i]
                    if candidate < costs[target]:
                        costs[target], previous[target], previous_edge[target] = candidate, node, i
                        heapq.heappush(queue, (candidate, target))
        return empty_result()

    def _describe_modes(self, path, edges, allowed):
        # Minimize switches on the chosen edge path; distance is the route objective.
        parents = []
        costs = [0, 0, 0]
        for step, edge in enumerate(edges):
            next_costs, row = [math.inf] * 3, [-1] * 3
            for mode, bit in enumerate((1, 2, 4)):
                if self.masks[edge] & allowed & bit:
                    if not step:
                        next_costs[mode] = 0
                    else:
                        before = min(range(3), key=lambda j: costs[j] + (j != mode))
                        next_costs[mode] = costs[before] + (before != mode)
                        row[mode] = before
            parents.append(row)
            costs = next_costs
        chosen = [0] * len(edges)
        last = min(range(3), key=lambda j: costs[j])
        for i in range(len(edges) - 1, -1, -1):
            chosen[i], last = last, parents[i][last]
        segments, legs = [], []
        totals = dict.fromkeys(MODE_NAMES, 0.0)
        for step, edge in enumerate(edges):
            name, bit, length = MODE_NAMES[chosen[step]], (1, 2, 4)[chosen[step]], self.lengths[edge]
            segments.append(dict(source=path[step], target=path[step + 1], mode=name, mode_bit=bit, distance_m=length))
            totals[name] += length
            if not legs or legs[-1]['mode'] != name:
                legs.append(dict(mode=name, distance_m=0.0, coordinates=[self.nodes[path[step]]]))
            legs[-1]['distance_m'] += length
            legs[-1]['coordinates'].append(self.nodes[path[step + 1]])
        return dict(segments=segments, legs=legs, distance_by_mode_m=totals, mode_changes=max(0, len(legs) - 1))

    def route(self, start, end, mode='mixed', max_snap_m=500):
        """Snap geographic endpoints and return a JSON-compatible route result."""
        mask = mode_mask(mode)
        start, end = coordinate(start), coordinate(end)
        source = self.nearest(start, mask, max_snap_m)
        target = self.nearest(end, mask, max_snap_m)
        result = self.shortest_path(source['node'], target['node'], mask) if source and target else empty_result()
        result.update(start=source, end=target, mode=mask, backend='python', algorithm='dijkstra')
        if source is None or target is None:
            result['error'] = 'snap_failed'
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--graph', type=Path, help='Explicit legacy JS/JSON graph, CSV directory or node CSV')
    parser.add_argument('--nodes', type=Path, help='Node CSV (default: road_nodes.csv beside this module)')
    parser.add_argument('--edges', type=Path, help='Edge CSV (default: road_edges.csv beside this module)')
    parser.add_argument('--export-csv', type=Path, help='Export the loaded graph to this directory')
    parser.add_argument('--start', type=float, nargs=2, metavar=('LAT', 'LON'))
    parser.add_argument('--end', type=float, nargs=2, metavar=('LAT', 'LON'))
    parser.add_argument('--mode', choices=MODES, default='mixed')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.graph is not None and (args.nodes is not None or args.edges is not None):
        parser.error('--graph cannot be combined with --nodes or --edges')
    if bool(args.start) != bool(args.end) or (not args.start and not args.export_csv):
        parser.error('Specify both --start and --end, or use --export-csv')
    graph = RoadGraph.load(args.graph, nodes_path=args.nodes, edges_path=args.edges)
    if args.export_csv:
        graph.export_csv(args.export_csv)
        print(f'CSV graph saved to {args.export_csv.resolve()}')
    if not args.start:
        return
    result = graph.route(args.start, args.end, args.mode)
    content = json.dumps(result, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(content, encoding='utf-8')
        print(f'Route result saved to {args.output.resolve()}')
    else:
        print(content)


if __name__ == '__main__':
    main()
