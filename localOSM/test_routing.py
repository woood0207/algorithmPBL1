import json
from pathlib import Path
import random
import tempfile
import unittest

if __package__:
    from .routing import RoadGraph
else:
    from routing import RoadGraph


class RoutingTests(unittest.TestCase):
    def test_csv_roundtrip_and_nonsequential_ids(self):
        original = RoadGraph(dict(nodes=[[36, 129], [36.001, 129], [36.002, 129]],
                                  node_ids=[100, 500, 900], edges=[[0, 1, 10, 3], [1, 2, 20, 4], [1, 0, 10, 1]]))
        with tempfile.TemporaryDirectory() as directory:
            original.export_csv(directory)
            graph = RoadGraph.load(directory)
            self.assertEqual(graph.source_format, 'csv')
            self.assertEqual(graph.node_ids, [100, 500, 900])
            self.assertEqual(graph.nodes, original.nodes)
            self.assertEqual(graph.offsets, original.offsets)
            self.assertEqual(graph.targets, original.targets)
            self.assertEqual(graph.masks, original.masks)
            self.assertEqual(graph.lengths, original.lengths)
            for mode in ('mixed', 'walk', 'bike', 'drive'):
                for start in range(3):
                    for end in range(3):
                        self.assertEqual(graph.shortest_path(start, end, mode), original.shortest_path(start, end, mode))
            self.assertEqual(RoadGraph.load(nodes_path=Path(directory)/'road_nodes.csv').node_ids, graph.node_ids)

    def test_csv_mode_rows_and_time_weight_use_length_in_meters(self):
        with tempfile.TemporaryDirectory() as directory:
            nodes = Path(directory)/'road_nodes.csv'
            edges = Path(directory)/'road_edges.csv'
            nodes.write_text('id,latitude,longitude\n7,36,129\n8,36.001,129\n', encoding='utf-8-sig')
            edges.write_text('source,target,weight,mode,length_m,weight_unit\n7,8,5,walk,100,s\n8,7,10,drive,200,s\n', encoding='utf-8-sig')
            graph = RoadGraph.load(nodes_path=nodes, edges_path=edges)
            self.assertEqual(graph.shortest_path(0, 1, 'walk')['distance_m'], 100)
            self.assertEqual(graph.shortest_path(1, 0, 'drive')['distance_m'], 200)
            self.assertFalse(graph.shortest_path(1, 0, 'walk')['reachable'])
            bad_rows = [
                'source,target,weight,mode,weight_unit\n7,8,5,walk,s\n',
                'source,target,weight,mode_mask\n7,99,10,1\n',
                'source,target,weight,mode_mask\n7,8,-1,1\n',
                'source,target,weight,mode_mask\n7,8,10,8\n',
                'source,target,weight,mode_mask\n7,8,10\n',
            ]
            for content in bad_rows:
                edges.write_text(content, encoding='utf-8')
                with self.subTest(content=content), self.assertRaises(ValueError):
                    RoadGraph.load(nodes_path=nodes, edges_path=edges)
            nodes.write_text('id,latitude,longitude\n7,36,129\n7,36.001,129\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Duplicate node ID'):
                RoadGraph.load(nodes_path=nodes, edges_path=edges)

    def test_all_pairs_against_independent_floyd_warshall(self):
        randomizer = random.Random(73)
        data = dict(nodes=[[36 + i * .0001, 129] for i in range(25)], edges=[])
        for _ in range(140):
            u, v = randomizer.randrange(24), randomizer.randrange(24)
            if u != v:
                data['edges'].append([u, v, randomizer.randint(0, 100), randomizer.randint(1, 7)])
        graph = RoadGraph(data)
        for mask in (1, 2, 4, 7):
            expected = [[float('inf')] * 25 for _ in range(25)]
            for i in range(25):
                expected[i][i] = 0
            for u, v, length, modes in data['edges']:
                if modes & mask:
                    expected[u][v] = min(expected[u][v], length)
            for k in range(25):
                for u in range(25):
                    for v in range(25):
                        expected[u][v] = min(expected[u][v], expected[u][k] + expected[k][v])
            for start in range(25):
                for end in range(25):
                    result = graph.shortest_path(start, end, mask)
                    with self.subTest(mask=mask, start=start, end=end):
                        self.assertEqual(result['reachable'], expected[start][end] != float('inf'))
                        if not result['reachable']:
                            self.assertIsNone(result['distance_m'])
                            continue
                        self.assertEqual(result['distance_m'], expected[start][end])
                        self.assertEqual(result['path'][0], start)
                        self.assertEqual(result['path'][-1], end)
                        self.assertEqual(sum(result['distance_by_mode_m'].values()), result['distance_m'])
                        self.assertEqual(sum(leg['distance_m'] for leg in result['legs']), result['distance_m'])
                        for segment in result['segments']:
                            self.assertTrue(segment['mode_bit'] & mask)
                            self.assertTrue(any(u == segment['source'] and v == segment['target']
                                                and length == segment['distance_m'] and modes & segment['mode_bit']
                                                for u, v, length, modes in data['edges']))

    def test_mixed_modes_snapping_and_wrapper_loading(self):
        data = dict(nodes=[[36, 129], [36.001, 129], [36.002, 129], [36.003, 129], [36, 129.001]],
                    edges=[[0, 1, 10, 1], [1, 2, 20, 4], [2, 3, 30, 2], [0, 4, 50, 1], [4, 3, 50, 1]])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'route_data.js'
            path.write_text('window.LOCAL_ROUTE_DATA=' + json.dumps(data) + ';', encoding='utf-8')
            graph = RoadGraph.load(path)
        result = graph.route(data['nodes'][0], data['nodes'][3])
        self.assertEqual(result['distance_m'], 60)
        self.assertEqual([leg['mode'] for leg in result['legs']], ['walk', 'drive', 'bike'])
        self.assertEqual(result['mode_changes'], 2)
        self.assertEqual(result['backend'], 'python')
        self.assertEqual(graph.shortest_path(0, 3, 'walk')['distance_m'], 100)
        self.assertFalse(graph.shortest_path(0, 3, 'drive')['reachable'])
        self.assertEqual(graph.route(data['nodes'][0], data['nodes'][0])['distance_m'], 0)
        self.assertIsNone(graph.nearest([0, 0]))
        self.assertEqual(graph.route([0, 0], data['nodes'][0])['error'], 'snap_failed')
        self.assertIsNone(graph.nearest(data['nodes'][0], 'car', max_distance=5))

    def test_fewer_changes_on_selected_path_and_invalid_inputs(self):
        data = dict(nodes=[[36 + i * .001, 129] for i in range(4)],
                    edges=[[0, 1, 10, 5], [1, 2, 20, 5], [2, 3, 30, 4]])
        graph = RoadGraph(data)
        self.assertEqual([leg['mode'] for leg in graph.shortest_path(0, 3)['legs']], ['drive'])
        for bad in (0, 8, True, 'boat', None):
            with self.subTest(mode=bad), self.assertRaises(ValueError):
                graph.shortest_path(0, 3, bad)
        for point in ([float('nan'), 129], [91, 129], [36], [True, 129]):
            with self.subTest(point=point), self.assertRaises(ValueError):
                graph.nearest(point)
        with self.assertRaises(ValueError):
            RoadGraph(dict(nodes=[[36, 129]], edges=[[0, 0, -1, 1]]))
        with self.assertRaises(ValueError):
            graph.shortest_path(-1, 0)


if __name__ == '__main__':
    unittest.main()
