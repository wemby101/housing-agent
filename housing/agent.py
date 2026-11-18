"""AI 상담 모드: 말로 물으면 AI가 진단 도구를 골라 부르고 결과를 설명한다. 숫자는 모두 도구(코드)가 계산한다.

AI 제공사는 OpenAI 호환 채팅 API를 쓴다. 키가 있는 제공사를 Gemini → Groq 순서로 쓰고, 한도가 차면 다음으로 넘어간다."""
import json
import os
import re
import time
from datetime import date

import requests

from housing.address import search_address
from housing.errors import DataSourceError
from housing.official_price import list_units
from housing.pipeline import diagnose
from housing.policies import Profile

PROVIDERS = [  # (채팅 API 주소, .env 키 이름, 모델) — 붐비거나(503) 한도가 차거나(429) 기록을 거부하면(400) 다음 모델, 30초 안에 응답이 없으면 다음 제공사
    # Gemini 무료 등급은 답이 가장 깔끔하지만 모델마다 하루 20회라 여럿을 돌린다 (2026-11-17: 3.7·3.8은 503)
    # 시간 초과면 남은 Gemini를 건너뛰므로 가장 빠른 모델이 먼저 (2026-11-18: preview 2.5초, 3.6 20초, 3.5 60초 초과)
    ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "GEMINI_API_KEY",
     ["gemini-3-flash-preview", "gemini-3.6-flash", "gemini-3.5-flash"]),
    # Groq 무료 등급은 분당 한도가 작아 예비로만 쓴다. gpt-oss는 숫자를 지어내거나 틀려서(2026-11-17 비교) qwen만
    ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY", ["qwen/qwen3.8-27b"]),
]
MAX_STEPS = 6  # 도구 호출이 끝없이 이어지지 않게
MAN = 10_000
LEVELS = {"danger": "위험", "caution": "주의", "info": "참고"}

SYSTEM = """너는 청년의 전월세 계약 전 위험을 진단해 주는 상담사야. 서울·경기·인천의 빌라(연립·다세대)와 아파트만 진단할 수 있어.

규칙:
- 금액·비율·한도 같은 숫자는 반드시 도구 결과에 있는 값만 써. 직접 계산하거나 추정하지 마.
- 보증금을 권할 때는 safe_deposit_target_man 이하로만 권해. 그보다 큰 금액이나 범위를 제시하지 마.
- 진단에는 주소(도로명이나 지번), 호수, 보증금, 월세가 필요해. 빠진 게 있으면 짧게 물어봐. 근저당을 모르면 모른다고 넘겨.
- 지원 정책은 나이와 세전 연소득을 알아야 찾을 수 있어. 진단을 먼저 하고, 모르면 나중에 물어봐.
- 지원 금액은 서로 더하지 마. 서울·국토부 청년월세처럼 같이 받을 수 없는 지원이 있어. 가장 큰 혜택과 확인할 조건을 알려 줘.
- 주소 검색 결과가 여러 개고 어느 집인지 애매하면 사용자에게 골라 달라고 해.
- 도구가 error를 주면 무엇이 필요한지 사용자에게 쉽게 말해.
- 답은 한국어(한글) 해요체 존댓말로만 짧게 써. 금액·비율은 도구 결과 값을 반올림하지 말고 그대로 써. 영어·중국어·태국어 등 다른 나라 글자나 단어를 섞지 마. 순서는 한 줄 결론 → 핵심 이유 2~3개 → 계약 전에 할 일. 제목(#)은 쓰지 말고 굵은 글씨와 목록만 써. 금액은 "2억 2,000만 원"처럼 읽기 쉽게.
- 법률 자문이 아니라 참고용이라는 점, 계약 전 공인중개사·HUG(1566-9009) 확인을 마지막에 한 줄로 안내해."""

TOOLS = [
    {"type": "function", "function": {
        "name": "search_address",
        "description": "주소를 검색해 후보 목록을 돌려준다. 어느 집인지 확인할 때 쓴다.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "도로명이나 지번 주소, 건물명 (예: 봉천동 1-70)"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "diagnose_house",
        "description": "집 한 채의 계약 위험(깡통전세·보증보험 한도·주변 전세 시세·건축물대장)과 받을 수 있는 청년 지원 정책을 공공데이터로 계산한다.",
        "parameters": {"type": "object", "properties": {
            "address": {"type": "string", "description": "주소 검색어"},
            "pick": {"type": "integer", "description": "search_address 후보 번호 (기본 0)"},
            "dong": {"type": "string", "description": "동 (아파트 101동 등, 없으면 비움)"},
            "ho": {"type": "string", "description": "호수 (예: 201)"},
            "area_m2": {"type": "number", "description": "전용면적㎡. 모르면 비움 (공시자료에서 찾음)"},
            "deposit_man": {"type": "integer", "description": "보증금 (만원)"},
            "monthly_rent_man": {"type": "integer", "description": "월세 (만원, 전세면 0)"},
            "senior_debt_man": {"type": "integer", "description": "근저당 등 선순위 채권 (만원). 모르면 비움"},
            "age": {"type": "integer", "description": "만 나이. 모르면 비움"},
            "income_man": {"type": "integer", "description": "세전 연소득 (만원, 기혼이면 부부 합산). 모르면 비움"},
            "married": {"type": "boolean"},
            "homeless": {"type": "boolean", "description": "무주택 여부 (기본 true, 기혼이면 부부 모두)"},
            "lives_apart": {"type": "boolean", "description": "부모와 따로 사는지 (주민등록·건강보험도 따로). 사용자가 말했을 때만 true"},
            "assets_ok": {"type": "boolean", "description": "본인 재산(보증금 포함) 1.2억 원 이하·자동차 2,500만 원 미만. 사용자가 말했을 때만 true"}},
            "required": ["address", "ho", "deposit_man"]}}},
]


class AgentError(Exception):
    pass


def _models():
    return [(url, os.environ[key], m) for url, key, models in PROVIDERS if os.environ.get(key) for m in models]


def available() -> bool:
    return bool(_models())


def _plain(m: dict, gemini: bool) -> dict:
    """Gemini는 사고 서명(extra_content)을 돌려받아야 하고, 다른 제공사는 모르는 필드가 있으면 400을 낸다."""
    extra = ("extra_content",) if gemini else ()
    out = {k: v for k, v in m.items() if k in ("role", "content", "tool_calls", "tool_call_id", *extra)}
    if out.get("tool_calls"):
        out["tool_calls"] = [{k: v for k, v in c.items() if k in ("id", "type", "function", *extra)} for c in out["tool_calls"]]
    return out


def _man(won):
    return None if won is None else won // MAN  # 화면(risk.man)과 같이 버림: 목표 보증금을 넘겨 말하지 않게


def search_houses(query: str) -> dict:
    found = search_address(query, limit=10)
    return {"results": [{"pick": i, "address": a.road_addr, "jibun": a.jibun_addr, "building": a.building_name}
                        for i, a in enumerate(found)]}


def diagnose_house(address: str, ho: str, deposit_man: int, pick: int = 0, dong: str = "", area_m2: float | None = None,
                   monthly_rent_man: int = 0, senior_debt_man: int | None = None, age: int | None = None,
                   income_man: int | None = None, married: bool = False, homeless: bool = True,
                   lives_apart: bool = False, assets_ok: bool = False) -> dict:
    found = search_address(address, limit=10)
    if not found:
        return {"error": "주소를 찾지 못했어요. 도로명이나 지번을 다시 확인해 달라고 하세요."}
    addr = found[pick]
    ho = str(ho).strip().removesuffix("호").strip()
    dong = str(dong or "").strip().removesuffix("동").strip()
    if not area_m2:
        try:
            units = list_units(addr)
        except DataSourceError:
            units = []
        unit = next((u for u in units if u.ho == ho and (not dong or u.dong.removesuffix("동") == dong)), None)
        if unit is None or not unit.area_m2:
            return {"error": f"{addr.road_addr} {ho}호의 전용면적을 공시자료에서 찾지 못했어요. 전용면적(㎡)을 물어보세요."}
        dong, area_m2 = unit.dong, unit.area_m2
    known = age is not None and income_man is not None
    profile = Profile(age or 26, (income_man or 0) * MAN, married, homeless, deposit_man * MAN, monthly_rent_man * MAN, area_m2,
                      lives_apart, assets_ok)
    d = diagnose(addr, dong, ho, float(area_m2), deposit_man * MAN, monthly_rent_man * MAN,
                 None if senior_debt_man is None else senior_debt_man * MAN, profile, today=date.today())
    r = d.risk
    out = {
        "address": f"{addr.road_addr} {dong} {ho}호".replace("  ", " "), "area_m2": area_m2,
        "headline": r.headline,
        "signals": [{"level": LEVELS[s.level], "title": s.title, "detail": s.detail} for s in r.signals],
        "official_price_man": _man(d.official.price_won) if d.official else None,
        "estimated_market_price_man": _man(d.market.price_won) if d.market else None,
        "nearby_jeonse_price_man": _man(d.jeonse.price_won) if d.jeonse else None,
        "deposit_to_market_ratio_pct": round(r.deposit_ratio * 100) if r.deposit_ratio is not None else None,
        "guarantee_limit_man": _man(r.guarantee_limit_won),
        "safe_deposit_target_man": _man(r.target_deposit_won),
        "building": f"{d.building.main_purpose} {d.building.etc_purpose} {d.building.approval_year or ''}년" if d.building else None,
        "data_errors": d.errors,
    }
    if known:  # 나이를 모르면 임시 나이로 걸러진 목록이라 주지 않는다
        out["other_local_youth_housing_policies"] = [y.name for y in d.youth]
        out["policies"] = [{"name": m.policy["name"], "status": "조건 충족" if m.status == "eligible" else "추가 확인 필요",
                            "max_benefit_man": _man(m.policy["benefit_max_won"]), "benefit": m.policy["benefit_text"]}
                           for m in d.policies]
    else:
        out["policies_note"] = "나이와 세전 연소득을 모르면 지원 정책을 찾을 수 없어요."
    return out


def run_tool(name: str, arguments: str) -> dict:
    """도구 실패는 예외 대신 {"error": ...}로 돌려 AI가 사용자에게 설명하게 한다."""
    fn = {"search_address": search_houses, "diagnose_house": diagnose_house}.get(name)
    if fn is None:
        return {"error": f"없는 도구예요: {name}"}
    try:
        return fn(**json.loads(arguments or "{}"))
    except DataSourceError as e:
        return {"error": str(e)}
    except (ValueError, TypeError, IndexError) as e:  # 잘못된 인자·후보 번호
        return {"error": f"도구 인자가 잘못됐어요 ({type(e).__name__}: {e})"}
    except Exception as e:  # 예상 못 한 실패도 채팅을 멈추지 않고 AI가 설명하게 한다
        return {"error": f"공공데이터 조회 중 문제가 생겼어요 ({type(e).__name__}). 잠시 뒤 다시 시도하거나 폼으로 진단해 달라고 안내하세요."}


def _chat(messages: list[dict], tools: list | None = TOOLS, failed: set | None = None) -> dict:
    """failed: 한 답변 안에서 실패한 모델·제공사. 다음 단계에서 다시 기다리지 않게 reply가 넘겨준다."""
    resp, failed = None, set() if failed is None else failed
    for url, key, model in _models():
        if url in failed or model in failed:
            continue
        gemini = "googleapis" in url
        body = {"model": model, "messages": [_plain(m, gemini) for m in messages], **({"tools": tools} if tools else {}),
                "reasoning_effort": "low",
                # Groq 무료 등급은 분당 출력 1,000토큰이고 요청한 상한을 미리 뺀다 → 연달아 불러도 들어가게 600
                **({} if gemini else {"max_completion_tokens": 600})}
        try:
            resp = requests.post(url, headers={"Authorization": f"Bearer {key}"}, timeout=(5, 30), json=body)
            wait = float((getattr(resp, "headers", None) or {}).get("retry-after") or "inf")
            if resp.status_code == 429 and not gemini and wait <= 5:  # 토큰이 잠깐 모자랄 때만 — 하루 한도면 길게 준다
                time.sleep(wait)
                resp = requests.post(url, headers={"Authorization": f"Bearer {key}"}, timeout=(5, 30), json=body)
        except requests.RequestException:  # 응답이 없으면 같은 제공사의 다른 모델도 기다리지 않고 다음 제공사로
            failed.add(url)
            continue
        # 400: Groq이 만든 도구 호출 기록에는 사고 서명이 없어 Gemini가 거부한다 → 다음 모델이 이어받게
        if resp.status_code not in (400, 429, 503):
            break
        failed.add(model)
    if resp is None:  # 오류 메시지에는 키가 든 헤더가 섞일 수 있어 원문을 쓰지 않는다
        raise AgentError("AI가 응답하지 않아요. 잠시 뒤에 다시 물어보거나 아래 폼으로 진단해 주세요.")
    if resp.status_code == 503:
        raise AgentError("AI 서버가 붐벼요. 잠시 뒤에 다시 물어봐 주세요.")
    if resp.status_code == 429:
        raise AgentError("무료 AI 사용 한도에 걸렸어요. 1분쯤 뒤에 다시 물어보거나 아래 폼으로 진단해 주세요.")
    if resp.status_code >= 400:
        raise AgentError(f"AI 응답 오류 (HTTP {resp.status_code})")
    try:
        return resp.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError):
        raise AgentError("AI 응답을 해석하지 못했어요") from None


FOREIGN = re.compile(r"[぀-ヿ㐀-䶿一-鿿฀-๿]")  # 한자·가나·태국 문자
NUMS = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _hangul_only(messages: list[dict], text: str, failed: set) -> str:
    """한 번만 한글로 다시 받는다. 실패하거나 숫자가 바뀌면 원래 답을 쓴다."""
    try:
        out = (_chat([*messages, {"role": "user", "content": "방금 답에 한자나 외국 글자가 섞였어. 숫자와 내용은 그대로 두고 "
                                  "한글로만 다시 써 줘. 다시 쓴 답만 보여 줘."}], tools=None, failed=failed).get("content") or "").strip()
    except AgentError:
        return text
    if not out or FOREIGN.search(out) or set(NUMS.findall(out)) != set(NUMS.findall(text)):
        return text
    return out


def reply(history: list[dict]) -> tuple[str, list[dict]]:
    """대화 기록(user/assistant/tool 메시지)을 받아 답과 늘어난 기록을 돌려준다."""
    messages, failed = [{"role": "system", "content": SYSTEM}, *history], set()
    for _ in range(MAX_STEPS):
        msg = _chat(messages, failed=failed)
        messages.append(msg)  # 받은 그대로: Gemini의 사고 서명(extra_content)을 다음 요청에 돌려줘야 한다
        calls = msg.get("tool_calls") or []
        if not calls:
            text = msg.get("content") or ""
            if FOREIGN.search(text):  # 지시문에 한국어만 쓰라고 해도 한자 등이 가끔 섞인다
                text = _hangul_only(messages, text, failed)
                messages[-1] = {**msg, "content": text}
            return text, messages[1:]
        for c in calls:
            result = run_tool(c["function"]["name"], c["function"].get("arguments", "{}"))
            messages.append({"role": "tool", "tool_call_id": c.get("id", ""), "content": json.dumps(result, ensure_ascii=False)})
    return "질문을 처리하는 단계가 너무 길어졌어요. 주소·호수·보증금을 한 번에 적어 다시 물어봐 주세요.", messages[1:]


POLISH = ("세입자가 집주인이나 공인중개사에게 보낼 메시지야. 정중하고 자연스러운 한국어 문자 메시지로 다듬어 줘. "
          "숫자·금액·비율과 번호 붙은 특약 문장은 한 글자도 바꾸거나 빼지 마. 새 내용이나 숫자를 더하지 마. 다듬은 메시지만 답해.")


def polish(text: str) -> str:
    """AI는 말투만 다듬는다. 원문의 숫자가 하나라도 사라지거나 새 숫자가 생기면 쓰지 않는다."""
    out = (_chat([{"role": "system", "content": POLISH}, {"role": "user", "content": text}], tools=None).get("content") or "").strip()
    if not out or set(NUMS.findall(out)) != set(NUMS.findall(text)):
        raise AgentError("AI가 숫자를 바꾸거나 빠뜨려서 원래 문장을 그대로 보여 드려요.")
    return out
