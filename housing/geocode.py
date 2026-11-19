"""지번 주소 → 경위도. 실거래 지도에 쓴다.
좌표제공 키(JUSO_COORD_API_KEY)가 있으면 도로명주소 API(검색 → 좌표, UTM-K), 없으면 브이월드 지오코더(하루 4만 건).
배포 서버(해외)에서는 브이월드가 막혀 도로명주소 API를 쓴다."""
import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median

import requests

from housing import demo
from housing.address import Address
from housing.config import get_key
from housing.errors import DataSourceError
from housing.market import CACHE_DIR

URL = "https://api.vworld.kr/req/address"
JUSO_SEARCH_URL = "https://business.juso.go.kr/addrlink/addrLinkApi.do"
JUSO_COORD_URL = "https://business.juso.go.kr/addrlink/addrCoordApi.do"
CACHE = CACHE_DIR / "geocode.json"  # 지번 좌표는 바뀌지 않아 한 번 찾으면 계속 쓴다
SEED = Path(__file__).resolve().parent.parent / "data" / "geocode_seed.json"  # 데모 집 거래 지번 — 첫 지도도 바로 뜨게 같이 배포
_lock = threading.Lock()
_memo: dict | None = None


def _cache() -> dict:
    global _memo
    if _memo is None:
        _memo = {}
        for path in (SEED, CACHE):
            try:
                _memo.update(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
    return _memo


def geocode(address: str) -> tuple[float, float] | None:
    """(위도, 경도). 주소를 못 찾으면 None, 호출이 실패하면 DataSourceError."""
    with _lock:
        if address in _cache():
            hit = _cache()[address]
            return tuple(hit) if hit else None
    point = _juso_point(address) if os.environ.get("JUSO_COORD_API_KEY") else _vworld_point(address)
    with _lock:
        _cache()[address] = point
        CACHE.parent.mkdir(exist_ok=True)
        tmp = CACHE.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(_cache(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(CACHE)
    return point


def _vworld_point(address: str) -> tuple[float, float] | None:
    params = {"service": "address", "request": "getcoord", "version": "2.0", "crs": "epsg:4326", "type": "parcel",
              "address": address, "refine": "true", "simple": "false", "format": "json", "key": get_key("VWORLD_API_KEY")}
    try:
        body = demo.get(URL, params, timeout=5).json()["response"]
    except (requests.RequestException, ValueError, KeyError) as e:  # 메시지에 key가 든 URL이 있을 수 있다
        raise DataSourceError(f"좌표 조회 실패 ({type(e).__name__})") from None
    if body.get("status") == "OK":
        p = body["result"]["point"]
        return float(p["y"]), float(p["x"])
    if body.get("status") == "NOT_FOUND":
        return None
    raise DataSourceError(f"좌표 조회 실패 ({body.get('status')})")


JUSO_GAP = 0.5  # 도로명주소 API는 1초쯤에 11번 넘게 부르면 E0007로 5초 막힌다 (2026-11-19 측정: 0.5초 간격 30번 무사)
_last_call: dict[str, float] = {}
_rate_lock = threading.Lock()


def _juso_rows(url: str, params: dict) -> list[dict]:
    for wait in (5.0, None):  # 그래도 E0007이면 풀릴 때까지 쉬었다 한 번 더
        with _rate_lock:  # 지도 점을 여러 스레드가 찾아도 API마다 간격을 지킨다
            gap = _last_call.get(url, 0) + JUSO_GAP - time.monotonic()
            if gap > 0:
                time.sleep(gap)
            _last_call[url] = time.monotonic()
        try:
            body = demo.get(url, {**params, "resultType": "json"}, timeout=5).json()["results"]
        except (requests.RequestException, ValueError, KeyError) as e:  # 메시지에 confmKey가 든 URL이 있을 수 있다
            raise DataSourceError(f"좌표 조회 실패 ({type(e).__name__})") from None
        code = str(body.get("common", {}).get("errorCode"))
        if code != "E0007" or wait is None:
            break
        time.sleep(wait)
    if code != "0":
        raise DataSourceError(f"좌표 조회 실패 (도로명주소 API {code})")
    return body.get("juso") or []


def _juso_point(address: str) -> tuple[float, float] | None:
    """지번으로 도로명주소를 찾고(건물이 없는 땅이면 None) 그 건물의 좌표를 받는다."""
    rows = _juso_rows(JUSO_SEARCH_URL, {"confmKey": get_key("JUSO_API_KEY"), "keyword": address, "currentPage": 1, "countPerPage": 1})
    if not rows:
        return None
    r = rows[0]
    coords = _juso_rows(JUSO_COORD_URL, {"confmKey": get_key("JUSO_COORD_API_KEY"),
                                         **{k: r[k] for k in ("admCd", "rnMgtSn", "udrtYn", "buldMnnm", "buldSlno")}})
    return utmk_to_wgs84(float(coords[0]["entX"]), float(coords[0]["entY"])) if coords else None


def utmk_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """UTM-K(EPSG:5179: GRS80, 중앙경선 127.5°, 원점위도 38°, 축척 0.9996, 1,000,000 / 2,000,000) → (위도, 경도).
    횡메르카토르 역변환 (Snyder 1987 식) — 수도권 범위에서 오차 1m 미만."""
    a, f, k0 = 6378137.0, 1 / 298.257222101, 0.9996
    e2 = 2 * f - f * f
    ep2 = e2 / (1 - e2)
    c0 = 1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256

    def meridian(phi):
        return a * (c0 * phi - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * phi)
                    + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * phi) - 35 * e2 ** 3 / 3072 * math.sin(6 * phi))

    mu = (meridian(math.radians(38)) + (y - 2_000_000) / k0) / (a * c0)
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + 151 * e1 ** 3 / 96 * math.sin(6 * mu) + 1097 * e1 ** 4 / 512 * math.sin(8 * mu))
    s1, c1, t1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    C, T = ep2 * c1 ** 2, t1 ** 2
    N = a / math.sqrt(1 - e2 * s1 ** 2)
    R = a * (1 - e2) / (1 - e2 * s1 ** 2) ** 1.5
    D = (x - 1_000_000) / (N * k0)
    lat = phi1 - N * t1 / R * (D ** 2 / 2 - (5 + 3 * T + 10 * C - 4 * C ** 2 - 9 * ep2) * D ** 4 / 24
                               + (61 + 90 * T + 298 * C + 45 * T ** 2 - 252 * ep2 - 3 * C ** 2) * D ** 6 / 720)
    lon = (D - (1 + 2 * T + C) * D ** 3 / 6 + (5 - 2 * C + 28 * T - 3 * C ** 2 + 8 * ep2 + 24 * T ** 2) * D ** 5 / 120) / c1
    return math.degrees(lat), 127.5 + math.degrees(lon)


def comp_points(addr: Address, comps) -> list[dict]:
    """지도용 점: 이 집 하나 + 거래 지번마다 하나 (거래 수, ㎡당 중간값). 좌표를 못 찾은 지번은 뺀다."""
    prefix = addr.jibun_addr.split(f" {addr.emd_name} ")[0]  # "서울특별시 관악구"
    lots: dict[tuple[str, str], list] = {}
    for t in comps:
        lots.setdefault((t.umd_name, t.jibun), []).append(t)
    keys = list(lots)

    def lot(k):  # 지번 하나가 실패해도 나머지로 지도를 그린다 (키 없음 같은 실패는 아래 이 집 조회가 알린다)
        try:
            return geocode(f"{prefix} {k[0]} {k[1]}")
        except DataSourceError:
            return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        found = list(pool.map(lot, keys))
        home = geocode(addr.jibun_addr)
    points = [{"lat": home[0], "lon": home[1], "home": True, "count": 0,
               "label": f"이 집 · {addr.jibun_addr.removeprefix(prefix + ' ')}"}] if home else []
    for (umd, jibun), point in zip(keys, found):
        if point:
            ts = lots[(umd, jibun)]
            per = median(t.price_won / t.area_m2 for t in ts)
            points.append({"lat": point[0], "lon": point[1], "home": False, "count": len(ts),
                           "label": f"{jibun} · 거래 {len(ts)}건 · ㎡당 {round(per / 10_000):,}만 원"})
    return points
