# localOSM: Offline Map and Python Route Planner

localOSM is a self-contained map viewer and shortest-distance route planner for a pre-extracted area around Pohang, South Korea. It displays roads, building footprints and place names, and calculates routes between two points selected on the map.

The browser displays the map with Leaflet. A local Python server reads the routing graph from CSV files and calculates paths using Dijkstra's algorithm. All map assets and graph data are stored in this folder; using the supplied snapshot requires no internet connection.

## 1. Requirements and quick start

You need:

- Python 3.10 or newer.
- A modern browser with JavaScript enabled.
- The complete `localOSM` folder, including both routing CSV files and the `assets` directory.

The runtime uses only Python's standard library. You do not need to install pip packages, download the original OSM PBF file, or configure an external routing service.

### Windows

Double-click `start_map.bat`. It uses `py -3` when available, otherwise `python`.

Alternatively, open a terminal in this folder and run:

```powershell
python server.py
```

The server loads the CSV graph, prints its local address and opens the browser:

```text
http://127.0.0.1:8765/index.html
```

Keep the terminal open while using routing. Press **Ctrl+C** to stop the server.

### Other platforms or launch options

Run `python server.py` from this folder. If your Python executable is named `python3`, substitute `python3` in the commands in this document.

```powershell
# Choose another port if 8765 is already in use.
python server.py --port 8766

# Start the server without opening a browser automatically.
python server.py --no-browser

# Load a different pair of graph CSV files.
python server.py --nodes "path/to/road_nodes.csv" --edges "path/to/road_edges.csv"
```

Open the address printed by the server when using a different port. The server listens on `127.0.0.1` only, so it is accessible from the same computer.

**Use the HTTP address for routing.** Opening `index.html` directly as a file can display the map, but Python route calculation requires `server.py` to be running.

Unless otherwise stated, terminal commands below assume the current directory is `localOSM`.

## 2. Using the map

1. Choose **Mixed**, **Walking only**, **Car only**, or **Bicycle only**. Mixed is the default.
2. Click the start point, then the end point. You can select points on the map, roads, building footprints or place-name labels.
3. The map displays the calculated route, total road distance, distance by mode, ordered stages and mode-change count.
4. Use **Pick start** or **Pick end** to replace one endpoint while keeping the other. Use **Clear** to remove the route and start again.

Route colors are green for walking, purple for bicycle and blue for car. Small markers indicate mode changes. Gray dashed lines connect the clicked points to their snapped road nodes.

You can also:

- Search for roads, buildings and places using displayed names or their original local names.
- Toggle footpaths, cycleways, road junction/end markers and building footprints.
- Show place names when the displayed metric scale bar is **50 m or closer**, such as 50 m, 30 m or 10 m. Overlapping names are filtered for readability.
- Inspect road or building details after completing point selection.
- Use **View full area** to return to the supplied map extent.

The interface is English. Names use supplied OSM English names when available. Otherwise, the original Korean or other local-language name is retained. Names and address values are not automatically romanized. This policy also applies when preparing data for an expanded area.

## 3. Files and responsibilities

```text
localOSM/
|-- index.html                 Map interface and route visualization
|-- map_data.js                Display data: roads, buildings, names and map extent
|-- road_nodes.csv             Default routing nodes
|-- road_edges.csv             Default directed routing edges
|-- routing.py                 Reusable Python routing module and CLI
|-- server.py                  Local HTTP server and routing API
|-- start_map.bat              Windows launcher
|-- __init__.py                Allows Python package imports
|-- README.md                  This guide
|-- verification.json          Recorded verification results
|-- test_routing.py            Routing and CSV tests
|-- test_server.py             HTTP API tests
|-- .gitignore                 Ignores generated Python bytecode
|-- route_data.js              Legacy graph, available for explicit import
`-- assets/
    |-- leaflet.js             Leaflet map library
    |-- leaflet.css            Leaflet styles
    |-- LEAFLET-LICENSE.txt     Leaflet license
    |-- images/                Local Leaflet image assets
    `-- local_router.js        Previous browser routing implementation; not loaded
```

The map currently loads `assets/leaflet.js`, `assets/leaflet.css` and `map_data.js`. It requests routes from Python rather than loading `route_data.js` or `assets/local_router.js` into the browser.

### Display data and routing data are separate

- **`map_data.js` controls what the map shows**, including road geometry, building footprints, names and the selectable area.
- **`road_nodes.csv` and `road_edges.csv` control route calculation.** Editing them does not rebuild the displayed roads or buildings.

The server loads the routing graph once at startup and reuses it. **Restart the server after changing either CSV file.** Refresh the browser after changing display data.

When replacing or expanding the area, prepare compatible display data and routing CSVs together. This folder contains the runtime and prepared data, not a PBF download or extraction tool.

The supplied routing snapshot contains **242,467 nodes** and **507,702 directed edges**. The display area is a rectangle centered near latitude `36.01243647`, longitude `129.32180209`, extending approximately 20 km in each cardinal direction. Boundary-crossing road ways may extend beyond that rectangle.

## 4. CSV graph specification

Files are read as UTF-8; a UTF-8 BOM is accepted. Headers are case-sensitive. Routing coordinates are WGS84 latitude/longitude in decimal degrees.

### `road_nodes.csv`

Required columns:

| Column | Meaning |
| --- | --- |
| `id` | Unique integer node identifier. |
| `latitude` | Latitude from -90 to 90. |
| `longitude` | Longitude from -180 to 180. |

Example with nonsequential IDs:

```csv
id,latitude,longitude
100,36.0000,129.0000
500,36.0010,129.0000
```

IDs need not start at zero or be consecutive. The bundled files use zero-based generated IDs; they should not be assumed to be original OSM node IDs.

### `road_edges.csv`

The bundled format is:

| Column | Meaning |
| --- | --- |
| `source` | Source node ID from `road_nodes.csv`. |
| `target` | Target node ID from `road_nodes.csv`. |
| `weight` | Non-negative road distance in meters. |
| `mode_mask` | Integer bitmask specifying allowed transport modes. |
| `weight_unit` | `m` for the bundled distance weights. |

```csv
source,target,weight,mode_mask,weight_unit
100,500,111.2,7,m
500,100,111.2,3,m
```

Every edge is **directed**: the first example permits all modes from node 100 to node 500, while the reverse edge permits walking and bicycle only. A two-way connection requires both directions to be present. Parallel edges are supported.

Mode bits can be combined:

| Mask | Allowed modes |
| --- | --- |
| `1` | Walk |
| `2` | Bicycle |
| `3` | Walk + bicycle |
| `4` | Car |
| `5` | Walk + car |
| `6` | Bicycle + car |
| `7` | Walk + bicycle + car |

The loader rejects duplicate node IDs, missing endpoint references, invalid coordinates, negative or non-finite distances, and masks outside 1 through 7.

### Compatibility with other road CSV exports

- `length_m` can be supplied instead of `weight`. A non-empty `length_m` value takes priority over `weight`.
- A non-empty `mode_mask` takes priority over `mode`. Otherwise, `mode` can specify `walk`, `bike` or `drive` for that row.
- If `length_m` is absent or empty, `weight` is interpreted as meters. A non-empty `weight_unit` must then be `m`.
- A time-valued `weight`, such as one with `weight_unit=s`, requires a separate `length_m` value. Seconds are not interpreted as meters.
- Additional columns are ignored.

### CSV IDs versus internal indexes

The loader maps node IDs to contiguous, zero-based indexes in **CSV row order**. The algorithm's `path`, segment endpoints and snapped `node` values use these internal indexes.

Use `graph.node_ids[index]` to recover the CSV ID. For the sample nodes above, internal index 0 corresponds to CSV ID 100, and index 1 corresponds to CSV ID 500.

## 5. Route calculation and model assumptions

The routing process is:

1. Find the nearest eligible road node to each input coordinate, using great-circle distance. A node is eligible if an incoming or outgoing edge allows the selected mode.
2. Reject an endpoint if no eligible node is within the default **500 m** snap limit.
3. Run Dijkstra's algorithm on the directed graph using non-negative road distances.
4. Reconstruct the selected edges and assign an allowed mode to each edge.
5. Group consecutive edges with the same mode into stages, called `legs` in the result.

The click is snapped to a **node**, not projected onto the nearest point of a road segment. The dashed access links are straight-line connections and are not checked as traversable paths. Their distances are reported separately and excluded from `distance_m`.

The objective is **shortest road distance**, not shortest time, lowest fare or fewest transfers. Searches use only the loaded graph and do not download missing roads or outside-area detours. If both points snap to the same node, the road distance is zero.

### Mixed mode

Mixed mode permits any edge that allows walking, bicycle or car. It can combine modes, but it can also return a single-mode route when that is optimal.

- Mode changes are allowed at any road node with no distance or transfer penalty.
- Vehicle possession, parking, rental availability and designated transfer locations are not tracked.
- For the selected edge path, mode assignment minimizes unnecessary changes. Transfer count is not used to choose between different equal-distance paths.
- Edge direction and allowed-mode masks are respected. The runtime trusts the prepared CSV masks; it does not infer permissions from road classifications or fetch OSM tags.
- Turn-restriction relations and access barriers on individual nodes are not modeled.

## 6. Run routing without the map

The same Python module can be used without a browser or HTTP server.

### Command line

```powershell
python routing.py --start 36.0122587 129.3218056 --end 36.0035966 129.3193556 --mode mixed --output result.json
```

Canonical CLI modes are `mixed`, `walk`, `bike` and `drive`. If `--output` is omitted, the result is printed as JSON.

To select CSV files explicitly:

```powershell
python routing.py --nodes road_nodes.csv --edges road_edges.csv --start 36.0122587 129.3218056 --end 36.0035966 129.3193556 --mode walk --output result.json
```

### Python imports

From inside this folder:

```python
from routing import RoadGraph

graph = RoadGraph.load()  # Reads the CSVs beside routing.py.
result = graph.route(
    [36.0122587, 129.3218056],
    [36.0035966, 129.3193556],
    mode="mixed",
)
print(result["reachable"])
print(result["distance_m"])
print(result["legs"])
```

From the parent directory, use `from localOSM.routing import RoadGraph` instead. Default CSV paths are resolved relative to `routing.py`; explicit relative paths are resolved from the current working directory.

Other entry points:

```python
# Load explicit CSV files.
graph = RoadGraph.from_csv("road_nodes.csv", "road_edges.csv")

# Find an eligible node within a custom snap limit.
snap = graph.nearest([36.0122587, 129.3218056], mode="walk", max_distance=250)

# Route using internal node indexes, without geographic snapping.
route = graph.shortest_path(0, 1, mode="walk")
csv_path_ids = [graph.node_ids[index] for index in route["path"]]

# Write a reusable pair of graph CSV files.
graph.export_csv("exported_graph")
```

`graph.route(...)` also accepts `max_snap_m` to override the default 500 m limit for direct Python calls. The map and HTTP API use the default limit.

### Legacy graph import

Older JSON graphs and the `window.LOCAL_ROUTE_DATA=...;` wrapper in `route_data.js` can be loaded explicitly. The wrapper is parsed as JSON, not executed as JavaScript.

```powershell
python routing.py --graph route_data.js --export-csv exported_graph
```

This writes `road_nodes.csv` and `road_edges.csv` into `exported_graph`. The default loader does not fall back to legacy data when CSV files are missing. `--graph` cannot be combined with `--nodes` or `--edges`.

### Result fields

| Field | Meaning |
| --- | --- |
| `reachable` | Whether a path exists between the snapped or supplied nodes. |
| `distance_m` | Sum of selected road-edge distances, or `null` when no route is available. |
| `path` | Ordered internal node indexes. |
| `coordinates` | Ordered `[latitude, longitude]` route coordinates. |
| `segments` | Individual directed edges with mode, mode bit and distance. |
| `legs` | Consecutive same-mode stages, each with distance and coordinates. |
| `distance_by_mode_m` | Distance totals for `walk`, `bike` and `drive`. |
| `mode_changes` | Number of boundaries between successive legs. |
| `start`, `end` | Coordinate-routing only: snapped node index, coordinates and straight-line connection distance. |
| `mode` | Coordinate-routing only: the requested mode mask. |
| `backend`, `algorithm` | Coordinate-routing only: `python` and `dijkstra`. |
| `error` | Coordinate-routing only: `snap_failed` if either endpoint has no eligible node within the snap limit. |

All routing coordinate pairs are **latitude first**. The GeoJSON geometries embedded in `map_data.js` follow the GeoJSON convention of **longitude first**.

## 7. Local HTTP API

The browser calls the same API that other local clients can use.

### Health check

```text
GET http://127.0.0.1:8765/api/health
```

The response identifies the backend, algorithm, graph format, node/edge counts and available modes. A default CSV-backed server reports `graph_format: "csv"`.

### Calculate a route

```text
POST http://127.0.0.1:8765/api/route
Content-Type: application/json
```

```json
{
  "start": [36.0122587, 129.3218056],
  "end": [36.0035966, 129.3193556],
  "mode": "mixed"
}
```

Use `mixed`, `walk`, `bike` or `drive`. The API also accepts the UI's numeric masks `7`, `1`, `2` and `4`.

A valid calculation returns HTTP 200, including when `reachable` is false or snapping fails; inspect the JSON result. Invalid input returns HTTP 400 with an `error` message. Unknown API paths return 404. The server checks local Host and Origin headers and does not enable cross-origin access.

## 8. Tests and recorded checks

From this folder:

```powershell
python -m unittest discover -p "test_*.py"
```

The current suite has seven test methods. It covers 2,500 independent all-pairs distance comparisons, mode restrictions, mixed stages, snapping, zero-distance and unreachable routes, CSV round trips, nonsequential IDs, invalid CSV input, and HTTP API behavior.

`verification.json` records additional checks on the bundled graph, including exact CSV/legacy graph equivalence and real-route distances for all four routing options. These results describe the checked snapshot; rerun validation after changing the data or code.

## 9. Troubleshooting and data updates

| Symptom | What to check |
| --- | --- |
| Python is not found | Install Python 3.10 or newer, then reopen the terminal. Try `py -3 server.py` on Windows or `python3 server.py` on other platforms. |
| Map opens but Python routing is unavailable | Start `server.py`, keep its terminal open and use the printed HTTP address rather than a `file://` URL. |
| Port 8765 is already in use | Stop the previous server or launch with `--port 8766`, then use the newly printed address. |
| CSV file is missing or invalid | Keep both CSVs beside `routing.py`, or pass `--nodes` and `--edges`. Check headers, node IDs, endpoint references, distances and mode masks. |
| No usable node within 500 m | Select a point closer to a road permitted for the chosen mode. |
| No connected route | Try different endpoints or a different mode. The selected directed graph may be disconnected. |
| Names are not visible | Enable place names and zoom until the scale bar reads 50 m or closer. Some names are omitted to prevent overlap; unnamed objects cannot display a name. |
| CSV edits have no effect | Restart the server so it reloads the graph. |
| New routing data does not match the displayed roads | Update `map_data.js` and its area bounds together with the routing CSVs, then refresh the browser. |

To use this project on another computer, copy the complete folder and install Python. Downloading or processing fresh OSM data is a separate preparation step; the runtime does not update itself automatically.

## 10. Data attribution and licenses

The prepared data comes from OpenStreetMap, using a regional extract provided by [Geofabrik](https://download.geofabrik.de/asia/south-korea.html). OpenStreetMap data is copyright OpenStreetMap contributors and licensed under the [Open Database License (ODbL)](https://www.openstreetmap.org/copyright).

The map uses Leaflet 1.9.4 under the BSD 2-Clause license. See [assets/LEAFLET-LICENSE.txt](assets/LEAFLET-LICENSE.txt) for its license notice.
