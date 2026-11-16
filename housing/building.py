"""건축물대장 표제부: 주용도·세대수·사용승인연도."""
from dataclasses import dataclass

from housing.address import Address
from housing.datagokr import call_datagokr, items_of

TITLE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"
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
    row = next((r for r in main if r.get("mainPurpsCdNm") in RESIDENTIAL), main[0] if main else None)
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
