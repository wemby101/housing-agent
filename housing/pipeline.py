"""한 번의 진단: 출처별로 따로 호출하고, 실패한 출처는 errors에 남긴 채 나머지를 계산한다."""
from dataclasses import dataclass
from datetime import date

from housing.address import Address
from housing.building import BuildingInfo, get_building
from housing.errors import DataSourceError
from housing.market import MarketEstimate, get_market_price
from housing.official_price import OfficialPrice, get_official_price
from housing.policies import PolicyMatch, Profile, load_policies, match_policies
from housing.risk import RiskReport, assess_risk


@dataclass
class Diagnosis:
    address: Address
    building: BuildingInfo | None
    official: OfficialPrice | None
    market: MarketEstimate | None
    risk: RiskReport
    policies: list[PolicyMatch]
    errors: dict[str, str]


def diagnose(address: Address, dong: str, ho: str, area_m2: float, deposit_won: int,
             monthly_rent_won: int, senior_debt_won: int | None, profile: Profile,
             manual_official_won: int | None = None, today: date | None = None) -> Diagnosis:
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
    market = attempt("market", get_market_price, address, area_m2, building.approval_year if building else None, today)
    risk = assess_risk(deposit_won, senior_debt_won, official, market, building, market_failed="market" in errors,
                       building_failed="building" in errors)
    return Diagnosis(address, building, official, market, risk, match_policies(profile, load_policies()), errors)
