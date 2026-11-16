"""도로명주소 검색 API → 공공데이터 조회용 코드(시군구·법정동·번지·PNU)."""
from dataclasses import dataclass

import requests

from housing.config import get_key
from housing.errors import DataSourceError

JUSO_URL = "https://business.juso.go.kr/addrlink/addrLinkApi.do"


@dataclass(frozen=True)
class Address:
    road_addr: str
    jibun_addr: str
    building_name: str
    sigungu_cd: str   # 5자리
    bjdong_cd: str    # 5자리
    emd_name: str
    plat_gb_cd: str   # 건축물대장: "0" 대지, "1" 산
    bun: str          # 4자리
    ji: str           # 4자리
    pnu: str          # 19자리: admCd + 산여부(1 대지/2 산) + 본번 + 부번


def parse_juso_row(row: dict) -> Address:
    adm_cd = row["admCd"]
    is_san = row.get("mtYn") == "1"
    bun = row["lnbrMnnm"].zfill(4)
    ji = (row.get("lnbrSlno") or "0").zfill(4)
    return Address(
        road_addr=row["roadAddr"],
        jibun_addr=row["jibunAddr"],
        building_name=row.get("bdNm", ""),
        sigungu_cd=adm_cd[:5],
        bjdong_cd=adm_cd[5:],
        emd_name=row.get("emdNm", ""),
        plat_gb_cd="1" if is_san else "0",
        bun=bun,
        ji=ji,
        pnu=adm_cd + ("2" if is_san else "1") + bun + ji,
    )


def search_address(keyword: str, limit: int = 10) -> list[Address]:
    params = {
        "confmKey": get_key("JUSO_API_KEY"),
        "keyword": keyword,
        "currentPage": 1,
        "countPerPage": limit,
        "resultType": "json",
    }
    try:
        resp = requests.get(JUSO_URL, params=params, timeout=10)
        resp.raise_for_status()
        results = resp.json()["results"]
    except (requests.RequestException, ValueError, KeyError) as e:  # 메시지에 confmKey가 든 URL이 있을 수 있다
        raise DataSourceError(f"주소 검색 실패 ({type(e).__name__})") from None
    common = results["common"]
    if common.get("errorCode") != "0":
        raise DataSourceError(f"주소 검색 실패: {common.get('errorMessage')}")
    return [parse_juso_row(r) for r in results.get("juso") or []]
