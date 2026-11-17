"""app.py 입력 처리 — 데이터 출처는 가짜로 바꾸고 Streamlit AppTest로 실행."""
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import housing.address
import housing.official_price
import housing.pipeline
from housing.pipeline import Diagnosis
from housing.risk import RiskReport
from tests.test_building import BONGCHEON

APP = str(Path(__file__).resolve().parent.parent / "app.py")
DEPOSIT, RENT, DEBT = 1, 2, 3  # number_input 순서: 면적, 보증금, 월세, 선순위, 공시가격, 나이, 소득


@pytest.fixture
def calls(monkeypatch):
    st.cache_data.clear()  # AppTest끼리 같은 프로세스 캐시를 공유한다
    seen = []

    def fake_diagnose(*args, **kwargs):
        seen.append((args, kwargs))
        return Diagnosis(BONGCHEON, None, None, None, RiskReport("h", [], None, None, None), [], {"market": "데이터를 가져오지 못했어요"},
                         policies_checked=args[7] is not None)

    monkeypatch.setattr(housing.address, "search_address", lambda kw, limit=10: [BONGCHEON])
    monkeypatch.setattr(housing.pipeline, "diagnose", fake_diagnose)
    monkeypatch.setattr(housing.official_price, "list_units", lambda addr: [])
    return seen


def submit(deposit=10000, rent=0, debt=None):
    at = AppTest.from_file(APP, default_timeout=10)
    at.run()
    at.text_input[0].input("봉천동 1-70").run()
    at.selectbox[0].select(BONGCHEON).run()
    at.number_input[DEPOSIT].set_value(deposit)
    at.number_input[RENT].set_value(rent)
    if debt is not None:
        at.number_input[DEBT].set_value(debt)
    at.button[0].click().run()
    return at


def test_typed_debt_counts_even_with_unknown_checked(calls):
    submit(debt=10000)
    args, _ = calls[-1]
    assert args[6] == 100_000_000


def test_unknown_debt_with_zero_input_is_none(calls):
    submit()
    assert calls[-1][0][6] is None


def test_zero_deposit_and_rent_is_not_diagnosed(calls):
    at = submit(deposit=0, rent=0)
    assert calls == [] and any("보증금" in w.value for w in at.warning)


def test_diagnosis_with_source_errors_is_not_cached(calls):
    at = submit()
    at.button[0].click().run()
    assert len(calls) == 2


def test_full_result_renders(monkeypatch):
    from datetime import date
    from housing.building import BuildingInfo
    from housing.market import MarketEstimate
    from housing.official_price import OfficialPrice
    from housing.policies import Profile, load_policies, match_policies
    from housing.risk import assess_risk

    st.cache_data.clear()
    official, mkt = OfficialPrice(160_000_000, 2026, "사용자 입력"), MarketEstimate(250_000_000, 3, "same_jibun", 24)
    bld = BuildingInfo("공동주택", "다세대주택", 9, 2003, True)
    prof = Profile(26, 30_000_000, False, True, 220_000_000, 0)
    full = Diagnosis(BONGCHEON, bld, official, mkt, assess_risk(220_000_000, 50_000_000, official, mkt, bld),
                     match_policies(prof, load_policies()), {})
    monkeypatch.setattr(housing.address, "search_address", lambda kw, limit=10: [BONGCHEON])
    monkeypatch.setattr(housing.pipeline, "diagnose", lambda *a, **k: full)
    at = submit(deposit=22000, debt=5000)
    assert not at.exception
    text = " ".join(m.value for m in at.markdown) + " ".join(c.label for c in at.checkbox)
    assert "보증보험 가입 한도를" in text and "추정 시세 × 70% − 선순위 채권 = 12,500만 원" in text
    assert "참고용 진단입니다" in " ".join(c.value for c in at.caption)


def test_changing_address_hides_old_result(calls, monkeypatch):
    from dataclasses import replace
    other = replace(BONGCHEON, road_addr="서울특별시 관악구 다른로 1", pnu="1162010100100020000")
    at = submit()
    assert any(h.value == BONGCHEON.road_addr for h in at.subheader)
    monkeypatch.setattr(housing.address, "search_address", lambda kw, limit=10: [other])
    at.text_input[0].input("다른로 1").run()
    at.selectbox[0].select(other).run()
    assert not any(h.value == BONGCHEON.road_addr for h in at.subheader)


def test_full_page_of_results_asks_for_more_detail(calls, monkeypatch):
    from dataclasses import replace
    many = lambda kw, limit=10: [replace(BONGCHEON, road_addr=f"주소 {i}") for i in range(limit)]
    monkeypatch.setattr(housing.address, "search_address", many)
    at = AppTest.from_file(APP, default_timeout=10)
    at.run()
    at.text_input[0].input("관악구").run()
    assert any("자세히" in c.value for c in at.caption)


def test_picking_unit_fills_dong_ho_and_area(calls, monkeypatch):
    from housing.official_price import Unit
    u201 = Unit("", "201", 41.01)
    monkeypatch.setattr(housing.official_price, "list_units", lambda addr: [Unit("", "101", 30.0), u201])
    at = AppTest.from_file(APP, default_timeout=10)
    at.run()
    at.text_input[0].input("봉천동 1-70").run()
    at.selectbox[0].select(BONGCHEON).run()
    at.selectbox[1].select(u201).run()
    at.number_input[DEPOSIT].set_value(10000)
    at.button[0].click().run()
    args, _ = calls[-1]
    assert (args[1], args[2], args[3]) == ("", "201", 41.01)


LINK = {"q": "봉천동 1-70", "pnu": BONGCHEON.pnu, "ho": "201", "area": "41.0", "dep": "20000", "rent": "0", "debt": "5000"}


def test_submit_saves_house_inputs_in_link_but_not_personal_info(calls):
    at = submit(deposit=20000, debt=5000)
    qp = at.query_params
    assert qp["pnu"] == BONGCHEON.pnu and qp["dep"] == "20000" and qp["debt"] == "5000" and qp["q"] == "봉천동 1-70"
    assert not {"age", "income", "married", "homeless"} & set(qp)


def test_unknown_debt_is_left_out_of_link(calls):
    assert "debt" not in submit().query_params


def test_link_restores_diagnosis_on_open(calls):
    at = AppTest.from_file(APP, default_timeout=10)
    at.query_params.update(LINK)
    at.run()
    args, _ = calls[-1]
    assert (args[0], args[2], args[4], args[6]) == (BONGCHEON, "201", 200_000_000, 50_000_000)
    assert any(h.value == BONGCHEON.road_addr for h in at.subheader)
    assert args[7] is None and any("내 정보" in i.value for i in at.info)  # 나이·소득은 링크에 없어 정책은 빼고 안내


def test_checklist_progress_is_kept_in_link(calls):
    at = submit()
    box = next(c for c in at.checkbox if c.label.startswith("등기부등본"))
    box.check().run()
    i = [c.label for c in at.checkbox if c.label.startswith(("등기부등본", "건축물대장", "특약", "보증금 협상"))].index(box.label)
    assert at.query_params["done"] == str(i)
    reopened = AppTest.from_file(APP, default_timeout=10)
    reopened.query_params.update(at.query_params)
    reopened.run()
    assert next(c for c in reopened.checkbox if c.label == box.label).value is True


def test_registry_pdf_fills_debt_and_flags(calls, monkeypatch):
    import housing.registry
    from housing.registry import Lien, RegistryReading
    reading = RegistryReading([Lien(2, "근저당권", 84_000_000), Lien(4, "전세권", 50_000_000)], ["압류"])
    monkeypatch.setattr(housing.registry, "read_registry_pdf", lambda data: reading)
    at = AppTest.from_file(APP, default_timeout=10)
    at.run()
    at.text_input[0].input("봉천동 1-70").run()
    at.selectbox[0].select(BONGCHEON).run()
    at.file_uploader[0].set_value(("등기부.pdf", b"%PDF-fake", "application/pdf")).run()
    assert any("13,400만" in s.value for s in at.success)
    at.number_input[DEPOSIT].set_value(10000)
    at.button[0].click().run()
    args, kwargs = calls[-1]
    assert args[6] == 134_000_000 and kwargs["registry_flags"] == ("압류",)


def test_link_flags_apply_only_when_opening_link(calls):
    at = AppTest.from_file(APP, default_timeout=10)
    at.query_params.update({**LINK, "reg": "압류"})
    at.run()
    assert calls[-1][1]["registry_flags"] == ("압류",)
    at.number_input[DEPOSIT].set_value(30000)
    at.button[0].click().run()
    assert calls[-1][1]["registry_flags"] == ()


def test_result_offers_report_download(calls):
    at = submit()
    buttons = at.get("download_button")
    assert len(buttons) == 1 and "저장" in buttons[0].proto.label


def test_youth_policies_listed_separately(calls, monkeypatch):
    from housing.youth import YouthPolicy
    p = YouthPolicy("인품 자립주택 운영", "인천광역시 청년정책담당관", "보증금 일부 지원", "상시", "https://youth.incheon.go.kr/x")
    d = Diagnosis(BONGCHEON, None, None, None, RiskReport("h", [], None, None, None), [], {}, [p])
    monkeypatch.setattr(housing.pipeline, "diagnose", lambda *a, **k: d)
    at = submit()
    exp = next(e for e in at.expander if "이 지역의 다른 청년 주거 정책" in e.label)
    assert "1개" in exp.label
    text = " ".join(m.value for m in exp.markdown)
    assert "인품 자립주택 운영" in text and "https://youth.incheon.go.kr/x" in text and "온통청년" in text


def test_youth_policy_with_blank_support_does_not_crash(calls, monkeypatch):
    from housing.youth import YouthPolicy
    p = YouthPolicy("빈 설명 정책", "인천광역시", "- ", "상시", "https://www.youthcenter.go.kr")
    d = Diagnosis(BONGCHEON, None, None, None, RiskReport("h", [], None, None, None), [], {}, [p])
    monkeypatch.setattr(housing.pipeline, "diagnose", lambda *a, **k: d)
    at = submit()
    assert not at.exception


def test_chat_answers_and_keeps_history(monkeypatch):
    import housing.agent
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    asked = []

    def fake_reply(history):
        asked.append(history)
        return "깡통전세 위험이 커요.", history + [{"role": "assistant", "content": "깡통전세 위험이 커요."}]

    monkeypatch.setattr(housing.agent, "reply", fake_reply)
    at = AppTest.from_file(APP, default_timeout=10)
    at.run()
    at.chat_input[0].set_value("봉천동 1-70 201호 전세 2억2천 괜찮아?").run()
    assert asked[-1][-1] == {"role": "user", "content": "봉천동 1-70 201호 전세 2억2천 괜찮아?"}
    texts = [m.markdown[0].value for m in at.chat_message]
    assert texts == ["봉천동 1-70 201호 전세 2억2천 괜찮아?", "깡통전세 위험이 커요."]
    at.chat_input[0].set_value("월세면?").run()
    assert len(asked[-1]) == 3  # 이전 대화가 함께 간다


def test_chat_error_is_shown_not_crashing(monkeypatch):
    import housing.agent
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    def fail(history):
        raise housing.agent.AgentError("무료 AI 사용 한도에 걸렸어요.")

    monkeypatch.setattr(housing.agent, "reply", fail)
    at = AppTest.from_file(APP, default_timeout=10)
    at.run()
    at.chat_input[0].set_value("봉천동 1-70 201호 괜찮아?").run()
    assert not at.exception and any("한도" in e.value for e in at.error)
    at.run()  # 다시 그려도 방금 쓴 질문은 남아 있어 다시 칠 필요가 없다
    assert [m.markdown[0].value for m in at.chat_message] == ["봉천동 1-70 201호 괜찮아?"]
    assert at.session_state["chat"] == [{"role": "user", "content": "봉천동 1-70 201호 괜찮아?"}]


def test_chat_hidden_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    at = AppTest.from_file(APP, default_timeout=10)
    at.run()
    assert len(at.chat_input) == 0


def jeonse_result(monkeypatch):
    from dataclasses import replace
    from housing.building import BuildingInfo
    from housing.market import MarketEstimate
    from housing.official_price import OfficialPrice
    from housing.risk import assess_risk

    st.cache_data.clear()
    official, mkt = OfficialPrice(281_000_000, 2026, "브이월드"), MarketEstimate(238_450_000, 51, "same_dong", 24)
    bld = BuildingInfo("공동주택", "다세대주택", 9, 2003, True)
    full = Diagnosis(BONGCHEON, bld, official, mkt, assess_risk(220_000_000, None, official, mkt, bld), [], {},
                     deposit_won=220_000_000, monthly_rent_won=0, senior_debt_won=None)
    monkeypatch.setattr(housing.address, "search_address", lambda kw, limit=10: [BONGCHEON])
    monkeypatch.setattr(housing.pipeline, "diagnose", lambda *a, **k: full)
    return submit(deposit=22000)


def whatif_text(at):
    return " ".join(m.value for m in at.markdown)


def test_lowering_deposit_shows_new_ratio_and_monthly_rent(monkeypatch):
    at = jeonse_result(monkeypatch)
    slider = next(s for s in at.slider if "보증금" in s.label)
    assert slider.value == 22000 and slider.max == 22000
    assert "92%" in whatif_text(at)
    slider.set_value(15000).run()  # 7,000만 원을 낮추면 연 5%로 월 291,667원 → 29.2만 원
    text = whatif_text(at)
    assert "62%" in text and "29.2만 원" in text  # 15,000 ÷ 23,845 = 62.9% → 다음 구간으로 올려 쓰지 않는다
    assert not at.exception


def test_target_button_moves_slider_to_safe_deposit(monkeypatch):
    at = jeonse_result(monkeypatch)
    next(b for b in at.button if "목표" in b.label).click().run()
    slider = next(s for s in at.slider if "보증금" in s.label)
    assert slider.value == 16600  # 목표 16,691만 원을 100만 원 단위로 내림 (넘지 않게)
    assert "70%" in whatif_text(at) or "69%" in whatif_text(at)


def test_zero_deposit_in_slider_does_not_crash(monkeypatch):
    at = jeonse_result(monkeypatch)
    next(s for s in at.slider if "보증금" in s.label).set_value(0).run()
    assert not at.exception


AGE, INCOME = 5, 6


def test_my_info_starts_empty_and_skips_policies(calls):
    at = AppTest.from_file(APP, default_timeout=10)
    at.run()
    assert at.number_input[AGE].value is None and at.number_input[INCOME].value is None
    at = submit()
    assert calls[-1][0][7] is None  # 나이·소득이 없으면 프로필 없이 진단
    assert any("나이" in i.value and "소득" in i.value for i in at.info)


def test_no_income_checkbox_counts_as_zero_income(calls):
    at = AppTest.from_file(APP, default_timeout=10)
    at.run()
    at.text_input[0].input("봉천동 1-70").run()
    at.selectbox[0].select(BONGCHEON).run()
    at.number_input[DEPOSIT].set_value(10000)
    at.number_input[AGE].set_value(21)
    next(c for c in at.checkbox if "소득 없음" in c.label).check()
    at.button[0].click().run()
    profile = calls[-1][0][7]
    assert profile.age == 21 and profile.income_won == 0
