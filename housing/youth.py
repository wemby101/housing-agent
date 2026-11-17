"""온통청년 청년정책 API: 이 지역 지자체의 다른 청년 주거 정책 (정책표에 없는 것만, 자격은 판정하지 않음)."""
from dataclasses import dataclass
from datetime import date

import requests

from housing import demo
from housing.config import get_key
from housing.errors import DataSourceError

YOUTH_URL = "https://www.youthcenter.go.kr/go/ythip/getPlcy"
CLOSED = "0057003"  # 신청기간구분: 마감
COVERED = ("보증료", "청년월세", "월세 지원")  # 정책표에서 이미 조건까지 따져 보여준다
PAGE_SIZE = 100


@dataclass(frozen=True)
class YouthPolicy:
    name: str
    org: str
    support: str  # 지원 내용 (원문)
    period: str   # 신청기간, 없으면 "상시"
    url: str


def _fetch(zip_cd: str) -> list[dict]:
    rows, page = [], 1
    while True:
        params = {"apiKeyNm": get_key("YOUTH_POLICY_API_KEY"), "pageNum": page, "pageSize": PAGE_SIZE, "rtnType": "json",
                  "pageType": 1, "lclsfNm": "주거", "zipCd": zip_cd}
        try:
            data = demo.get(YOUTH_URL, params, timeout=20).json()
        except (requests.RequestException, ValueError) as e:  # 메시지에 apiKeyNm이 든 URL이 있을 수 있다
            status = getattr(getattr(e, "response", None), "status_code", None)
            raise DataSourceError(f"온통청년 조회 실패 ({f'HTTP {status}' if status else type(e).__name__})") from None
        if str(data.get("resultCode")) != "200":
            raise DataSourceError(f"온통청년 조회 실패: {data.get('resultMessage') or data.get('resultCode')}")
        result = data.get("result") or {}
        batch = result.get("youthPolicyList") or []
        rows += batch
        if not batch or len(rows) >= int((result.get("pagging") or {}).get("totCount") or 0):
            return rows
        page += 1


def get_youth_policies(zip_cd: str, age: int, today: date | None = None) -> list[YouthPolicy]:
    """zip_cd(시군구 5자리) 지역 전용, 신청 가능, 나이가 맞는 주거 정책."""
    out, seen = [], set()
    today_s = (today or date.today()).strftime("%Y%m%d")
    for p in _fetch(zip_cd):
        name = (p.get("plcyNm") or "").strip()
        zips = [z for z in (p.get("zipCd") or "").split(",") if z]
        lo, hi = int(p.get("sprtTrgtMinAge") or 0), int(p.get("sprtTrgtMaxAge") or 0)
        end = (p.get("aplyYmd") or "").replace(" ", "").split("~")[-1]  # "20250507 ~ 20251231" → 마감일
        if (not name or name in seen or not zips
                or any(z[:2] != zip_cd[:2] for z in zips)  # 전국·다른 지역까지 걸린 정책은 정책표·다른 안내와 겹친다
                or p.get("aplyPrdSeCd") == CLOSED or p.get("mclsfNm") == "기숙사"
                or any(w in name for w in COVERED)
                or (hi and not lo <= age <= hi)
                or (len(end) == 8 and end.isdigit() and end < today_s)):  # 기간구분이 '마감'이 아니어도 날짜가 지난 것
            continue
        seen.add(name)
        out.append(YouthPolicy(name, (p.get("rgtrInstCdNm") or p.get("sprvsnInstCdNm") or "").strip(),
                               (p.get("plcySprtCn") or "").strip(), (p.get("aplyYmd") or "").strip() or "상시",
                               (p.get("aplyUrlAddr") or p.get("refUrlAddr1") or "https://www.youthcenter.go.kr").strip()))
    return out
