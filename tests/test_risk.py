from housing.building import BuildingInfo
from housing.market import MarketEstimate
from housing.official_price import OfficialPrice
from housing.risk import assess_risk

residential = BuildingInfo("공동주택", "다세대주택", 9, 2003, True)
shop = BuildingInfo("제2종근린생활시설", "", 0, 2015, False)
NO_RISK = "확인된 위험 신호가 없어요. 단, 등기부등본은 꼭 직접 확인하세요."


def market(won):
    return MarketEstimate(won, 3, "same_jibun", 24)


def codes(r):
    return {s.code for s in r.signals}


def test_guarantee_blocked_example():
    r = assess_risk(220_000_000, 0, OfficialPrice(160_000_000, 2026, "x"), None, residential)
    assert r.guarantee_limit_won == 201_600_000
    assert r.signals[0].code == "GUARANTEE_BLOCKED" and "1,840만" in r.signals[0].title
    assert r.headline == r.signals[0].title
    assert r.target_deposit_won == 201_600_000


def test_senior_debt_reduces_limit():
    r = assess_risk(150_000_000, 60_000_000, OfficialPrice(160_000_000, 2026, "x"), None, residential)
    assert r.guarantee_limit_won == 141_600_000 and "GUARANTEE_BLOCKED" in codes(r)


def test_ratio_thresholds():
    assert codes(assess_risk(80_000_000, 0, None, market(100_000_000), residential)) >= {"HIGH_RATIO"}
    assert codes(assess_risk(70_000_000, 0, None, market(100_000_000), residential)) >= {"CAUTION_RATIO"}
    assert not codes(assess_risk(69_000_000, 0, None, market(100_000_000), residential)) & {"HIGH_RATIO", "CAUTION_RATIO"}


def test_target_is_min_of_limit_and_70pct():
    r = assess_risk(150_000_000, 0, OfficialPrice(160_000_000, 2026, "x"), market(200_000_000), residential)
    assert r.target_deposit_won == min(201_600_000, 140_000_000)


def test_unknown_debt_adds_registry_caution():
    assert "CHECK_REGISTRY" in codes(assess_risk(1, None, None, None, residential))


def test_zero_deposit_monthly_rent_has_no_deposit_risk():
    r = assess_risk(0, None, OfficialPrice(1, 2026, "x"), market(1), residential)
    assert not codes(r) & {"GUARANTEE_BLOCKED", "HIGH_RATIO", "CAUTION_RATIO"}
    assert r.target_deposit_won is None


def test_no_risk_headline_never_says_safe():
    r = assess_risk(50_000_000, 0, OfficialPrice(200_000_000, 2026, "x"), market(300_000_000), residential)
    assert r.headline == NO_RISK
    assert "안전" not in r.headline


def test_not_residential_is_danger():
    r = assess_risk(50_000_000, 0, OfficialPrice(200_000_000, 2026, "x"), market(300_000_000), shop)
    assert r.signals[0].code == "NOT_RESIDENTIAL" and r.signals[0].level == "danger"


def test_missing_data_are_info():
    r = assess_risk(100_000_000, 0, None, None, residential)
    levels = {s.code: s.level for s in r.signals}
    assert levels == {"NO_OFFICIAL_PRICE": "info", "NO_MARKET_DATA": "info"}
    assert r.headline == NO_RISK and r.target_deposit_won is None


def test_signals_sorted_danger_caution_info():
    r = assess_risk(90_000_000, None, None, market(100_000_000), shop)
    order = [s.level for s in r.signals]
    assert order == sorted(order, key=["danger", "caution", "info"].index)
