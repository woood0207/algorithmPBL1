# KoreaOSM — nationwide offline route planner

This directory contains the desktop KoreaOSM application, including its complete
offline South Korea dataset. The existing application at the repository root is
unchanged by this addition.

## Download and run on Windows

Install Python 3.10 or newer, Git and Git LFS. Then:

```text
git lfs install
git clone --branch feat/korea-osm-offline-routing https://github.com/woood0207/algorithmPBL1.git
cd algorithmPBL1
git lfs pull
cd KoreaOSM
start_national_map.bat
```

The launcher restores the bundled data automatically on first launch, verifies
the restored SHA-256 hashes, and starts the local map server. Allow at least
8 GB of free disk space for the downloaded archive parts and restored application.
The browser opens the local map address printed by the server.

The application runtime uses Python's standard library and the bundled Leaflet
assets; no Python packages or API keys are required for connected-network routing.
Once Git LFS has downloaded the archive parts, restoration and routing are offline.
GitHub's source ZIP downloads may contain only LFS pointers. Clone the branch with
Git LFS and run `git lfs pull` to obtain the actual data.

Alternative launch:

```text
python main.py national --port 8766
```

To verify every restored dataset file again:

```text
python localOSM/dataset_bundle.py --verify
```

## Included behavior

* Nationwide road routes, with shortest-distance and speed-based fastest options.
* Walking, cycling and passenger-car road modes.
* Train and bus access, ride and final-connection routes.
* Timetable-free connected-network routing and a separate imported-timetable mode.
* English map controls and route results, route highlighting and GeoJSON exports.

The connected-network model assumes continuous train service on connected tracks
and bus service between road-connected terminals, with no waiting. Train speed is
configurable (default 100 km/h); buses use the car-road speed model. Nearby facility
candidates and optional bus connections at the rail journey's ends are compared.
This is a bounded candidate model, not a global optimum over every facility and
unlimited transfers. Actual services, traffic and fares are not verified.

## Dataset packaging

`data/manifest.json` records the exact original paths, sizes and SHA-256 hashes of
the dataset files. `data/korea-osm-data.zip.part*` are ordered pieces of a single
ZIP64 archive, each at most 512 MiB, tracked through Git LFS. The original largest
database exceeds the per-file limit of GitHub's free Git LFS plan, so it is
compressed and split rather than committed as a normal Git blob.

`localOSM/dataset_bundle.py` reads the ZIP across part boundaries without creating
a joined archive, then restores and verifies the original file bytes. The source
desktop folder is not modified by this packaging. Restored files are ignored by
Git to avoid accidentally committing multi-gigabyte databases.

Data: © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright),
[ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/), extracted by Geofabrik.
The included national dataset metadata identifies the South Korea snapshot dated
2026-11-14. OSM-derived databases remain under ODbL. Leaflet's license is included
at `localOSM/assets/LEAFLET-LICENSE.txt`.
