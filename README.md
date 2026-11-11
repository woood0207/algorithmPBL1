# 캠퍼스 경로 탐색 (PBL1 통합본)

팀원들의 코드를 하나로 합친 프로젝트입니다. `road_route`의 도로망을 기본 지도로 쓰고, `WeightedAstar_visualization`의 **탐색 → Trace JSON → 시각화** 구조 위에 나머지 기능을 올렸습니다.

## 실행

Python 3.10 이상이면 추가 설치 없이 웹 뷰어가 돌아갑니다.

```bash
python main.py
```

터미널에 나온 주소(기본 `http://127.0.0.1:8000/`)를 브라우저로 여세요. HTML 파일을 직접 열면 서버와 연결되지 않습니다. 배경 지도 타일에는 인터넷이 필요하고, 없어도 도로와 탐색은 그대로 보입니다.

PNG·GIF, 지도 HTML, 새 지역 다운로드까지 쓰려면 패키지를 설치합니다.

```bash
pip install -r requirements.txt
```

## 무엇이 어디서 왔나

| 원본 | 합쳐진 기능 | 위치 |
| --- | --- | --- |
| `road_route` | 기본 지도(746 정점, 1,976 간선, 실제 도로 곡선), OSM 다운로드·재시도, GraphML, 번호 매기기, ID·좌표로 경로, 좌표→정점 연결(max-snap), 지도 HTML, 간선·정점 정보, 추출 반경 표시 | `data/`, `road_map.py`, `main.py route/build`, 웹 지도 |
| `WeightedAstar_visualization` | f = g + w·h, Trace 저장·불러오기, viridis 확장 순서, open list, 부모 경로, 패널 공유 시계, `result.png`, `search.gif`, `--viz-only` | `search.py`, `trace_io.py`, `visualize.py`, `main.py compare`, 웹 패널 |
| `walk drive walk` | 도보→차량→도보 상태 Dijkstra, 차량 구역(파란 간선), 속도 설정, 일반 A* 함수 `a_star` | `search.py`, 웹 프리셋 "도보-차량-도보" |
| `shortestpath_dijkstra.py` | 경유지 후보 중 최선 선택(Dijkstra 2회), 후보 순위표, 구간별 색, `find_route` | `search.py`, `main.py via`, 웹 프리셋 "경유지" |
| `repbl` (Path Lab) | Greedy, BFS, 양방향 Dijkstra, Beam, Local Walk, Random Walk, 휴리스틱 3종, 예산·시드·재방문 금지, 전체 비교, JSON 저장, `register_algorithm` 확장, 벤치마크 | `search.py`, `analysis.py`, 웹 "같은 조건 전체 비교", `main.py benchmark` |
| `repbl` (greedy_analysis) | 랜덤 쌍 실험, Wilson 신뢰구간, 스피어만 상관, 거리 구간별 그래프 4개, 산점도→지도, 알고리즘별 표시 전환 | `analysis.py`, 웹 "통계 분석", `main.py analyze` |
| `pbl1/alt_visualizer` | ALT, 랜드마크 개수·Farthest·Random·클릭 토글, 전처리 정보, h 정확도 히트맵, 노드 툴팁(실제 거리/h ALT/h 직선/g), 하한을 준 랜드마크 연결선, 끝까지·한 단계 재생, 다크 모드, ALT 설명 | `landmarks.py`, 웹 전반 |

## 웹 뷰어

**경로 탐색 탭**

- 지도를 클릭해 출발, 도착, 랜드마크, 경유지, 차량 구역 중심을 지정합니다. ID나 좌표로 넣어도 됩니다.
- 패널마다 알고리즘, 휴리스틱, w를 고르면 나란히 재생됩니다. 모든 패널이 같은 확장 횟수 시계를 쓰므로 탐색량을 바로 비교할 수 있습니다.
- 재생, 한 단계, 처음, 끝까지, 시계 슬라이더, 속도 조절이 있습니다.
- Trace JSON을 저장하고 다시 불러올 수 있습니다. 불러오면 탐색을 다시 돌리지 않고 그대로 재생합니다.

**통계 분석 탭**: 처음 열면 실험이 자동으로 돌아갑니다. 기본 300쌍은 1분 정도 걸립니다.

## 명령어

```bash
python main.py compare --weights 0 1 3
```

```bash
python main.py compare --algos dijkstra astar greedy --heuristic alt
```

```bash
python main.py compare --viz-only
```

```bash
python main.py route --start 1 --end 100 --serve
```

```bash
python main.py route --start-coord 36.0115136 129.331617 --end-coord 36.0078929 129.3287292 --solver vehicle_switch --drive-radius 400
```

```bash
python main.py via --start 1 --end 500 --waypoints 100 250 320
```

```bash
python main.py benchmark --start 1 --goal 60 --repeats 5
```

```bash
python main.py analyze --pairs 300 --bins 5
```

```bash
python main.py build --center 36.01243647 129.32180209 --radius 1000 --network-type walk
```

```bash
python main.py selftest
```

각 명령의 옵션은 `python main.py 명령 --help`로 볼 수 있습니다. 결과는 `output/`에 저장됩니다. 예전 830개 정점 그래프는 어느 명령이든 `--data-dir data/legacy_830`을 붙이면 됩니다. 이 그래프에는 도로 곡선이 없어 직선으로 그립니다.

`build`는 `data/`의 CSV와 GraphML을 새 지역으로 덮어씁니다. 정점 번호도 바뀝니다.

## 파일 구조

```text
main.py          CLI 입구
graph_data.py    CSV, GraphML 읽기
search.py        모든 탐색 알고리즘과 등록부
landmarks.py     ALT 전처리와 휴리스틱
elevation.py     정점 고도와 업힐 가중치 (ALT 업힐 회피)
trace_io.py      Trace 형식, 저장·불러오기
visualize.py     PNG, GIF
analysis.py      전체 비교, 통계 실험
road_map.py      OSM 다운로드, 지도 HTML
server.py        웹 뷰어용 로컬 서버
web/             웹 뷰어 (서버가 준 Trace를 그리기만 함)
test_engine.py   회귀 테스트
data/            road_route 그래프와 예전 830 그래프
```

## ALT 업힐 회피 (`alt_uphill`)

오르막을 덜 타는 경로를 찾는 ALT입니다. 고도는 `data/road_elevation.csv`(정점별 고도, m)에 있고, 정점이 바뀌면 `python main.py elevation`으로 다시 받습니다(Open-Meteo Elevation API, Copernicus DEM 90 m, 인터넷 필요). 무료 API 제한 때문에 8,697개에 10분 넘게 걸릴 수 있고, 끊기면 다시 실행하면 이어서 받습니다.

- **간선 비용**: `c'(u, v) = 거리 + λ × max(0, 고도(v) − 고도(u))`. 같은 도로도 올라가는 방향만 비싸지므로 정점이 아니라 방향 있는 간선에 붙입니다. λ는 "1 m 오르는 것 = 평지 λ m"이고 기본 8은 Naismith 규칙(600 m 오르기 ≈ 평지 5 km)에서 왔습니다. λ를 키울수록 오르막을 더 피하고, λ = 0이면 보통 최단 경로입니다.
- **계단 벌점**: OSM에서 계단(`highway=steps`)으로 표시된 간선 52개는 거리에 s배를 곱합니다(`--stairs`, 웹 "계단 배수", 1이면 끔). 90 m DEM은 계단의 고도차를 거의 못 잡고(고도차 0으로 나오는 계단이 많음), OSM에는 계단을 오르는 방향이 없어서 양방향 모두에 줍니다. 예: 413 → 75에서 λ=8만 주면 437 m 중 계단 197 m인 경로가 나오고(최단 경로보다 계단이 더 많음), 계단 ×3을 더하면 477 m 중 계단 56 m입니다.
- **ALT**: 랜드마크 거리표를 c' 그래프에서 다시 계산해야 h가 c' 기준 하한이 됩니다. (λ, 랜드마크)별로 한 번 만들고 캐시합니다.
- **웹**: 구성 불러오기 → "ALT · 업힐 회피 (λ = 8) · 계단 ×3 추가". 패널마다 λ와 계단 배수를 바꿀 수 있습니다. 지도의 주황 도로는 경사 3% 이상 구간(진할수록 가파름), 보라 점선은 계단이고, 결과 경로의 분홍은 오르막, 검정은 계단, 파랑은 평지·내리막입니다. 아래 상세에 오르막 총량과 "업힐 무시 최단 경로 대비 몇 m 덜 오르고 몇 m 더 걷는지"가 나옵니다.
- **도보 간선, 실제 길이 기준**: `vehicle-switch` 이후 CSV weight는 이동 시간(초)이고 도보·차량·자전거 간선이 같이 들어 있습니다. 업힐 회피는 도보 가능 간선만 쓰고 `length_m`(m)으로 계산합니다.
- **계단 표시**: 새 CSV에는 도로 종류가 없어서, 예전 캠퍼스 `roads.graphml`의 계단을 양 끝 좌표가 같은 간선에 옮깁니다. 그래서 캠퍼스 밖 계단은 모르고, 캠퍼스 안도 일부만 맞습니다(16개 간선). `road_route.py`가 `highway` 열을 같이 내보내면 그걸 바로 씁니다.
- **CLI**: `python main.py route --start 8247 --end 7708 --solver alt_uphill --uphill 8 --stairs 3`
- **예시** (8,697 정점 지도의 기본 출발 8247 → 도착 7708): 도보 최단 12,622 m / 오르막 208 m → λ=8이면 13,115 m / 98 m. (예전 746 정점 지도에서는 3,599 m / 143 m → 3,658 m / 129 m)
- **더 나은 고도 자료**: 국토지리정보원 국토정보플랫폼(https://map.ngii.go.kr/pd/ctlsSvc/ctlsSvc.do) 지도자료 카탈로그의 "공개DEM"(로그인 필요). 지면 기준이라 건물·나무 높이가 섞인 Copernicus보다 정확할 것으로 보지만 아직 적용하지 않았습니다.
- **한계**: 90 m 격자 DEM이라 짧은 간선의 고도차가 부정확합니다(경사 30% 넘는 간선이 1,888개 중 156개). 경로 전체 오르막 합계는 대략 맞지만 간선 하나하나의 경사는 믿기 어렵습니다.

## 새 알고리즘 추가

`search.py` 끝에서 등록하면 CLI, 전체 비교, 웹 패널에 자동으로 나옵니다. 함수는 `Context`를 받아 `Trace`를 돌려주면 됩니다.

```python
register_algorithm("my_algo", "내 알고리즘", my_algo, uses_heuristic=True, hint="한 줄 설명")
```

`Trace` 형식은 `trace_io.py` 맨 위에 적혀 있습니다. 다른 언어로 구현해도 같은 JSON을 내보내면 웹 뷰어의 "Trace JSON 불러오기"로 그대로 재생됩니다.

## 합치면서 정한 것

- **평행 간선과 self-loop**: CSV의 모든 행은 보존하고 간선 번호도 그대로 쓰지만, 탐색에는 같은 (u, v) 중 가장 짧은 간선만 쓰고 self-loop는 뺍니다. 그래서 BFS 결과 비용이 Path Lab 원본과 일부 쌍에서 다릅니다(같은 홉 수에서 더 짧은 간선을 고름).
- **도보-차량-도보의 차량 간선 기준**: 원본 코드는 도착 정점만 구역 안이면 차량 속도로 계산하지만, 원본의 docstring과 지도 표시는 간선 기준(양 끝이 구역 안)입니다. 기본값은 간선 기준이고, 웹의 "차량 간선 기준"이나 `--drive-rule node`로 원본 동작을 고를 수 있습니다.
- **차량 구역 기본값**: 중심은 지도 중심, 반경 200 m입니다(`road_route.py` 쪽 값). `multi_method.py`의 단독 실행 예시는 500 m였습니다.
- **비용 단위 (2026-11-11)**: 간선 CSV의 `weight_unit`(m 또는 s)을 읽어 그래프가 단위를 압니다. s(이동 시간)면 직선거리 휴리스틱을 "직선거리 ÷ 데이터에서 가장 빠른 속도"로 바꿔 하한을 유지합니다(이전에는 미터를 그대로 써서 A*가 최단 경로를 못 찾았음). 웹 표와 CLI 출력의 단위는 각 Trace의 `cost_unit`을 따르고, 표의 "비용 기준" 열과 상단 표시줄에 무엇을 기준으로 돌렸는지 나옵니다. 수단 열이 없는 예전 CSV(`legacy_830`)도 다시 읽힙니다.
- **휴리스틱 이름**: `haversine`은 직선거리, `geo`는 직선거리 × α(α = 간선 길이/직선거리의 최솟값)입니다. 이 그래프는 α ≥ 1이라 직선거리도 하한입니다.

## 검증

- `python main.py selftest`: 정확 알고리즘과 Dijkstra 비교, Weighted A* 비용 상한, ALT 하한, 경유지, 도보-차량-도보, Trace 입출력 등 20개 (ALT 업힐 회피가 c' 그래프의 Dijkstra와 같은 비용인지 포함).
- 팀원 원본 코드와 같은 입력으로 비교: `road_route` Dijkstra, `path_algorithms`의 정확·비정확 알고리즘, `shortest_via`, `vehicle_switch`(`--drive-rule node`)에서 300개 쌍 기준 일치(위의 BFS 차이 제외).

지도 데이터 © OpenStreetMap contributors (ODbL).
