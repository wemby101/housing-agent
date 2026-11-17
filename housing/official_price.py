"""공동주택 공시가격 (브이월드). 자동 조회 실패 시 화면에서 사용자 입력으로 대체한다."""
import os
from dataclasses import dataclass
from datetime import date

import requests

from housing.address import Address
from housing import demo
from housing.config import get_key
from housing.errors import DataSourceError

VWORLD_URL = "https://api.vworld.kr/ned/data/getApartHousingPriceAttr"
SOURCE = "브이월드 공동주택가격"


@dataclass(frozen=True)
class OfficialPrice:
    price_won: int
    year: int
    source: str  # "브이월드 공동주택가격" | "사용자 입력"


@dataclass(frozen=True)
class Unit:
    dong: str
    ho: str
    area_m2: float | None  # 전용면적


def _rows(addr: Address, extra: dict) -> list[dict]:
    """브이월드 공동주택가격 행 목록. 키가 없거나 호출이 실패하면 DataSourceError."""
    try:
        key = get_key("VWORLD_API_KEY")
    except DataSourceError:
        raise DataSourceError("공시가격 자동 조회를 아직 쓸 수 없어요. 공시가격을 직접 입력해 주세요.") from None
    # 브이월드는 domain이 키에 등록한 서비스URL과 다르거나 없으면 INCORRECT_KEY를 준다 (배포 시 VWORLD_DOMAIN으로 바꿈)
    domain = os.environ.get("VWORLD_DOMAIN") or "localhost:8501"
    rows, page = [], 1
    while True:  # 큰 아파트 단지는 한 해에만 수천 행이라 페이지를 넘긴다
        params = {"key": key, "pnu": addr.pnu, "format": "json", "numOfRows": 1000, "pageNo": page, "domain": domain, **extra}
        try:
            resp = demo.get(VWORLD_URL, params, timeout=10)
            data = resp.json()
        except (requests.RequestException, ValueError) as e:  # 메시지에 key가 든 URL이 있을 수 있다
            status = getattr(getattr(e, "response", None), "status_code", None)
            raise DataSourceError(f"공시가격 조회 실패 ({f'HTTP {status}' if status else type(e).__name__})") from None
        body = data.get("apartHousingPrices") or {}  # 0건이면 "response"로 온다
        if body.get("resultCode"):
            raise DataSourceError(f"공시가격 조회 실패: {body.get('resultMsg') or body['resultCode']}")
        batch = body.get("field") or []
        rows += batch
        if not batch or len(rows) >= int(body.get("totalCount") or 0):
            break
        page += 1
    return [r for r in rows if str(r.get("stdrYear", "")).isdigit()]


def get_official_price(addr: Address, dong: str, ho: str) -> OfficialPrice | None:
    ho = ho.strip().removesuffix("호").strip()
    if not ho:
        return None
    extra = {"hoNm": ho} | ({"dongNm": dong.strip()} if dong.strip() else {})
    rows = [r for r in _rows(addr, extra) if str(r.get("pblntfPc", "")).isdigit()]
    if not rows:
        return None
    latest = max(rows, key=lambda r: int(r["stdrYear"]))
    return OfficialPrice(int(latest["pblntfPc"]), int(latest["stdrYear"]), SOURCE)


def list_units(addr: Address, year: int | None = None) -> list[Unit]:
    """올해(발표 전이면 작년) 동·호 목록 (공동주택가격이 있는 집만).

    연도를 안 주면 옛 연도부터 와서 큰 단지는 첫 페이지가 수년 전 동 이름(예: 1A동)뿐이다."""
    year = year or date.today().year
    rows = _rows(addr, {"stdrYear": str(year)}) or _rows(addr, {"stdrYear": str(year - 1)})
    if not rows:
        return []
    year = max(r["stdrYear"] for r in rows)
    units = set()
    for r in rows:
        if r["stdrYear"] == year and r.get("hoNm"):
            try:
                area = float(r.get("prvuseAr"))
            except (TypeError, ValueError):
                area = None
            units.add(Unit(r.get("dongNm") or "", r["hoNm"], area))
    return sorted(units, key=lambda u: (u.dong, len(u.ho), u.ho))
