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
        return Diagnosis(args[0], None, None, None, RiskReport("h", [], None, None, None), [], {"market": "데이터를 가져오지 못했어요"},
                         deposit_won=args[4], monthly_rent_won=args[5], policies_checked=args[7] is not None)

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
                     match_policies(prof, load_policies()), {}, deposit_won=220_000_000, senior_debt_won=50_000_000)
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


def test_no_income_locks_income_at_zero(calls):
    at = AppTest.from_file(APP, default_timeout=10)
    at.run()
    at.number_input[INCOME].set_value(2500).run()
    next(c for c in at.checkbox if "소득 없음" in c.label).check().run()
    assert at.number_input[INCOME].value == 0 and at.number_input[INCOME].disabled
    next(c for c in at.checkbox if "소득 없음" in c.label).uncheck().run()
    assert not at.number_input[INCOME].disabled and at.number_input[INCOME].value is None  # 풀면 다시 직접 입력


def test_monthly_contract_has_no_switch_to_monthly_section(monkeypatch):
    """이미 월세 계약이면 '보증금을 낮추고 월세로 바꾸면'은 뜻이 없어 보여 주지 않는다."""
    from dataclasses import replace
    at = jeonse_result(monkeypatch)
    d = replace(at.session_state["diagnosis"], deposit_won=50_000_000, monthly_rent_won=1_500_000)
    monkeypatch.setattr(housing.pipeline, "diagnose", lambda *a, **k: d)
    at.number_input[DEPOSIT].set_value(5000)
    at.number_input[RENT].set_value(150)
    at.button[0].click().run()
    assert not any("월세로 바꾸면" in m.value for m in at.markdown)
    assert not any("보증금" in s.label for s in at.slider)


def test_negotiation_target_hidden_when_deposit_already_below(monkeypatch):
    """보증금 5천만 원짜리 월세에 '협상 목표 7억 이하'를 보여 주면 헷갈린다."""
    from dataclasses import replace
    at = jeonse_result(monkeypatch)
    d = at.session_state["diagnosis"]
    small = replace(d, deposit_won=50_000_000, monthly_rent_won=1_500_000)
    monkeypatch.setattr(housing.pipeline, "diagnose", lambda *a, **k: small)
    at.number_input[DEPOSIT].set_value(5000)
    at.number_input[RENT].set_value(150)
    at.button[0].click().run()
    assert not any(c.label.startswith("보증금 협상 목표") for c in at.checkbox)


def summary(at):
    return {m.label: m.value for m in at.metric}


def test_result_starts_with_three_key_numbers_and_ratio_bar(monkeypatch):
    at = jeonse_result(monkeypatch)
    s = summary(at)
    assert s["시세 대비 보증금"] == "92%" and s["협상 목표 보증금"] == "16,691만 원 이하"
    assert s["받을 수 있는 지원"] == "0만 원"  # 조건 충족 지원이 없을 때
    bar = next(m.value for m in at.markdown if 'class="risk-bar"' in m.value)
    assert "left: 92%" in bar and "시세 대비 92%" in bar  # 화면 읽기 프로그램용 설명도


def test_summary_without_market_or_my_info(calls):
    at = submit()  # 가짜 진단: 시세 없음, 나이·소득 없음
    s = summary(at)
    assert s["시세 대비 보증금"] == "-" and s["받을 수 있는 지원"] == "내 정보 필요"
    assert not any('class="risk-bar"' in m.value for m in at.markdown)


def code_blocks(at):
    return [c.value for c in at.code]


def test_contract_phrases_use_target_then_follow_slider(monkeypatch):
    at = jeonse_result(monkeypatch)
    terms, msg = code_blocks(at)[:2]
    assert "1. 임대인은 잔금일 다음 날까지" in terms
    assert "보증금을 1억 6,600만 원으로 낮추고 월세를 22.5만 원으로" in msg  # 슬라이더를 안 움직였으면 협상 목표로
    next(s for s in at.slider if "보증금" in s.label).set_value(15000).run()
    assert "보증금을 1억 5,000만 원으로 낮추고 월세를 29.2만 원으로" in code_blocks(at)[1]
    assert not at.exception


def test_ai_polish_shows_polished_message(monkeypatch):
    from housing import agent
    monkeypatch.setattr(agent, "available", lambda: True)
    monkeypatch.setattr(agent, "polish", lambda text: "다듬은 메시지 " + text)
    at = jeonse_result(monkeypatch)
    next(b for b in at.button if "다듬기" in b.label).click().run()
    assert code_blocks(at)[1].startswith("다듬은 메시지")


def test_ai_polish_failure_keeps_original(monkeypatch):
    from housing import agent

    def fail(text):
        raise agent.AgentError("AI가 숫자를 바꾸거나 빠뜨려서 원래 문장을 그대로 보여 드려요.")
    monkeypatch.setattr(agent, "available", lambda: True)
    monkeypatch.setattr(agent, "polish", fail)
    at = jeonse_result(monkeypatch)
    next(b for b in at.button if "다듬기" in b.label).click().run()
    assert code_blocks(at)[1].startswith("안녕하세요") and any("숫자" in w.value for w in at.warning)


def test_market_evidence_lists_comps(monkeypatch):
    from housing.market import MarketEstimate, Trade
    comps = (Trade(200_000_000, 40.0, 2003, "봉천동", "1-70", "202605"), Trade(240_000_000, 40.0, 2003, "봉천동", "1-70", "202609"),
             Trade(220_000_000, 40.0, 2003, "봉천동", "1-70", "202601"))
    full = Diagnosis(BONGCHEON, None, None, MarketEstimate(220_000_000, 3, "same_jibun", 24, comps=comps),
                     RiskReport("h", [], 1.0, None, None), [], {}, deposit_won=220_000_000)
    st.cache_data.clear()
    monkeypatch.setattr(housing.address, "search_address", lambda kw, limit=10: [BONGCHEON])
    monkeypatch.setattr(housing.pipeline, "diagnose", lambda *a, **k: full)
    at = submit(deposit=22000)
    ev = next(e for e in at.expander if "근거" in e.label)
    assert "2억 2,000만 원" in ev.label and "3건" in ev.label
    table = at.dataframe[0].value
    assert list(table["계약"]) == ["2026.09", "2026.05", "2026.01"] and list(table["㎡당(만원)"]) == [600.0, 500.0, 550.0]


def test_compare_collects_houses_across_diagnoses(calls):
    at = submit(deposit=10000)
    next(b for b in at.button if "비교 목록에 담기" in b.label).click().run()
    assert any(b.label == "비교 목록에 담겼어요" and b.disabled for b in at.button)
    at.number_input[DEPOSIT].set_value(15000)
    at.button[0].click().run()
    next(b for b in at.button if "비교 목록에 담기" in b.label).click().run()
    table = next(m.value for m in at.markdown if "| **조건** |" in m.value)
    assert "전세 1억 원" in table and "전세 1억 5,000만 원" in table
    next(b for b in at.button if "비우기" in b.label).click().run()
    assert not any("| **조건** |" in m.value for m in at.markdown)


def test_price_trend_chart_and_caption(monkeypatch):
    from housing.market import MarketEstimate, Trade
    comps = tuple(Trade(p, 40.0, 2003, "봉천동", "1-70", ym) for p, ym in
                  [(200_000_000, "202501"), (200_000_000, "202506"), (180_000_000, "202512"), (180_000_000, "202606")])
    est = MarketEstimate(190_000_000, 4, "same_jibun", 24, comps=comps, trend=-0.10)
    full = Diagnosis(BONGCHEON, None, None, est, RiskReport("h", [], 1.0, None, None), [], {}, deposit_won=150_000_000)
    st.cache_data.clear()
    monkeypatch.setattr(housing.address, "search_address", lambda kw, limit=10: [BONGCHEON])
    monkeypatch.setattr(housing.pipeline, "diagnose", lambda *a, **k: full)
    at = submit(deposit=15000)
    assert any("㎡당 매매가 추이" in m.value for m in at.markdown)
    assert any("10% 떨어졌어요" in c.value for c in at.caption)
    assert not at.exception


def test_loan_interest_compared_with_monthly_rent(monkeypatch):
    at = jeonse_result(monkeypatch)  # 보증금 2억 2천 → 기본 대출 1억 5천(한도), 2.8% → 월 35만 원
    loan = next(n for n in at.number_input if "대출받을 금액" in n.label)
    assert loan.value == 15000
    text = whatif_text(at)
    assert "월 대출이자 **35만 원**" in text and "슬라이더로 보증금을 낮추면" in text
    next(s for s in at.slider if "보증금" in s.label).set_value(15000).run()
    # 내 돈 7천 → 보증금 1억 5천이면 대출 8천(월 18.7만) + 월세 29.2만 = 47.9만, 1년에 155만 원쯤 더
    text = whatif_text(at)
    assert "월 대출이자 18.7만 원 + 월세 29.2만 원 = **47.9만 원**" in text and "더** 들어요" in text


def test_checklist_has_landlord_checks(monkeypatch):
    at = jeonse_result(monkeypatch)
    labels = [c.label for c in at.checkbox]
    assert any("안심전세 앱" in l for l in labels) and any("세무서" in l for l in labels)


def test_map_toggle_draws_points(monkeypatch):
    import housing.geocode
    from housing.market import MarketEstimate, Trade
    comps = (Trade(200_000_000, 40.0, 2003, "봉천동", "5-1", "202605"),) * 3
    full = Diagnosis(BONGCHEON, None, None, MarketEstimate(200_000_000, 3, "same_dong", 24, comps=comps),
                     RiskReport("h", [], 1.0, None, None), [], {}, deposit_won=200_000_000)
    st.cache_data.clear()
    monkeypatch.setattr(housing.address, "search_address", lambda kw, limit=10: [BONGCHEON])
    monkeypatch.setattr(housing.pipeline, "diagnose", lambda *a, **k: full)
    monkeypatch.setattr(housing.geocode, "comp_points", lambda addr, comps: [
        {"lat": 37.48, "lon": 126.95, "home": True, "count": 0, "label": "이 집"},
        {"lat": 37.481, "lon": 126.951, "home": False, "count": 3, "label": "5-1 · 거래 3건"}])
    at = submit(deposit=20000)
    next(t for t in at.toggle if "지도" in t.label).set_value(True).run()
    assert not at.exception
    assert len(at.get("deck_gl_json_chart")) == 1


def test_link_checklist_does_not_carry_to_another_house(calls, monkeypatch):
    from dataclasses import replace
    other = replace(BONGCHEON, pnu="1162010100100020000", road_addr="서울특별시 관악구 다른길 2", jibun_addr="서울특별시 관악구 봉천동 2")
    monkeypatch.setattr(housing.address, "search_address", lambda kw, limit=10: [BONGCHEON, other])
    at = AppTest.from_file(APP, default_timeout=10)
    at.query_params.update({**LINK, "done": "0,1,2,3,4,5"})
    at.run()
    assert any(c.value for c in at.checkbox if c.label.startswith("등기부등본"))  # 링크의 집은 체크가 살아 있다
    at.selectbox[0].select(other).run()
    at.button[0].click().run()
    assert not any(c.value for c in at.checkbox if c.label.startswith(("등기부등본", "건축물대장", "계약서에")))


@pytest.mark.parametrize("bad", [{"dep": "inf"}, {"dep": "-5"}, {"area": "0"}, {"rent": "nan"}])
def test_broken_link_numbers_do_not_crash(calls, bad):
    at = AppTest.from_file(APP, default_timeout=10)
    at.query_params.update({**LINK, **bad})
    at.run()
    assert not at.exception
