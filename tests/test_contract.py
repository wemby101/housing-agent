from housing.building import BuildingInfo
from housing.contract import negotiation_message, special_terms, won
from housing.market import MarketEstimate
from housing.official_price import OfficialPrice
from housing.pipeline import Diagnosis
from housing.risk import assess_risk
from tests.test_building import BONGCHEON

OFFICIAL = OfficialPrice(281_000_000, 2026, "브이월드")
MARKET = MarketEstimate(238_450_000, 51, "same_dong", 24)
VILLA = BuildingInfo("공동주택", "다세대주택", 9, 2003, True)


def diag(deposit=220_000_000, rent=0, debt=None, flags=(), building=VILLA, official=OFFICIAL):
    risk = assess_risk(deposit, debt, official, MARKET, building, registry_flags=flags)
    return Diagnosis(BONGCHEON, building, official, MARKET, risk, [], {}, deposit_won=deposit, monthly_rent_won=rent,
                     senior_debt_won=debt, registry_flags=flags, ho="201")


def test_basic_terms_protect_deposit():
    terms = special_terms(diag())
    text = " ".join(terms)
    assert "담보권" in text and "반환보증" in text and "완납증명서" in text
    assert "근저당권" not in text  # 근저당이 없으면 말소 특약도 없다


def test_known_debt_and_registry_flags_get_their_own_terms():
    text = " ".join(special_terms(diag(debt=50_000_000, flags=("신탁",))))
    assert "채권최고액 합계 5,000만 원" in text and "신탁" in text


def test_mixed_use_building_gets_purpose_term():
    shop = BuildingInfo("제2종근린생활시설", "근린생활시설, 다세대주택", 5, 2010, False)
    assert any("주택이 아닌 용도" in t for t in special_terms(diag(building=shop)))


def test_no_deposit_terms_without_deposit():
    assert not any("반환보증" in t for t in special_terms(diag(deposit=0, rent=500_000)))


def test_message_proposes_lower_deposit_with_code_numbers():
    d = diag()
    msg = negotiation_message(d, 166_000_000, 225_000)
    assert "봉천동 1-70 201호" in msg  # 도로명이 없으면 지번 주소
    assert "시세의 92%" in msg and "보증보험" not in msg.split("특약")[0]  # 한도(3억 5,406만) 안이면 걱정 목록에 없다
    assert "보증금을 1억 6,600만 원으로 낮추고 월세를 22.5만 원으로" in msg
    assert all(t in msg for t in special_terms(d))  # 특약도 함께 보낸다


def test_message_mentions_guarantee_limit_when_over():
    msg = negotiation_message(diag(official=OfficialPrice(160_000_000, 2026, "브이월드")), 166_000_000, 225_000)
    assert "전세보증보험 한도(2억 160만 원)를 넘어" in msg


def test_message_without_proposal_only_asks_for_terms():
    msg = negotiation_message(diag(deposit=100_000_000))
    assert "낮추고" not in msg and "특약" in msg


def test_won_reads_like_korean_money():
    assert won(220_000_000) == "2억 2,000만 원" and won(225_000) == "22.5만 원" and won(100_000_000) == "1억 원"
    assert won(5_000_000) == "500만 원" and won(238_451_000) == "2억 3,845만 원"  # 큰 금액은 소수점 없이 (man()과 같이 내림)


def test_landlord_checks_cover_app_and_unpaid_tax():
    from housing.contract import landlord_checks
    text = " ".join(landlord_checks(diag()))
    assert "안심전세 앱" in text and "사고 이력" in text
    assert "체납" in text and "세무서" in text
    assert "신탁원부" not in text


def test_trust_registry_asks_for_trustee_consent():
    from housing.contract import landlord_checks
    text = " ".join(landlord_checks(diag(flags=("신탁",))))
    assert "신탁원부" in text and "동의서" in text


def test_small_deposit_skips_tax_viewing_without_consent():
    from housing.contract import landlord_checks
    assert not any("세무서" in c for c in landlord_checks(diag(deposit=5_000_000, rent=500_000)))
