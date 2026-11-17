"""한 번의 진단: 출처별로 따로 호출하고, 실패한 출처는 errors에 남긴 채 나머지를 계산한다."""
from dataclasses import dataclass, field
from datetime import date

from housing.address import Address
from housing.building import BuildingInfo, get_building
from housing.errors import DataSourceError
from housing.market import MarketEstimate, get_market_price
from housing.official_price import OfficialPrice, get_official_price
from housing.policies import PolicyMatch, Profile, load_policies, match_policies
from housing.risk import RiskReport, assess_risk
from housing.youth import YouthPolicy, get_youth_policies


@dataclass
class Diagnosis:
    address: Address
    building: BuildingInfo | None
    official: OfficialPrice | None
    market: MarketEstimate | None
    risk: RiskReport
    policies: list[PolicyMatch]
    errors: dict[str, str]
    youth: list[YouthPolicy] = field(default_factory=list)  # 온통청년: 이 지역의 다른 청년 주거 정책


def diagnose(address: Address, dong: str, ho: str, area_m2: float, deposit_won: int,
             monthly_rent_won: int, senior_debt_won: int | None, profile: Profile,
             manual_official_won: int | None = None, today: date | None = None,
             registry_flags: tuple[str, ...] = ()) -> Diagnosis:
    today = today or date.today()
    errors: dict[str, str] = {}

    def attempt(name, fn, *args):
        try:
            return fn(*args)
        except DataSourceError as e:
            errors[name] = f"데이터를 가져오지 못했어요: {e}"
            return None

    building = attempt("building", get_building, address)
    if manual_official_won:
        official = OfficialPrice(manual_official_won, today.year, "사용자 입력")
    else:
        official = attempt("official", get_official_price, address, dong, ho)
    year = building.approval_year if building else None
    market = None
    if building is None:  # 건축물대장이 없거나 실패: 같은 지번에 아파트 거래가 쌓여 있으면 아파트로 본다
        try:
            market = get_market_price(address, area_m2, year, today, "apt")
        except DataSourceError:
            pass
        if market and market.basis != "same_jibun":
            market = None
    if market is None:
        kind = "apt" if building and building.is_apartment else "rh"
        market = attempt("market", get_market_price, address, area_m2, year, today, kind)
    risk = assess_risk(deposit_won, senior_debt_won, official, market, building, market_failed="market" in errors,
                       building_failed="building" in errors, registry_flags=registry_flags)
    youth = attempt("youth", get_youth_policies, address.sigungu_cd, profile.age) or []
    return Diagnosis(address, building, official, market, risk, match_policies(profile, load_policies(), address.sigungu_cd[:2]),
                     errors, youth)
