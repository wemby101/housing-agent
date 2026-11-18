from datetime import date

import pytest

from housing import pipeline
from housing.building import BuildingInfo
from housing.errors import DataSourceError
from housing.market import MarketEstimate
from housing.policies import Profile
from tests.test_building import BONGCHEON

PROFILE = Profile(age=26, income_won=32_000_000, married=False, homeless=True, deposit_won=220_000_000, monthly_rent_won=0)


def boom(*a, **k):
    raise DataSourceError("키가 아직 활성화되지 않았어요")


@pytest.fixture
def sources(monkeypatch):
    seen = {}
    monkeypatch.setattr(pipeline, "get_building", lambda addr: BuildingInfo("공동주택", "다세대주택", 9, 2003, True))
    monkeypatch.setattr(pipeline, "get_official_price", boom)
    monkeypatch.setattr(pipeline, "get_youth_policies", lambda zip_cd, age: [])

    def market(addr, area, year, today, kind, months=24):
        seen.setdefault("kinds", []).append(kind)
        if kind.endswith("_rent"):
            return MarketEstimate(200_000_000, 7, "same_dong", months, kind)
        seen["market_args"] = (area, year, today)
        seen["kind"] = kind
        return MarketEstimate(250_000_000, 3, "same_jibun", 24)

    monkeypatch.setattr(pipeline, "get_market_price", market)
    return seen


def run(**kw):
    args = dict(address=BONGCHEON, dong="", ho="201", area_m2=40.0, deposit_won=220_000_000, monthly_rent_won=0,
                senior_debt_won=0, profile=PROFILE, today=date(2026, 11, 16))
    return pipeline.diagnose(**{**args, **kw})


def test_market_failure_does_not_block_other_results(sources, monkeypatch):
    monkeypatch.setattr(pipeline, "get_market_price", boom)
    d = run(manual_official_won=160_000_000)
    assert "market" in d.errors and d.market is None
    assert "NO_MARKET_DATA" not in {s.code for s in d.risk.signals}  # 오류 카드가 대신 설명
    assert d.risk.guarantee_limit_won == 201_600_000
    assert "guarantee_fee" in {m.policy["id"] for m in d.policies}


def test_manual_official_price_used_when_given(sources):
    d = run(manual_official_won=160_000_000)
    assert d.official.source == "사용자 입력" and d.official.year == 2026
    assert "official" not in d.errors


def test_official_failure_recorded_without_manual(sources):
    d = run()
    assert d.official is None and "official" in d.errors
    assert "NO_OFFICIAL_PRICE" in {s.code for s in d.risk.signals}


def test_building_year_feeds_market(sources):
    run()
    assert sources["market_args"] == (40.0, 2003, date(2026, 11, 16))


def test_building_failure_passes_flag(sources, monkeypatch):
    monkeypatch.setattr(pipeline, "get_building", boom)
    d = run(manual_official_won=160_000_000)
    assert "building" in d.errors and "NO_BUILDING" not in {s.code for s in d.risk.signals}


def test_registry_flags_reach_risk(sources):
    d = run(manual_official_won=160_000_000, registry_flags=("가압류",))
    assert "REGISTRY_FLAGS" in {s.code for s in d.risk.signals}


def test_villa_uses_villa_trades(sources):
    run()
    assert sources["kind"] == "rh"


def test_apartment_uses_apartment_trades(sources, monkeypatch):
    monkeypatch.setattr(pipeline, "get_building", lambda addr: BuildingInfo("공동주택", "아파트", 300, 2010, True))
    run()
    assert sources["kind"] == "apt"


def test_youth_policies_attached_and_failure_isolated(sources, monkeypatch):
    from housing.youth import YouthPolicy
    p = YouthPolicy("인품 자립주택 운영", "인천광역시", "지원 내용", "상시", "https://x")
    monkeypatch.setattr(pipeline, "get_youth_policies", lambda zip_cd, age: [p] if zip_cd == "11620" and age == 26 else [])
    assert run().youth == [p]
    monkeypatch.setattr(pipeline, "get_youth_policies", boom)
    d = run()
    assert d.youth == [] and "youth" in d.errors and d.policies  # 정책표 매칭은 그대로


def test_building_failure_guesses_apartment_from_same_lot_trades(sources, monkeypatch):
    """건축물대장이 실패해도 같은 지번에 아파트 거래가 있으면 아파트로 본다 (송도 시연 중 실제로 겪음)."""
    monkeypatch.setattr(pipeline, "get_building", boom)
    kinds = []

    def market(addr, area, year, today, kind, months=24):
        kinds.append(kind)
        return MarketEstimate(800_000_000, 92, "same_jibun", 24, kind) if kind == "apt" else None

    monkeypatch.setattr(pipeline, "get_market_price", market)
    d = run(manual_official_won=400_000_000, deposit_won=600_000_000)
    assert kinds == ["apt", "apt_rent"] and d.market.kind == "apt"
    assert next(s for s in d.risk.signals if s.code == "GUARANTEE_BLOCKED").level == "caution"


def test_building_failure_falls_back_to_villa_without_same_lot_apartment_trades(sources, monkeypatch):
    monkeypatch.setattr(pipeline, "get_building", boom)
    kinds = []

    def market(addr, area, year, today, kind, months=24):
        kinds.append(kind)
        return MarketEstimate(250_000_000, 5, "same_dong", 24, kind)  # 같은 동 아파트 거래는 빌라 근거가 안 된다

    monkeypatch.setattr(pipeline, "get_market_price", market)
    d = run(manual_official_won=160_000_000)
    assert kinds == ["apt", "rh", "rh_rent"] and d.market.kind == "rh"


def test_jeonse_compared_only_for_jeonse_contracts(sources):
    d = run()
    assert d.jeonse.price_won == 200_000_000 and sources["kinds"][-1] == "rh_rent"
    sources["kinds"].clear()
    d = run(monthly_rent_won=500_000)
    assert d.jeonse is None and "rh_rent" not in sources["kinds"]


def test_jeonse_failure_recorded_separately(sources, monkeypatch):
    real = pipeline.get_market_price

    def market(addr, area, year, today, kind, months=24):
        if kind.endswith("_rent"):
            raise DataSourceError("공공데이터 호출 실패 (HTTP 403)")
        return real(addr, area, year, today, kind, months)

    monkeypatch.setattr(pipeline, "get_market_price", market)
    d = run()
    assert "jeonse" in d.errors and "market" not in d.errors and d.market is not None


def test_what_if_recalculates_with_same_house_data(sources):
    """보증금만 바꿔 다시 계산한다. 공공데이터는 다시 부르지 않고, 근저당·등기부 기록은 그대로 쓴다."""
    d = run(senior_debt_won=20_000_000, registry_flags=("압류",))
    calls = len(sources["kinds"])
    assert "HIGH_RATIO" in {s.code for s in d.risk.signals}  # (2.2억 + 0.2억) ÷ 2.5억 = 96%
    lower = pipeline.what_if(d, 150_000_000, 0)
    assert len(sources["kinds"]) == calls
    assert lower.deposit_ratio == pytest.approx(0.68)  # (1.5억 + 0.2억) ÷ 2.5억
    codes = {s.code for s in lower.signals}
    assert "HIGH_RATIO" not in codes and "CAUTION_RATIO" not in codes and "REGISTRY_FLAGS" in codes
    assert lower.target_deposit_won == d.risk.target_deposit_won


def test_what_if_monthly_rent_drops_jeonse_comparison(sources):
    d = run()
    assert "JEONSE_NEARBY" in {s.code for s in d.risk.signals}  # 2.2억 ÷ 주변 전세 2억 = 110%
    assert not {"JEONSE_ABOVE_NEARBY", "JEONSE_NEARBY"} & {s.code for s in pipeline.what_if(d, 150_000_000, 300_000).signals}


def test_rent_for_lowered_deposit_uses_conversion_rate():
    # 5,000만 원을 연 5%로 바꾸면 월 208,333원 → 천 원 단위
    assert pipeline.rent_for_cut(50_000_000, 5.0) == 208_000
    assert pipeline.rent_for_cut(0, 5.0) == 0


def test_without_my_info_policies_are_skipped_not_guessed(sources, monkeypatch):
    """나이·소득을 안 넣으면 임시 값으로 정책을 고르지 않는다. 위험 진단은 그대로 한다."""
    monkeypatch.setattr(pipeline, "get_youth_policies", boom)  # 부르면 안 된다
    d = run(profile=None)
    assert d.policies == [] and d.youth == [] and not d.policies_checked
    assert "youth" not in d.errors and d.risk.deposit_ratio is not None
    assert run().policies_checked


def test_monthly_interest_on_loan():
    assert pipeline.monthly_interest(150_000_000, 2.8) == 350_000  # 1억 5천 × 2.8% ÷ 12
    assert pipeline.monthly_interest(0, 2.8) == 0


def test_default_youth_loan_is_80_percent_up_to_limit():
    assert pipeline.default_loan(150_000_000) == 120_000_000
    assert pipeline.default_loan(220_000_000) == 150_000_000  # 청년전용 버팀목 한도


def test_building_failure_hides_market_estimated_without_build_year(sources, monkeypatch):
    """건축물대장 실패로 건축년도를 모른 채 고른 같은 동 거래는 새 건물이 섞여 시세가 부푼다(연남동 +34%).
    보증금이 안전해 보이는 틀린 숫자 대신 다시 진단하라고 알린다."""
    monkeypatch.setattr(pipeline, "get_building", boom)

    def market(addr, area, year, today, kind, months=24):
        return None if kind == "apt" else MarketEstimate(320_000_000, 42, "same_dong", 24, kind, year_known=False)

    monkeypatch.setattr(pipeline, "get_market_price", market)
    d = run(manual_official_won=160_000_000)
    assert d.market is None and d.jeonse is None
    assert "건축년도" in d.errors["market"] and "다시" in d.errors["market"]


def test_same_lot_estimate_kept_when_building_fails(sources, monkeypatch):
    monkeypatch.setattr(pipeline, "get_building", boom)
    monkeypatch.setattr(pipeline, "get_market_price",
                        lambda addr, area, year, today, kind, months=24: MarketEstimate(250_000_000, 3, "same_jibun", 24, kind, year_known=False))
    assert run(manual_official_won=160_000_000).market.price_won == 250_000_000
