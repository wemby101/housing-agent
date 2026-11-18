"""청년 주거 지원 정책 매칭. 정책 조건은 data/policies.json (공식 출처에서 정리)."""
import json
from dataclasses import dataclass
from pathlib import Path

from housing.risk import man

DEFAULT_PATH = str(Path(__file__).resolve().parent.parent / "data" / "policies.json")
# 2026년 기준 중위소득 (월, 원) — 보건복지부 고시. 따로 사는 청년가구는 1인(기혼이면 부부 2인)만 쓴다
MEDIAN_INCOME_2026 = {1: 2_564_238, 2: 4_199_292}


@dataclass(frozen=True)
class Profile:
    age: int
    income_won: int  # 본인 세전 연소득, 기혼이면 부부 합산
    married: bool
    homeless: bool
    deposit_won: int
    monthly_rent_won: int
    area_m2: float | None = None  # 계약할 집의 전용면적, 모르면 None
    lives_apart: bool = False  # 부모와 따로 삶 (주민등록·건강보험도 따로) — False는 '모름'
    assets_ok: bool = False  # 본인 재산(보증금 포함) 1.2억 원 이하·자동차 2,500만 원 미만 — False는 '모름'

    @property
    def contract_type(self) -> str:
        return "monthly" if self.monthly_rent_won > 0 else "jeonse"


@dataclass(frozen=True)
class PolicyMatch:
    policy: dict
    status: str  # "eligible" | "check_needed"
    reasons: list[str]


def load_policies(path: str = DEFAULT_PATH) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _check(profile: Profile, p: dict) -> tuple[list[str], list[str]] | None:
    """조건을 모두 충족하면 (충족 사유, 추가 확인할 조건), 하나라도 불충족이면 None. null 조건은 검사하지 않는다."""
    types = p.get("contract_types") or ["any"]
    if "any" not in types and profile.contract_type not in types:
        return None
    reasons, unverified = [], []
    lo, hi = p.get("min_age"), p.get("max_age")
    if lo is not None or hi is not None:
        if (lo is not None and profile.age < lo) or (hi is not None and profile.age > hi):
            return None
        reasons.append(f"나이 {profile.age}세 ({lo or ''}~{hi or ''}세)")
    limit = p.get("max_income_won_married") if profile.married and p.get("max_income_won_married") is not None else p.get("max_income_won")
    if limit is not None:
        newlywed = p.get("max_income_won_newlywed")
        if profile.married and newlywed is not None and limit < profile.income_won <= newlywed:
            unverified.append(f"부부 합산 연소득 {man(limit)} 원 초과라 혼인신고 7년 이내 신혼부부({man(newlywed)} 원 이하)만 가능")
        elif profile.income_won > limit:
            return None
        else:
            reasons.append(f"{'부부 합산 ' if profile.married else ''}연소득 {man(profile.income_won)} 원 ≤ {man(limit)} 원")
    if p.get("min_deposit_won") is not None and profile.deposit_won < p["min_deposit_won"]:
        return None
    for key, value, label in (("max_deposit_won", profile.deposit_won, "보증금"),
                              ("max_monthly_rent_won", profile.monthly_rent_won, "월세")):
        if p.get(key) is not None:
            if value > p[key]:
                return None
            reasons.append(f"{label} {man(value)} 원 ≤ {man(p[key])} 원")
    if p.get("max_area_m2") is not None:
        if profile.area_m2 is None:
            unverified.append(f"전용면적 {p['max_area_m2']:g}㎡ 이하 주택")
        elif profile.area_m2 > p["max_area_m2"]:
            return None
        else:
            reasons.append(f"전용면적 {profile.area_m2:g}㎡ ≤ {p['max_area_m2']:g}㎡")
    if p.get("max_transaction_won") is not None:  # 거래금액 = 보증금 + 월세 × 100
        amount = profile.deposit_won + profile.monthly_rent_won * 100
        if amount > p["max_transaction_won"]:
            return None
        reasons.append(f"거래금액(보증금 + 월세 × 100) {man(amount)} 원 ≤ {man(p['max_transaction_won'])} 원")
    if p.get("median_income_pct"):
        lo, hi = p["median_income_pct"]
        span = f"{lo or 0}% 초과~{hi}% 이하" if lo else f"{hi}% 이하"
        if not profile.lives_apart:  # 부모와 같이 살면 가구 소득에 부모 소득이 들어가 계산할 수 없다
            unverified.append(f"가구 소득 기준 중위소득 {span} (부모와 같이 살면 부모 소득 포함)")
        else:
            size = 2 if profile.married else 1
            pct = round(profile.income_won / 12 / MEDIAN_INCOME_2026[size] * 100)
            if pct > hi:
                return None
            if lo and pct <= lo:  # 학생 등은 빼지 않고 안내 — 서울 공고: 48% 이하는 주거급여·국토부 청년월세 대상
                unverified.append(f"가구 소득이 기준 중위소득 {lo}% 이하({pct}%)라 대상이 아닐 수 있어요. 주거급여·국토부 청년월세를 먼저 확인")
            else:
                reasons.append(f"{size}인 가구 소득 기준 중위소득 {pct}% ({span})")
    if p.get("lives_apart_required"):
        if profile.lives_apart:
            reasons.append("부모와 따로 삶")
        else:
            unverified.append("부모님과 따로 사는 청년")
    if p.get("assets_text"):
        (reasons if profile.assets_ok else unverified).append(p["assets_text"])
    if p.get("homeless_required"):
        if not profile.homeless:
            return None
        reasons.append("무주택")
    return reasons, unverified


def match_policies(profile: Profile, policies: list[dict], region: str | None = None) -> list[PolicyMatch]:
    """region은 시도 코드 앞 2자리(11 서울, 41 경기, 28 인천). regions가 빈 정책은 전국, region이 None이면 거르지 않는다."""
    matches = []
    for p in policies:
        if region is not None and p.get("regions") and region not in p["regions"]:
            continue
        checked = _check(profile, p)
        if checked is None:
            continue
        reasons, unverified = checked
        unverifiable = unverified + (p.get("unverifiable_conditions") or [])
        matches.append(PolicyMatch(
            policy=p,
            status="check_needed" if unverifiable else "eligible",
            reasons=reasons + [f"확인 필요: {c}" for c in unverifiable],
        ))
    return matches
