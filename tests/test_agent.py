import json

import pytest
import requests

from housing import agent
from housing.building import BuildingInfo
from housing.errors import DataSourceError
from housing.market import MarketEstimate
from housing.official_price import OfficialPrice, Unit
from housing.pipeline import Diagnosis
from housing.policies import Profile, load_policies, match_policies
from housing.risk import assess_risk
from tests.helpers import FakeResponse
from tests.test_building import BONGCHEON


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "secret-gemini-key")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)  # 개발자 .env에 있어도 테스트는 Gemini만


def fake_diagnosis(*args, **kwargs):
    official = OfficialPrice(281_000_000, 2026, "브이월드 공동주택가격")
    mkt = MarketEstimate(238_449_000, 51, "same_dong", 24)  # 만 원 아래 자투리: 반올림과 버림이 갈린다
    jeonse = MarketEstimate(183_427_000, 8, "same_dong", 12, "rh_rent")
    bld = BuildingInfo("공동주택", "다세대주택", 9, 2003, True)
    risk = assess_risk(220_000_000, 0, official, mkt, bld, jeonse=jeonse)
    prof = args[7]
    return Diagnosis(BONGCHEON, bld, official, mkt, risk, match_policies(prof, load_policies(), "11"), {}, [], jeonse)


@pytest.fixture
def sources(monkeypatch):
    seen = {}
    monkeypatch.setattr(agent, "search_address", lambda q, limit=10: [BONGCHEON])
    monkeypatch.setattr(agent, "list_units", lambda addr: [Unit("", "101", 30.0), Unit("", "201", 41.01)])

    def diag(*args, **kwargs):
        seen["args"] = args
        return fake_diagnosis(*args, **kwargs)

    monkeypatch.setattr(agent, "diagnose", diag)
    return seen


def test_diagnose_tool_finds_area_from_unit_and_reports_code_numbers(sources):
    out = agent.run_tool("diagnose_house", json.dumps({"address": "봉천동 1-70", "ho": "201호", "deposit_man": 22000}))
    args = sources["args"]
    assert (args[2], args[3], args[4], args[5], args[6]) == ("201", 41.01, 220_000_000, 0, None)  # 모르는 근저당은 None
    assert out["headline"].startswith("보증금이 추정 시세의 92%")
    # 화면(risk.man)처럼 만 원 미만은 버린다: 반올림하면 AI 답이 화면과 1만 원 어긋나고 목표 보증금을 넘는다
    assert out["estimated_market_price_man"] == 23844 and out["nearby_jeonse_price_man"] == 18342
    assert out["safe_deposit_target_man"] == 16691  # 238,449,000 × 70% = 166,914,300원 → 화면도 1억 6,691만
    assert {s["title"] for s in out["signals"]} >= {"보증금이 주변 전세 시세보다 20% 높아요"}
    assert "policies" not in out and "나이" in out["policies_note"]  # 나이·소득을 모르면 정책을 지어내지 않는다


def test_diagnose_tool_lists_policies_when_age_and_income_given(sources):
    out = agent.run_tool("diagnose_house", json.dumps(
        {"address": "봉천동 1-70", "ho": "201", "deposit_man": 22000, "age": 27, "income_man": 3000}))
    assert sources["args"][7].age == 27 and sources["args"][7].income_won == 30_000_000
    assert any(p["status"] == "조건 충족" for p in out["policies"])


def test_unknown_unit_without_area_asks_for_area(sources):
    out = agent.run_tool("diagnose_house", json.dumps({"address": "봉천동 1-70", "ho": "999", "deposit_man": 22000}))
    assert "전용면적" in out["error"] and "args" not in sources


def test_tool_errors_are_returned_not_raised(sources, monkeypatch):
    def boom(q, limit=10):
        raise DataSourceError("주소 검색 실패 (Timeout)")
    monkeypatch.setattr(agent, "search_address", boom)
    assert "주소 검색 실패" in agent.run_tool("search_address", json.dumps({"query": "봉천동"}))["error"]
    assert "error" in agent.run_tool("diagnose_house", "{not json")
    assert "error" in agent.run_tool("no_such_tool", "{}")


def chat_response(message):
    return FakeResponse(json.dumps({"choices": [{"message": message, "finish_reason": "stop"}]}))


def test_reply_runs_tools_until_answer(sources, monkeypatch):
    call = {"id": "c1", "type": "function", "function": {"name": "diagnose_house",
            "arguments": json.dumps({"address": "봉천동 1-70", "ho": "201", "deposit_man": 22000})},
            "extra_content": {"google": {"thought_signature": "sig"}}}
    replies = [chat_response({"role": "assistant", "content": None, "tool_calls": [call]}),
               chat_response({"role": "assistant", "content": "이 집은 깡통전세 위험이 커요."})]
    sent = []

    def post(url, headers=None, json=None, timeout=None):
        sent.append((url, headers, json))
        return replies.pop(0)

    monkeypatch.setattr(agent.requests, "post", post)
    text, history = agent.reply([{"role": "user", "content": "봉천동 1-70 201호 전세 2억2천 괜찮아?"}])
    assert text == "이 집은 깡통전세 위험이 커요."
    assert [m["role"] for m in history] == ["user", "assistant", "tool", "assistant"]
    assert history[1]["tool_calls"][0]["extra_content"]  # Gemini 사고 서명은 그대로 돌려줘야 한다
    assert "92%" in history[2]["content"] and history[2]["tool_call_id"] == "c1"
    url, headers, body = sent[0]
    assert headers["Authorization"] == "Bearer secret-gemini-key" and "secret-gemini-key" not in url
    assert body["messages"][0]["role"] == "system" and {t["function"]["name"] for t in body["tools"]} == {"search_address", "diagnose_house"}


def test_api_errors_hide_key(monkeypatch):
    def post(url, headers=None, json=None, timeout=None):
        return FakeResponse('{"error": {"message": "quota"}}', 429)
    monkeypatch.setattr(agent.requests, "post", post)
    with pytest.raises(agent.AgentError, match="한도") as e:
        agent.reply([{"role": "user", "content": "안녕"}])
    assert "secret-gemini-key" not in str(e.value)

    def leak(url, headers=None, json=None, timeout=None):
        raise requests.ConnectionError(f"failed with {headers['Authorization']}")
    monkeypatch.setattr(agent.requests, "post", leak)
    with pytest.raises(agent.AgentError) as e:
        agent.reply([{"role": "user", "content": "안녕"}])
    assert "secret-gemini-key" not in str(e.value)


def test_runaway_tool_loop_stops(sources, monkeypatch):
    call = {"id": "c", "type": "function", "function": {"name": "search_address", "arguments": '{"query": "봉천동"}'}}
    monkeypatch.setattr(agent.requests, "post", lambda *a, **k: chat_response({"role": "assistant", "tool_calls": [call]}))
    text, _ = agent.reply([{"role": "user", "content": "x"}])
    assert "다시" in text


def test_available_only_with_key(monkeypatch):
    assert agent.available()
    monkeypatch.delenv("GEMINI_API_KEY")
    assert not agent.available()


def test_overloaded_model_falls_back_to_next(monkeypatch):
    models = []

    def post(url, headers=None, json=None, timeout=None):
        models.append(json["model"])
        if len(models) == 1:
            return FakeResponse('{"error": {"code": 503, "message": "high demand"}}', 503)
        return chat_response({"role": "assistant", "content": "안녕하세요"})

    monkeypatch.setattr(agent.requests, "post", post)
    assert agent.reply([{"role": "user", "content": "안녕"}])[0] == "안녕하세요"
    assert len(models) == 2 and models[0] != models[1]


def test_youth_list_left_out_when_age_unknown(sources):
    """나이를 모르면 임시 나이(26세)로 거른 목록이 되므로 AI에게 주지 않는다."""
    out = agent.run_tool("diagnose_house", json.dumps({"address": "봉천동 1-70", "ho": "201", "deposit_man": 22000}))
    assert "other_local_youth_housing_policies" not in out
    out = agent.run_tool("diagnose_house", json.dumps(
        {"address": "봉천동 1-70", "ho": "201", "deposit_man": 22000, "age": 36, "income_man": 3000}))
    assert "other_local_youth_housing_policies" in out


def test_unexpected_tool_failure_becomes_error_for_ai(sources, monkeypatch):
    def broken(*a, **k):
        raise FileNotFoundError("rh_11620_202501.tmp")
    monkeypatch.setattr(agent, "diagnose", broken)
    out = agent.run_tool("diagnose_house", json.dumps({"address": "봉천동 1-70", "ho": "201", "deposit_man": 22000}))
    assert "조회" in out["error"] and ".tmp" not in out["error"]  # 내부 경로는 보이지 않게


def test_daily_quota_on_one_model_moves_to_next(monkeypatch):
    """Gemini 무료 등급은 모델마다 하루 20회라, 한 모델이 다 쓰이면 다음 모델로 넘어간다."""
    models = []

    def post(url, headers=None, json=None, timeout=None):
        models.append(json["model"])
        if len(models) == 1:
            return FakeResponse('{"error": {"code": 429}}', 429)
        return chat_response({"role": "assistant", "content": "안녕하세요"})

    monkeypatch.setattr(agent.requests, "post", post)
    assert agent.reply([{"role": "user", "content": "안녕"}])[0] == "안녕하세요" and len(models) == 2


def test_free_quota_used_up_moves_to_groq_with_plain_messages(monkeypatch):
    """Gemini 하루 한도가 다 차면 Groq으로 넘어간다. Groq은 Gemini 전용 필드를 받으면 400이라 빼고 보낸다."""
    monkeypatch.setenv("GROQ_API_KEY", "secret-groq-key")
    sent = []

    def post(url, headers=None, json=None, timeout=None):
        sent.append((url, headers, json))
        if "googleapis" in url:
            return FakeResponse('{"error": {"code": 429}}', 429)
        return chat_response({"role": "assistant", "content": "안녕하세요", "reasoning": "생각"})

    monkeypatch.setattr(agent.requests, "post", post)
    earlier = [{"role": "user", "content": "봉천동"},
               {"role": "assistant", "content": None, "extra_content": {"google": {}}, "tool_calls": [
                   {"id": "c", "type": "function", "function": {"name": "search_address", "arguments": "{}"},
                    "extra_content": {"google": {"thought_signature": "sig"}}}]},
               {"role": "tool", "tool_call_id": "c", "content": "{}"},
               {"role": "assistant", "content": "어느 집인가요?", "reasoning": "x"}]
    text, history = agent.reply([*earlier, {"role": "user", "content": "1번"}])
    assert text == "안녕하세요"
    url, headers, body = sent[-1]
    assert "groq" in url and headers["Authorization"] == "Bearer secret-groq-key"
    assert "extra_content" not in str(body["messages"]) and "reasoning" not in str(body["messages"])
    assert history[1]["tool_calls"][0]["extra_content"]  # 기록은 그대로 둬서 다음에 Gemini가 쓸 수 있게


def test_available_with_only_groq_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    assert agent.available()


def test_polish_keeps_numbers_and_sends_no_tools(monkeypatch):
    sent = []

    def post(url, headers=None, json=None, timeout=None):
        sent.append(json)
        return chat_response({"role": "assistant", "content": "안녕하세요! 보증금을 1억 6,600만 원으로, 월세는 22.5만 원으로 부탁드려요."})

    monkeypatch.setattr(agent.requests, "post", post)
    out = agent.polish("보증금을 1억 6,600만 원으로 낮추고 월세를 22.5만 원으로 해 주세요.")
    assert "1억 6,600만" in out and "tools" not in sent[0]


def test_polish_rejects_changed_numbers(monkeypatch):
    monkeypatch.setattr(agent.requests, "post", lambda *a, **k: chat_response(
        {"role": "assistant", "content": "보증금을 1억 6,000만 원으로 해 주세요."}))
    with pytest.raises(agent.AgentError, match="숫자"):
        agent.polish("보증금을 1억 6,600만 원으로 낮춰 주세요.")


def test_polish_rejects_added_numbers(monkeypatch):
    monkeypatch.setattr(agent.requests, "post", lambda *a, **k: chat_response(
        {"role": "assistant", "content": "보증금을 1억 6,600만 원으로 낮추고 관리비 10만 원도 부탁드려요."}))
    with pytest.raises(agent.AgentError, match="숫자"):
        agent.polish("보증금을 1억 6,600만 원으로 낮춰 주세요.")


def test_slow_gemini_moves_to_groq_without_trying_other_gemini_models(monkeypatch):
    """Gemini가 응답하지 않으면(시간 초과) 남은 Gemini 모델로 또 기다리지 않고 바로 Groq로 넘어간다."""
    monkeypatch.setenv("GROQ_API_KEY", "secret-groq-key")
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append((url, timeout))
        if "googleapis" in url:
            raise requests.Timeout("read timed out")
        return chat_response({"role": "assistant", "content": "안녕하세요"})

    monkeypatch.setattr(agent.requests, "post", post)
    text, _ = agent.reply([{"role": "user", "content": "안녕"}])
    assert text == "안녕하세요"
    assert [u for u, _ in calls if "googleapis" in u] == [calls[0][0]]  # Gemini는 한 번만
    assert max(max(t) if isinstance(t, tuple) else t for _, t in calls) <= 30  # 화면이 90초씩 멈추지 않게


def test_answer_with_hanja_is_rewritten_in_hangul(monkeypatch):
    answers = iter(["보증금이 시세의 92%라 慎重하게 보세요.", "보증금이 시세의 92%라 신중하게 보세요."])
    sent = []

    def post(url, headers=None, json=None, timeout=None):
        sent.append(json)
        return chat_response({"role": "assistant", "content": next(answers)})

    monkeypatch.setattr(agent.requests, "post", post)
    text, history = agent.reply([{"role": "user", "content": "괜찮아?"}])
    assert text == "보증금이 시세의 92%라 신중하게 보세요."
    assert "tools" not in sent[1]  # 다시 쓰기는 도구 없이
    assert history[-1]["content"] == text  # 다음 대화에도 고친 답이 남게


def test_rewrite_that_changes_numbers_keeps_original(monkeypatch):
    answers = iter(["시세의 92%라 慎重하게 보세요.", "시세의 90%라 신중하게 보세요."])
    monkeypatch.setattr(agent.requests, "post", lambda *a, **k: chat_response({"role": "assistant", "content": next(answers)}))
    text, _ = agent.reply([{"role": "user", "content": "괜찮아?"}])
    assert "92%" in text  # 숫자를 바꾼 다시 쓰기는 버린다


def test_after_groq_takes_over_gemini_is_not_asked_again_in_same_reply(monkeypatch):
    """Groq이 도구를 부른 뒤 Gemini로 돌아가면 Gemini가 사고 서명 없는 기록을 400으로 거부한다."""
    monkeypatch.setenv("GROQ_API_KEY", "secret-groq-key")
    monkeypatch.setattr(agent, "run_tool", lambda name, args: {"ok": True})
    groq = iter([{"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "search_address", "arguments": "{}"}}]},
        {"role": "assistant", "content": "결론이에요"}])
    asked = []

    def post(url, headers=None, json=None, timeout=None):
        asked.append(json["model"])
        if "googleapis" in url:
            return FakeResponse("{}", 400 if any(m.get("tool_calls") for m in json["messages"]) else 503)
        return chat_response(next(groq))

    monkeypatch.setattr(agent.requests, "post", post)
    text, _ = agent.reply([{"role": "user", "content": "봉천동 1-70"}])
    assert text == "결론이에요"
    assert asked.count("gemini-3.6-flash") == 1  # 실패한 모델을 단계마다 다시 기다리지 않는다


def test_gemini_rejecting_earlier_groq_history_falls_back(monkeypatch):
    """이전 질문을 Groq이 답했으면 다음 질문에서 Gemini가 400을 낼 수 있다 → 다음 모델로."""
    monkeypatch.setenv("GROQ_API_KEY", "secret-groq-key")

    def post(url, headers=None, json=None, timeout=None):
        if "googleapis" in url:
            return FakeResponse("{}", 400)
        return chat_response({"role": "assistant", "content": "안녕하세요"})

    monkeypatch.setattr(agent.requests, "post", post)
    assert agent.reply([{"role": "user", "content": "안녕"}])[0] == "안녕하세요"


def test_groq_request_fits_free_output_token_limit(monkeypatch):
    """Groq 무료 등급은 분당 출력 토큰 1,000개 — 상한 없이 보내면 요청 자체를 429로 거절한다(2026-11-18 배포판)."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "secret-groq-key")
    sent = []

    def post(url, headers=None, json=None, timeout=None):
        sent.append(json)
        return chat_response({"role": "assistant", "content": "안녕하세요"})

    monkeypatch.setattr(agent.requests, "post", post)
    agent.reply([{"role": "user", "content": "안녕"}])
    assert 0 < sent[0]["max_completion_tokens"] <= 600  # 1,000에서 직전 요청분을 빼고도 들어가게


def test_fastest_gemini_model_is_tried_first():
    """2026-11-18 측정: 3-flash-preview 2.5초, 3.6-flash 20초, 3.5-flash 60초 초과 — 시간 초과면 남은 Gemini를 건너뛰니 빠른 모델이 먼저."""
    assert agent.PROVIDERS[0][2][0] == "gemini-3-flash-preview"


def test_groq_short_rate_wait_is_retried_once(monkeypatch):
    """Groq은 분당 토큰이 잠깐 모자라면 retry-after(초)를 준다 — 짧으면 기다렸다 한 번 더 부른다."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "secret-groq-key")
    slept, answers = [], [FakeResponse('{"error": {}}', 429), chat_response({"role": "assistant", "content": "안녕하세요"})]
    answers[0].headers = {"retry-after": "2"}
    monkeypatch.setattr(agent.requests, "post", lambda *a, **k: answers.pop(0))
    monkeypatch.setattr(agent.time, "sleep", slept.append)
    assert agent.reply([{"role": "user", "content": "안녕"}])[0] == "안녕하세요"
    assert slept == [2.0]


def test_groq_long_rate_wait_is_not_waited(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "secret-groq-key")
    resp = FakeResponse('{"error": {}}', 429)
    resp.headers = {"retry-after": "40"}
    monkeypatch.setattr(agent.requests, "post", lambda *a, **k: resp)
    monkeypatch.setattr(agent.time, "sleep", lambda s: pytest.fail("40초나 기다림"))
    with pytest.raises(agent.AgentError, match="한도"):
        agent.reply([{"role": "user", "content": "안녕"}])


def test_groq_polish_turns_off_reasoning(monkeypatch):
    """2026-11-19 측정: qwen이 생각에 600토큰을 다 써서 다듬은 글이 빈 채로 오곤 했다(finish_reason=length).
    도구 없이 글만 다듬을 때는 생각을 끄면 100토큰 안팎으로 바로 답한다."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "secret-groq-key")
    sent = []

    def post(url, headers=None, json=None, timeout=None):
        sent.append(json)
        return chat_response({"role": "assistant", "content": "보증금 92%"})

    monkeypatch.setattr(agent.requests, "post", post)
    agent.polish("보증금 92%")
    assert sent[0]["reasoning_effort"] == "none"


def test_system_prompt_forbids_made_up_support_names():
    """2026-11-19: Groq이 도구 결과에 없는 '청년월세 지원'을 전세 세입자에게 안내했다."""
    assert "도구 결과에 있는 지원만" in agent.SYSTEM
