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
    mkt = MarketEstimate(238_450_000, 51, "same_dong", 24)
    jeonse = MarketEstimate(183_420_000, 8, "same_dong", 12, "rh_rent")
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
    assert out["estimated_market_price_man"] == 23845 and out["nearby_jeonse_price_man"] == 18342
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
