"""지번 주소 → 경위도 (브이월드 지오코더 API 2.0, 하루 4만 건). 실거래 지도에 쓴다."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from statistics import median

import requests

from housing import demo
from housing.address import Address
from housing.config import get_key
from housing.errors import DataSourceError
from housing.market import CACHE_DIR

URL = "https://api.vworld.kr/req/address"
CACHE = CACHE_DIR / "geocode.json"  # 지번 좌표는 바뀌지 않아 한 번 찾으면 계속 쓴다
_lock = threading.Lock()
_memo: dict | None = None


def _cache() -> dict:
    global _memo
    if _memo is None:
        try:
            _memo = json.loads(CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _memo = {}
    return _memo


def geocode(address: str) -> tuple[float, float] | None:
    """(위도, 경도). 주소를 못 찾으면 None, 호출이 실패하면 DataSourceError."""
    with _lock:
        if address in _cache():
            hit = _cache()[address]
            return tuple(hit) if hit else None
    params = {"service": "address", "request": "getcoord", "version": "2.0", "crs": "epsg:4326", "type": "parcel",
              "address": address, "refine": "true", "simple": "false", "format": "json", "key": get_key("VWORLD_API_KEY")}
    try:
        body = demo.get(URL, params, timeout=5).json()["response"]
    except (requests.RequestException, ValueError, KeyError) as e:  # 메시지에 key가 든 URL이 있을 수 있다
        raise DataSourceError(f"좌표 조회 실패 ({type(e).__name__})") from None
    if body.get("status") == "OK":
        p = body["result"]["point"]
        point = (float(p["y"]), float(p["x"]))
    elif body.get("status") == "NOT_FOUND":
        point = None
    else:
        raise DataSourceError(f"좌표 조회 실패 ({body.get('status')})")
    with _lock:
        _cache()[address] = point
        CACHE.parent.mkdir(exist_ok=True)
        tmp = CACHE.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(_cache(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(CACHE)
    return point


def comp_points(addr: Address, comps) -> list[dict]:
    """지도용 점: 이 집 하나 + 거래 지번마다 하나 (거래 수, ㎡당 중간값). 좌표를 못 찾은 지번은 뺀다."""
    prefix = addr.jibun_addr.split(f" {addr.emd_name} ")[0]  # "서울특별시 관악구"
    lots: dict[tuple[str, str], list] = {}
    for t in comps:
        lots.setdefault((t.umd_name, t.jibun), []).append(t)
    keys = list(lots)
    with ThreadPoolExecutor(max_workers=6) as pool:
        found = list(pool.map(lambda k: geocode(f"{prefix} {k[0]} {k[1]}"), keys))
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
