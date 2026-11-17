"""위험 신호 판정. 모든 숫자는 여기서 코드로 계산한다 (AI가 만들지 않음)."""
from dataclasses import dataclass

from housing.building import BuildingInfo
from housing.market import MarketEstimate
from housing.official_price import OfficialPrice

NO_RISK_HEADLINE = "확인된 위험 신호가 없어요. 단, 등기부등본은 꼭 직접 확인하세요."
LEVEL_ORDER = ["danger", "caution", "info"]


@dataclass(frozen=True)
class Signal:
    code: str
    level: str  # "danger" | "caution" | "info"
    title: str
    detail: str
    source: str


@dataclass(frozen=True)
class RiskReport:
    headline: str
    signals: list[Signal]
    deposit_ratio: float | None
    guarantee_limit_won: int | None
    target_deposit_won: int | None


def man(won: int) -> str:
    return f"{won // 10_000:,}만"


def assess_risk(deposit_won: int, senior_debt_won: int | None, official: OfficialPrice | None,
                market: MarketEstimate | None, building: BuildingInfo | None,
                market_failed: bool = False, building_failed: bool = False,
                registry_flags: tuple[str, ...] = ()) -> RiskReport:
    signals: list[Signal] = []
    if registry_flags:
        names = "·".join(registry_flags)
        signals.append(Signal(
            "REGISTRY_FLAGS", "danger", f"등기부에 {names} 기록이 있어요",
            "압류·가압류·가처분·경매·신탁이 걸린 집은 보증금을 돌려받지 못할 위험이 커요. 계약 전에 말소 여부를 반드시 확인하세요.",
            "등기부등본 (업로드한 PDF)"))
    debt = senior_debt_won or 0
    limit = ratio = target = None

    if building is None and not building_failed:
        signals.append(Signal(
            "NO_BUILDING", "caution", "건축물대장에서 이 건물을 찾지 못했어요",
            "대장에 없는 건물(무허가·미등재)은 전세보증보험·전세대출이 어려울 수 있어요. 정부24에서 건축물대장을 직접 떼어 확인하세요.",
            "건축물대장"))
    elif building is not None and not building.is_residential:
        if building.households > 0 or "주택" in building.etc_purpose:  # 상가+주택 복합 건물: 내 호수가 주택인지가 관건
            signals.append(Signal(
                "MIXED_USE", "caution", "주택과 상가가 섞인 건물이에요",
                f"건축물대장 주용도는 {building.main_purpose}예요. 계약할 호수가 건축물대장(전유부)에 주택으로 올라 있는지 확인하세요. "
                "근린생활시설로 되어 있으면 보증보험·전세대출이 막힐 수 있어요.", "건축물대장"))
        else:
            signals.append(Signal(
                "NOT_RESIDENTIAL", "danger", f"주택이 아니에요 (건축물대장 주용도: {building.main_purpose})",
                "주택이 아닌 건물은 전세보증보험·전세대출이 막힐 수 있어요.", "건축물대장"))

    if deposit_won > 0:
        if senior_debt_won is None:
            signals.append(Signal(
                "CHECK_REGISTRY", "caution", "등기부등본에서 근저당을 꼭 확인하세요",
                "선순위 채권을 모르는 상태라 0원으로 계산했어요. 근저당이 있으면 위험이 커져요.", "등기부등본 (직접 확인)"))
        if official is not None:
            limit = official.price_won * 126 // 100 - debt
            if deposit_won > limit:
                apt = building is not None and building.is_apartment  # 아파트는 HUG가 KB·부동산테크 시세를 먼저 쓴다
                over = man(deposit_won - max(limit, 0))
                signals.append(Signal(
                    "GUARANTEE_BLOCKED", "caution" if apt else "danger",
                    f"공시가격 기준으로는 보증보험 한도를 {over} 원 넘어요" if apt else f"보증보험 가입 한도를 {over} 원 넘어요",
                    f"한도 = 공시가격 {man(official.price_won)} 원 × 126% − 선순위 채권 {man(debt)} 원 = {man(max(limit, 0))} 원"
                    + (". 아파트는 HUG가 KB·부동산테크 시세(의 90%)를 먼저 써서 실제 한도는 더 클 수 있어요. "
                       "안심전세 앱이나 HUG(1566-9009)에서 확인하세요." if apt else ""),
                    f"HUG 전세보증 기준 (공시가격 126%) · 공시가격: {official.source} {official.year}"))
        else:
            signals.append(Signal(
                "NO_OFFICIAL_PRICE", "info", "공시가격을 몰라 보증보험 한도를 계산하지 못했어요",
                "부동산공시가격알리미(realtyprice.kr)에서 확인해 입력하면 계산할 수 있어요.", "-"))
        if market is not None and official is not None and market.price_won > official.price_won * 2:
            # 공동주택 공시가격은 보통 시세의 60~70%라, 시세가 2배를 넘으면 특이 거래가 섞인 것으로 본다
            signals.append(Signal(
                "MARKET_DOUBTFUL", "caution",
                f"추정 시세가 공시가격의 {market.price_won / official.price_won:.1f}배라 믿기 어려워요",
                f"추정 시세 {man(market.price_won)} 원 (최근 {market.months}개월 비슷한 거래 {market.comp_count}건). "
                "특이한 거래가 섞였을 수 있어 깡통전세 비율 계산에서 뺐어요. 주변 중개사무소나 KB부동산 시세로 확인하세요.",
                "국토교통부 실거래가 · 공시가격 비교"))
            market, market_failed = None, True  # 비율·목표액에 쓰지 않고, '거래 부족' 안내도 띄우지 않는다
        if market is not None:
            ratio = (deposit_won + debt) / market.price_won  # 선순위 채권도 내 보증금보다 먼저 갚는 돈
            if ratio >= 0.7:
                high = ratio >= 0.8
                signals.append(Signal(
                    "HIGH_RATIO" if high else "CAUTION_RATIO", "danger" if high else "caution",
                    f"보증금{'+선순위 채권' if debt else ''}이 추정 시세의 {int(ratio * 100)}%예요"
                    + (" (깡통전세 위험)" if high else " (주의)"),
                    f"추정 시세 {man(market.price_won)} 원 (최근 {market.months}개월 비슷한 거래 {market.comp_count}건). "
                    "일반적으로 80% 이상은 위험, 70~80%는 주의로 봐요.",
                    f"국토교통부 {'아파트' if market.kind == 'apt' else '연립다세대'} 매매 실거래가 · 일반적 기준"))
        elif not market_failed:  # 조회 실패는 화면의 오류 안내가 설명한다
            signals.append(Signal(
                "NO_MARKET_DATA", "info", "비슷한 매매 거래가 부족해 시세를 추정하지 못했어요",
                "최근 24개월 같은 건물 또는 같은 동의 비슷한 집 거래가 3건 미만이에요.", "국토교통부 실거래가"))
        caps = [c for c in (limit, market.price_won * 7 // 10 - debt if market else None) if c is not None]
        target = max(min(caps), 0) if caps else None

    signals.sort(key=lambda s: LEVEL_ORDER.index(s.level))
    headline = next((s.title for s in signals if s.level != "info"), NO_RISK_HEADLINE)
    return RiskReport(headline, signals, ratio, limit, target)
