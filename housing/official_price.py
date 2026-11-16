"""공동주택 공시가격 (브이월드). 자동 조회 실패 시 화면에서 사용자 입력으로 대체한다."""
from dataclasses import dataclass

from housing.address import Address
from housing.config import get_key
from housing.errors import DataSourceError

VWORLD_URL = "https://api.vworld.kr/ned/data/getApartHousingPriceAttr"


@dataclass(frozen=True)
class OfficialPrice:
    price_won: int
    year: int
    source: str  # "브이월드 공동주택가격" | "사용자 입력"


def get_official_price(addr: Address, dong: str, ho: str) -> OfficialPrice | None:
    get_key("VWORLD_API_KEY")
    # shortcut: VWorld 응답 필드명을 키 없이 확정할 수 없어 아직 미구현, VWORLD_API_KEY 발급 후 fixture로 구현
    raise DataSourceError("공시가격 자동 조회 준비 중이에요. 공시가격을 직접 입력해 주세요.")
