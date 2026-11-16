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

    def market(addr, area, year, today):
        seen["market_args"] = (area, year, today)
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
