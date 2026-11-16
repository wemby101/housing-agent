"""청년 주거 지원 정책 매칭. 정책 조건은 data/policies.json (공식 출처에서 정리)."""
import json
from dataclasses import dataclass
from pathlib import Path

from housing.risk import man

DEFAULT_PATH = str(Path(__file__).resolve().parent.parent / "data" / "policies.json")


@dataclass(frozen=True)
class Profile:
    age: int
    income_won: int  # 본인 세전 연소득, 기혼이면 부부 합산
    married: bool
    homeless: bool
    deposit_won: int
    monthly_rent_won: int

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


def _check(profile: Profile, p: dict) -> list[str] | None:
    """조건을 모두 충족하면 충족 사유 목록, 하나라도 불충족이면 None. null 조건은 검사하지 않는다."""
    types = p.get("contract_types") or ["any"]
    if "any" not in types and profile.contract_type not in types:
        return None
    reasons = []
    lo, hi = p.get("min_age"), p.get("max_age")
    if lo is not None or hi is not None:
        if (lo is not None and profile.age < lo) or (hi is not None and profile.age > hi):
            return None
        reasons.append(f"나이 {profile.age}세 ({lo or ''}~{hi or ''}세)")
    limit = p.get("max_income_won_married") if profile.married and p.get("max_income_won_married") is not None else p.get("max_income_won")
    if limit is not None:
        if profile.income_won > limit:
            return None
        reasons.append(f"{'부부 합산 ' if profile.married else ''}연소득 {man(profile.income_won)} 원 ≤ {man(limit)} 원")
    for key, value, label in (("max_deposit_won", profile.deposit_won, "보증금"),
                              ("max_monthly_rent_won", profile.monthly_rent_won, "월세")):
        if p.get(key) is not None:
            if value > p[key]:
                return None
            reasons.append(f"{label} {man(value)} 원 ≤ {man(p[key])} 원")
    if p.get("homeless_required"):
        if not profile.homeless:
            return None
        reasons.append("무주택")
    return reasons


def match_policies(profile: Profile, policies: list[dict]) -> list[PolicyMatch]:
    matches = []
    for p in policies:
        reasons = _check(profile, p)
        if reasons is None:
            continue
        unverifiable = p.get("unverifiable_conditions") or []
        matches.append(PolicyMatch(
            policy=p,
            status="check_needed" if unverifiable else "eligible",
            reasons=reasons + [f"확인 필요: {c}" for c in unverifiable],
        ))
    return matches
