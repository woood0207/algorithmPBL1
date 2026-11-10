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
trace_io.py      Trace 형식, 저장·불러오기
visualize.py     PNG, GIF
analysis.py      전체 비교, 통계 실험
road_map.py      OSM 다운로드, 지도 HTML
server.py        웹 뷰어용 로컬 서버
web/             웹 뷰어 (서버가 준 Trace를 그리기만 함)
test_engine.py   회귀 테스트
data/            road_route 그래프와 예전 830 그래프
```

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
- **휴리스틱 이름**: `haversine`은 직선거리, `geo`는 직선거리 × α(α = 간선 길이/직선거리의 최솟값)입니다. 이 그래프는 α ≥ 1이라 직선거리도 하한입니다.

## 검증

- `python main.py selftest`: 정확 알고리즘과 Dijkstra 비교, Weighted A* 비용 상한, ALT 하한, 경유지, 도보-차량-도보, Trace 입출력 등 17개.
- 팀원 원본 코드와 같은 입력으로 비교: `road_route` Dijkstra, `path_algorithms`의 정확·비정확 알고리즘, `shortest_via`, `vehicle_switch`(`--drive-rule node`)에서 300개 쌍 기준 일치(위의 BFS 차이 제외).

지도 데이터 © OpenStreetMap contributors (ODbL).
