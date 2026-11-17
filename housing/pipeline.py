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
    jeonse: MarketEstimate | None = None  # 주변 전세 시세 (전세 계약일 때만)
    # 조건을 바꿔 다시 계산할 때(what_if) 쓰는 입력값
    deposit_won: int = 0
    monthly_rent_won: int = 0
    senior_debt_won: int | None = None
    registry_flags: tuple[str, ...] = ()
    policies_checked: bool = True  # 나이·소득을 몰라 지원 정책을 찾지 않았으면 False
    dong: str = ""
    ho: str = ""

# 전월세 전환율 법정 상한 = 한국은행 기준금리 3.00%(2026-10-22 동결) + 2%p (주택임대차보호법 시행령 제9조)
LEGAL_CONVERSION_PCT = 5.0


def diagnose(address: Address, dong: str, ho: str, area_m2: float, deposit_won: int,
             monthly_rent_won: int, senior_debt_won: int | None, profile: Profile | None,
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
    jeonse = None
    if deposit_won > 0 and monthly_rent_won == 0:
        kind = market.kind if market else ("apt" if building and building.is_apartment else "rh")
        jeonse = attempt("jeonse", get_market_price, address, area_m2, year, today, f"{kind}_rent", 12)  # 전세는 최근 1년이 시세
    risk = assess_risk(deposit_won, senior_debt_won, official, market, building, market_failed="market" in errors,
                       building_failed="building" in errors, registry_flags=registry_flags, jeonse=jeonse)
    # 나이·소득을 모르면(profile=None) 임시 값으로 고르지 않고 정책 찾기를 건너뛴다
    youth = (attempt("youth", get_youth_policies, address.sigungu_cd, profile.age) or []) if profile else []
    policies = match_policies(profile, load_policies(), address.sigungu_cd[:2]) if profile else []
    return Diagnosis(address, building, official, market, risk, policies, errors, youth, jeonse,
                     deposit_won, monthly_rent_won, senior_debt_won, registry_flags, profile is not None, dong, ho)


def what_if(d: Diagnosis, deposit_won: int, monthly_rent_won: int) -> RiskReport:
    """같은 집에서 계약 조건만 바꿔 위험을 다시 계산한다. 공공데이터는 다시 부르지 않는다."""
    return assess_risk(deposit_won, d.senior_debt_won, d.official, d.market, d.building,
                       market_failed="market" in d.errors, building_failed="building" in d.errors,
                       registry_flags=d.registry_flags, jeonse=d.jeonse if monthly_rent_won == 0 else None)


def rent_for_cut(cut_won: int, rate_pct: float) -> int:
    """보증금을 cut_won만큼 낮출 때 같은 값이 되는 월세 (전환율 = 1년 월세 합 ÷ 낮춘 보증금), 천 원 단위."""
    return int(round(cut_won * rate_pct / 100 / 12, -3))


# 청년전용 버팀목전세자금 (주택도시기금, 2026-11-17 확인): 연 2.2~3.3%, 최대 1.5억 원 · 보증금의 80% 이내
YOUTH_LOAN_LIMIT_WON = 150_000_000
YOUTH_LOAN_RATE_PCT = 2.8  # 범위의 가운데. 실제 금리는 소득·보증금에 따라 달라 화면에서 고칠 수 있다


def default_loan(deposit_won: int) -> int:
    return min(deposit_won * 8 // 10, YOUTH_LOAN_LIMIT_WON)


def monthly_interest(loan_won: int, rate_pct: float) -> int:
    """대출 원금에 대한 한 달 이자 (전월세 전환과 같은 식: 금액 × 연이율 ÷ 12), 천 원 단위."""
    return rent_for_cut(loan_won, rate_pct)
