"""도로명주소 검색 API → 공공데이터 조회용 코드(시군구·법정동·번지·PNU)."""
from dataclasses import dataclass

import requests

from housing import demo
from housing.config import get_key
from housing.errors import DataSourceError

# 같은 API·같은 키, 서버(IP)만 다르다. 2026-11-19 배포 서버(해외)에서 business에만 연결이 안 됐다
JUSO_HOSTS = ["https://business.juso.go.kr", "https://www.juso.go.kr"]


def juso_get(path: str, params: dict, timeout: float = 10):
    """도로명주소 API를 부른다. 연결이 안 되는 서버는 뒤로 보내 다음부터 다른 서버를 먼저 쓴다."""
    *first, last = list(JUSO_HOSTS)
    for host in first:
        try:
            return demo.get(host + path, params, timeout=(3, timeout))  # 막힌 서버는 연결 3초 만에 넘긴다
        except (requests.ConnectionError, requests.Timeout):
            JUSO_HOSTS.remove(host)
            JUSO_HOSTS.append(host)
    return demo.get(last + path, params, timeout=(3, timeout))


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
        emd_name=" ".join(filter(None, [row.get("emdNm", ""), row.get("liNm", "")])),  # 실거래가 umdNm과 같게 "봉담읍 동화리"
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
        resp = juso_get("/addrlink/addrLinkApi.do", params)
        results = resp.json()["results"]
    except (requests.RequestException, ValueError, KeyError) as e:  # 메시지에 confmKey가 든 URL이 있을 수 있다
        raise DataSourceError(f"주소 검색 실패 ({type(e).__name__})") from None
    common = results["common"]
    if common.get("errorCode") != "0":
        raise DataSourceError(f"주소 검색 실패: {common.get('errorMessage')}")
    return [parse_juso_row(r) for r in results.get("juso") or []]
