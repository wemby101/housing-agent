"""건축물대장 표제부(주용도·세대수·사용승인연도)와 전유부(호별 전용면적)."""
from dataclasses import dataclass

from housing.address import Address
from housing.datagokr import call_datagokr, items_of

TITLE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"
EXPOS_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrExposPubuseAreaInfo"
REGISTER_PAGE = 1000
# shortcut: 호마다 전유·공용 여러 줄이라 큰 단지는 수천~수만 줄이고, 연달아 부르면 data.go.kr이 503을 준다.
# 2천 줄 넘는 단지(대개 큰 아파트)는 목록 없이 직접 입력. 필요해지면 동별로 나눠 조회
MAX_PAGES = 2
RESIDENTIAL = {"공동주택", "단독주택"}


@dataclass(frozen=True)
class BuildingInfo:
    main_purpose: str
    etc_purpose: str
    households: int
    approval_year: int | None
    is_residential: bool

    @property
    def is_apartment(self) -> bool:
        return "아파트" in self.main_purpose + self.etc_purpose


def get_building(addr: Address) -> BuildingInfo | None:
    body = call_datagokr(TITLE_URL, {
        "sigunguCd": addr.sigungu_cd, "bjdongCd": addr.bjdong_cd, "platGbCd": addr.plat_gb_cd,
        "bun": addr.bun, "ji": addr.ji, "numOfRows": 100, "pageNo": 1,  # pageNo가 없으면 1행만 온다
    })
    rows = items_of(body)
    main = [r for r in rows if r.get("mainAtchGbCdNm") == "주건축물"] or rows
    # shortcut: 한 지번에 여러 동(아파트 단지·주상복합)이면 주택 동을 고른다. 내 호수가 어느 동인지는 보지 않는다
    homes = [r for r in main if r.get("mainPurpsCdNm") in RESIDENTIAL]  # 단지엔 경비실·관리동도 공동주택으로 나온다
    row = max(homes, key=lambda r: int(r.get("hhldCnt") or 0)) if homes else (main[0] if main else None)
    if row is None:
        return None
    apr = str(row.get("useAprDay") or "")
    purpose = row.get("mainPurpsCdNm") or ""
    return BuildingInfo(
        main_purpose=purpose,
        etc_purpose=row.get("etcPurps") or "",
        households=int(row.get("hhldCnt") or 0),
        approval_year=int(apr[:4]) if apr[:4].isdigit() else None,
        is_residential=purpose in RESIDENTIAL,
    )


def exclusive_areas(addr: Address) -> dict[tuple[str, str], float]:
    """(동, 호) → 전용면적㎡. 브이월드(해외 서버에서 막힘) 대신 호 목록을 채울 때 쓴다."""
    params = {"sigunguCd": addr.sigungu_cd, "bjdongCd": addr.bjdong_cd, "platGbCd": addr.plat_gb_cd,
              "bun": addr.bun, "ji": addr.ji, "numOfRows": REGISTER_PAGE}
    areas: dict[tuple[str, str], float] = {}
    for page in range(1, MAX_PAGES + 1):
        body = call_datagokr(EXPOS_URL, {**params, "pageNo": page})
        if int(body.get("totalCount") or 0) > MAX_PAGES * REGISTER_PAGE:
            return {}  # 일부 호만 보여 주면 내 호가 없어 헷갈린다
        rows = items_of(body)
        for r in rows:
            ho = str(r.get("hoNm") or "").strip().removesuffix("호").strip()
            if r.get("exposPubuseGbCdNm") != "전유" or not ho:
                continue
            try:
                area = float(r.get("area"))
            except (TypeError, ValueError):
                continue
            key = (str(r.get("dongNm") or "").strip(), ho)
            areas[key] = round(areas.get(key, 0) + area, 2)  # 복층 등 전유가 여러 줄이면 더한다
        if not rows or page * REGISTER_PAGE >= int(body.get("totalCount") or 0):
            break
    return areas
