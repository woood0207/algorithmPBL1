"""Client for the MOLIT TAGO bus API."""
from __future__ import annotations

import json
import hashlib
import math
import os
import re
import threading
import time
from datetime import datetime, time as clock_time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from graph_data import haversine

API_BASE_URL = "https://apis.data.go.kr/1613000"
ROOT = Path(__file__).resolve().parent
CITY_CODE = "37010"
REQUESTED_ROUTES = {"207", "306"}
ROUTE_PAGE_SIZE = 100
STOP_PAGE_SIZE = 500
BUS_PAGE_SIZE = 100
MAX_PAGES = 1000
SERVICE_START = clock_time(5, 20)
SERVICE_END = clock_time(23, 59, 59, 999999)
_ROUTE_ID_CACHE = {}
_ROUTE_ID_CACHE_LOCK = threading.Lock()


class BusAPIError(ValueError):
    pass


def load_env(path=None):
    """Load simple KEY=VALUE entries without overwriting existing environment values."""
    env_path = Path(path) if path else ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def _xml_value(element):
    children = list(element)
    if not children:
        return element.text or ""
    result = {}
    for child in children:
        value = _xml_value(child)
        if child.tag in result:
            if not isinstance(result[child.tag], list):
                result[child.tag] = [result[child.tag]]
            result[child.tag].append(value)
        else:
            result[child.tag] = value
    return result


def parse_response(payload, content_type=""):
    """Parse JSON or XML service responses; preserve the service header for status checks."""
    try:
        if "json" in content_type.lower() or payload.lstrip().startswith((b"{", b"[")):
            return json.loads(payload.decode("utf-8-sig"))
        root = ET.fromstring(payload)
        return {root.tag: _xml_value(root)}
    except (UnicodeDecodeError, json.JSONDecodeError, ET.ParseError) as error:
        raise BusAPIError(f"TAGO 버스 API 응답을 파싱하지 못했습니다: {error}") from error


def _key(row, aliases):
    normalize = lambda value: re.sub(r"[^a-z0-9가-힣]", "", str(value).lower())
    normalized = {normalize(key): value
                  for key, value in row.items()}
    for alias in aliases:
        value = normalized.get(normalize(alias))
        if value not in (None, ""):
            return value
    return None


def _items(payload):
    return _page_items(payload)[0]


def _page_items(payload):
    response = payload.get("response", payload) if isinstance(payload, dict) else {}
    header = response.get("header", {}) if isinstance(response, dict) else {}
    code = _key(header, ("resultCode", "result_code", "code"))
    if code is not None and str(code) not in {"", "0", "00", "K0", "0000", "200"}:
        message = _key(header, ("resultMsg", "resultMessage", "message")) or "서비스 오류"
        raise BusAPIError(f"TAGO 버스 API 오류 {code}: {message}")

    body = response.get("body", response) if isinstance(response, dict) else response
    total_count_value = _key(body, ("totalCount", "total_count"))
    if total_count_value is None:
        total_count_value = _key(response, ("totalCount", "total_count"))
    try:
        total_count = int(total_count_value) if total_count_value is not None else None
    except (TypeError, ValueError) as error:
        raise BusAPIError(f"버스 API totalCount 값이 올바르지 않습니다: {total_count_value!r}") from error
    if total_count is not None and total_count < 0:
        raise BusAPIError(f"버스 API totalCount 값이 음수입니다: {total_count}")
    rows = []

    def visit(value):
        if isinstance(value, list):
            for entry in value:
                visit(entry)
        elif isinstance(value, dict):
            if any(_key(value, aliases) is not None for aliases in (
                    ("routeId", "rteId"), ("nodeId", "stopId"),
                    ("vehicleNo", "vehId", "busId"), ("routeNo",),
                    ("gpsLati", "latitude", "lat"))):
                rows.append(value)
            else:
                for child in value.values():
                    visit(child)

    items = _key(body, ("items",)) if isinstance(body, dict) else None
    item_rows = _key(items, ("item",)) if isinstance(items, dict) else None
    if isinstance(item_rows, list):
        rows.extend(item for item in item_rows if isinstance(item, dict))
    elif isinstance(item_rows, dict):
        rows.append(item_rows)
    else:
        visit(body)
    return rows, total_count


def _number(row, aliases, label, required=True):
    value = _key(row, aliases)
    if value is None and not required:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise BusAPIError(f"버스 API 응답의 {label} 값이 올바르지 않습니다: {value!r}") from error
    if not math.isfinite(number):
        raise BusAPIError(f"버스 API 응답의 {label} 값이 유한하지 않습니다.")
    return number


def _request_items(endpoint, params, service_key, opener, timeout, base_url, page_size):
    rows = []
    total_count = None
    page = 1
    while page <= MAX_PAGES:
        query = dict(params, serviceKey=service_key, pageNo=page, numOfRows=page_size, _type="json")
        url = f"{base_url.rstrip('/')}/{endpoint}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url, headers={"Accept": "application/json, application/xml"})
        try:
            with opener(request, timeout=timeout) as response:
                status = response.status
                content_type = response.headers.get("Content-Type", "")
                payload = response.read()
        except urllib.error.HTTPError as error:
            raise BusAPIError(f"{endpoint} 요청이 HTTP {error.code}을 반환했습니다.") from error
        except urllib.error.URLError as error:
            raise BusAPIError(f"{endpoint} 요청에 실패했습니다: {error.reason}") from error
        except (TimeoutError, OSError) as error:
            raise BusAPIError(f"{endpoint} 요청에 실패했습니다: {error}") from error
        if status != 200:
            raise BusAPIError(f"{endpoint} 요청이 HTTP {status}을 반환했습니다.")
        page_rows, page_total_count = _page_items(parse_response(payload, content_type))
        if page_total_count is not None:
            total_count = page_total_count
        rows.extend(page_rows)
        if total_count is not None:
            if len(rows) >= total_count:
                break
            if not page_rows:
                raise BusAPIError(f"{endpoint} 응답이 비어 있지만 totalCount={total_count}입니다.")
        elif len(page_rows) < page_size:
            break
        page += 1
    else:
        raise BusAPIError(f"{endpoint} 응답이 최대 페이지 수({MAX_PAGES})를 초과했습니다.")
    return rows


def _route_number(row):
    value = str(_key(row, ("routeNo", "rteNo", "routeNm", "rteNm", "routeName")) or "")
    match = re.search(r"(?<!\d)(207|306)(?!\d)", value)
    return match.group(1) if match else None


def discover_route_ids(service_key, opener=urllib.request.urlopen, timeout=15, base_url=API_BASE_URL):
    """Fetch and cache TAGO route IDs for the requested Pohang route numbers."""
    cache_key = (base_url.rstrip("/"), hashlib.sha256(service_key.encode("utf-8")).digest())
    with _ROUTE_ID_CACHE_LOCK:
        cached = _ROUTE_ID_CACHE.get(cache_key)
    if cached is not None:
        return {number: dict(route_ids) for number, route_ids in cached.items()}

    route_ids = {number: {} for number in REQUESTED_ROUTES}
    for number in sorted(REQUESTED_ROUTES, key=int):
        rows = _request_items(
            "BusRouteInfoInqireService/getRouteNoList",
            {"cityCode": CITY_CODE, "routeNo": number},
            service_key, opener, timeout, base_url, ROUTE_PAGE_SIZE,
        )
        for row in rows:
            if _route_number(row) == number:
                route_id = _key(row, ("routeId", "route_id"))
                if route_id is not None:
                    route_ids[number][str(route_id)] = row
    missing = [number for number, matches in route_ids.items() if not matches]
    if missing:
        raise BusAPIError(f"포항시 TAGO 버스 노선 routeId를 찾지 못했습니다: {', '.join(sorted(missing))}")
    with _ROUTE_ID_CACHE_LOCK:
        _ROUTE_ID_CACHE[cache_key] = {number: dict(matches) for number, matches in route_ids.items()}
    return route_ids


def _coordinates(row):
    lat = _number(row, ("gpsLati", "gpsLat", "latitude", "lat", "y"), "위도")
    lon = _number(row, ("gpsLong", "gpsLon", "longitude", "lon", "x"), "경도")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise BusAPIError("버스 API 응답에 범위를 벗어난 위경도 좌표가 있습니다.")
    return lat, lon


def fetch_bus_feed(service_key=None, base_url=None, opener=urllib.request.urlopen, timeout=15,
                   force_service_active=False, current_time=None):
    """Fetch Pohang route stops and live vehicle locations from TAGO."""
    load_env()
    key = service_key or os.environ.get("TAGO_SERVICE_KEY")
    if not key:
        raise BusAPIError("TAGO_SERVICE_KEY가 설정되지 않았습니다. 루트 .env를 확인하세요.")

    api_base_url = (base_url or API_BASE_URL).rstrip("/")
    routes = discover_route_ids(key, opener, timeout, api_base_url)

    result_routes, all_stops, all_buses = [], [], []
    next_node_id = -1
    for number in sorted(routes, key=int):
        for route_id, master in routes[number].items():
            route_params = {"cityCode": CITY_CODE, "routeId": route_id}
            stop_rows = _request_items(
                "BusRouteInfoInqireService/getRouteAcctoThrghSttnList",
                route_params, key, opener, timeout, api_base_url, STOP_PAGE_SIZE,
            )
            bus_rows = _request_items(
                "BusLcInfoInqireService/getRouteAcctoBusLcList",
                route_params, key, opener, timeout, api_base_url, BUS_PAGE_SIZE,
            )
            stops = []
            for index, row in enumerate(stop_rows):
                lat, lon = _coordinates(row)
                sequence = _number(row, ("nodeOrd", "stnSeq", "sttnSeq", "stopSeq", "nodeSeq", "ord", "sequence"),
                                   "정류장 순번", required=False)
                sequence = sequence if sequence is not None else index + 1
                stop = {
                    "node_id": next_node_id,
                    "stop_id": str(_key(row, ("nodeId", "stnId", "sttnId", "stopId", "stnNo")) or f"{route_id}:{sequence}"),
                    "name": str(_key(row, ("nodeNm", "stnNm", "sttnNm", "stopNm", "stationName", "name")) or f"{number}번 정류장"),
                    "sequence": sequence, "lat": lat, "lon": lon,
                }
                next_node_id -= 1
                stops.append(stop)
            stops.sort(key=lambda stop: stop["sequence"])
            if not stops:
                raise BusAPIError(f"{number}번 노선({route_id})의 정류장 정보가 비어 있습니다.")
            buses = []
            for row in bus_rows:
                lat, lon = _coordinates(row)
                speed = _number(row, ("vhclSpeed", "vhclSpd", "speed", "spd", "speedKmh"), "버스 속도", required=False)
                sequence = _number(row, ("nodeOrd", "stnSeq", "nodeSeq", "sequence"),
                                   "현재 정류장 순번", required=False)
                buses.append({
                    "bus_id": str(_key(row, ("vehicleNo", "vhclNo", "busId", "vehId")) or f"{route_id}-{len(buses) + 1}"),
                    "route_id": route_id, "route_no": number, "lat": lat, "lon": lon,
                    "speed_kmh": speed if speed and speed > 0 else None,
                    "sequence": sequence,
                })
            route = {
                "route_id": route_id, "route_no": number,
                "name": str(_key(master, ("routeNm", "rteNm", "routeName")) or f"{number}번"),
                "stops": stops, "buses": buses, "service_ended": not bool(buses),
            }
            result_routes.append(route)
            all_stops.extend(stops)
    service_time = current_time if current_time is not None else datetime.now().time()
    service_hours = SERVICE_START <= service_time < SERVICE_END
    for route in result_routes:
        if force_service_active:
            active_buses = route["buses"]
        elif not service_hours:
            active_buses = []
        else:
            stops = route["stops"]
            terminal_sequences = {stops[0]["sequence"], stops[-1]["sequence"]}
            active_buses = [
                bus for bus in route["buses"]
                if bus.get("sequence") not in terminal_sequences
            ]
        route["buses"] = active_buses
        route["service_ended"] = not bool(active_buses)
        all_buses.extend(active_buses)
    service_ended = not bool(all_buses)
    return {
        "routes": result_routes, "stops": all_stops, "buses": all_buses,
        "fetched_at": time.time(), "source": "TAGO/1613000",
        "service_ended": service_ended,
        "service_message": "현재 운행 중인 버스가 없습니다 (운행 종료)" if service_ended else None,
        "simulation_override": bool(force_service_active),
    }


def estimate_bus_arrivals(route, buses):
    """Return each active vehicle's estimated seconds to each route stop."""
    stops = route["stops"]
    arrivals = {stop["node_id"]: [] for stop in stops}
    for bus in buses:
        if bus["route_id"] != route["route_id"]:
            continue
        sequence = bus.get("sequence")
        if sequence is not None:
            nearest = min(range(len(stops)), key=lambda i: abs(stops[i]["sequence"] - sequence))
        else:
            nearest = min(range(len(stops)), key=lambda i: haversine(
                bus["lat"], bus["lon"], stops[i]["lat"], stops[i]["lon"]))
        speed_mps = max(0.5, min(80.0, (bus.get("speed_kmh") or 25.0) / 3.6))
        remaining = haversine(bus["lat"], bus["lon"], stops[nearest]["lat"], stops[nearest]["lon"])
        for index in range(nearest, len(stops)):
            if index > nearest:
                remaining += haversine(stops[index - 1]["lat"], stops[index - 1]["lon"],
                                       stops[index]["lat"], stops[index]["lon"])
            arrivals[stops[index]["node_id"]].append(remaining / speed_mps)
    return arrivals
