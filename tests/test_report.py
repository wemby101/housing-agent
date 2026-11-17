from datetime import date

from housing.building import BuildingInfo
from housing.official_price import OfficialPrice
from housing.pipeline import Diagnosis
from housing.policies import Profile, load_policies, match_policies
from housing.report import build_report
from housing.risk import assess_risk
from tests.test_building import BONGCHEON

DISCLAIMER = "참고용 진단입니다. 계약 전 공인중개사·HUG(1566-9009) 상담으로 확인하세요."


def diagnosis():
    official = OfficialPrice(281_000_000, 2026, "브이월드 공동주택가격")
    bld = BuildingInfo("공동주택", "다세대주택", 9, 2003, True)
    prof = Profile(26, 30_000_000, False, True, 200_000_000, 0)
    return Diagnosis(BONGCHEON, bld, official, None, assess_risk(200_000_000, 200_000_000, official, None, bld),
                     match_policies(prof, load_policies()), {"market": "데이터를 가져오지 못했어요: <HTTP 403>"})


def test_report_has_result_checklist_and_disclaimer():
    d = diagnosis()
    html = build_report(d, ["**보증금 협상 목표**", "특약 넣기"], {0}, DISCLAIMER, date(2026, 11, 16))
    assert html.startswith("<!doctype html>")
    assert BONGCHEON.road_addr in html and d.risk.headline in html and DISCLAIMER in html
    assert d.risk.signals[0].detail in html
    assert "☑ 보증금 협상 목표" in html and "☐ 특약 넣기" in html and "**" not in html
    assert "2026-11-16" in html
    assert any(m.policy["name"] in html for m in d.policies)


def test_report_escapes_text():
    html = build_report(diagnosis(), [], set(), DISCLAIMER, date(2026, 11, 16))
    assert "&lt;HTTP 403&gt;" in html and "<HTTP 403>" not in html


def test_report_lists_youth_policies():
    from dataclasses import replace
    from housing.youth import YouthPolicy
    d = replace(diagnosis(), youth=[YouthPolicy("인품 <자립>주택", "인천광역시", "지원", "상시", "https://youth.incheon.go.kr/x")])
    html = build_report(d, [], set(), DISCLAIMER, date(2026, 11, 16))
    assert "이 지역의 다른 청년 주거 정책" in html and "인품 &lt;자립&gt;주택" in html and "https://youth.incheon.go.kr/x" in html
