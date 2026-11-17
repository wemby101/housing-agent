"""계약할 때 쓸 문구: 진단 결과에 맞춘 계약서 특약과 집주인·중개사에게 보낼 협상 메시지.
숫자는 모두 진단 결과(코드 계산)에서 온다."""
from housing.pipeline import Diagnosis
from housing.risk import man

REFUND = "임대인은 받은 돈 전부를 즉시 돌려준다"


def won(x: int) -> str:
    """2억 2,000만 원 · 22.5만 원처럼 읽기 쉬운 금액."""
    eok, rest = divmod(x, 100_000_000)
    m = rest / 10_000
    parts = [f"{eok:,}억"] if eok else []
    if m or not eok:
        parts.append(f"{m:,.1f}만".replace(".0만", "만") if m < 100 else f"{int(m):,}만")  # 소수점은 월세처럼 작은 금액만
    return " ".join(parts) + " 원"


def special_terms(d: Diagnosis) -> list[str]:
    terms = []
    if d.deposit_won > 0:
        terms += [
            f"임대인은 잔금일 다음 날까지 저당권 등 어떤 담보권도 설정하지 않는다. 이를 어기면 임차인은 계약을 해제할 수 있고, {REFUND}.",
            f"임차인의 보증금 반환보증(HUG·HF·SGI) 가입이 임차인 책임이 아닌 사유로 거절되면 계약을 해제하고, {REFUND}.",
        ]
    if d.senior_debt_won:
        terms.append(f"임대인은 잔금일까지 등기부 을구의 근저당권(채권최고액 합계 {man(d.senior_debt_won)} 원)을 말소한다. "
                     f"말소되지 않으면 임차인은 계약을 해제할 수 있고, {REFUND}.")
    if d.registry_flags:
        terms.append(f"임대인은 잔금일까지 등기부의 {'·'.join(d.registry_flags)} 기록을 말소한다. "
                     f"말소되지 않으면 임차인은 계약을 해제할 수 있고, {REFUND}.")
    if any(s.code in ("MIXED_USE", "NOT_RESIDENTIAL") for s in d.risk.signals):
        terms.append("계약할 호실이 건축물대장에 주택이 아닌 용도로 올라 있어 보증보험이나 전세대출이 거절되면 "
                     f"계약을 해제하고, {REFUND}.")
    terms.append("임대인은 계약일에 국세·지방세 완납증명서를 임차인에게 보여 준다.")
    return terms


def negotiation_message(d: Diagnosis, deposit_won: int | None = None, monthly_rent_won: int | None = None) -> str:
    """deposit_won이 지금 보증금보다 낮으면 그 조건을 제안하고, 아니면 특약만 요청한다."""
    r = d.risk
    home = " ".join(filter(None, [d.address.road_addr or d.address.jibun_addr, d.dong, f"{d.ho}호" if d.ho else ""]))
    lines = [f"안녕하세요. {home} 계약을 검토하고 있는 사람입니다.", ""]
    if deposit_won is not None and deposit_won < d.deposit_won:
        worries = []
        if r.deposit_ratio is not None and r.deposit_ratio >= 0.7:
            worries.append(f"보증금{'과 선순위 채권' if d.senior_debt_won else ''}이 최근 실거래로 추정한 시세의 "
                           f"{int(r.deposit_ratio * 100)}%입니다.")
        if r.guarantee_limit_won is not None and d.deposit_won > r.guarantee_limit_won:
            worries.append(f"공시가격 기준 전세보증보험 한도({won(max(r.guarantee_limit_won, 0))})를 넘어 보험 가입이 어려울 수 있습니다.")
        if worries:
            lines += ["공공데이터로 확인해 보니 이런 점이 걱정됩니다.", *[f"- {w}" for w in worries], ""]
        lines += [f"그래서 보증금을 {won(deposit_won)}으로 낮추고 월세를 {won(monthly_rent_won or 0)}으로 하는 조건이 "
                  "가능할지 여쭤봅니다.", ""]
    lines.append("또 아래 특약을 계약서에 넣어 주실 수 있을까요?")
    lines += [f"{i}. {t}" for i, t in enumerate(special_terms(d), 1)]
    lines += ["", "검토 부탁드립니다. 감사합니다."]
    return "\n".join(lines)


def landlord_checks(d: Diagnosis) -> list[str]:
    """집주인 확인: 공개 API가 없어 자동 조회는 못 하고, 어디서 어떻게 보는지 안내한다 (2026-11-17 확인)."""
    checks = ["집주인 확인: HUG 안심전세 앱에서 임대인 정보 조회 — 세금 체납 여부, 전세보증 사고 이력, "
              "보증 가입 금지 여부 (공인중개사를 통해 계약 의사가 확인되면 집주인 동의 없이 조회, 결과는 최대 7일 안에)"]
    if d.deposit_won > 10_000_000:
        checks.append("집주인 세금 체납 열람: 계약 후 입주일 전까지 계약서와 신분증을 들고 가까운 세무서에서 미납 국세 열람 "
                      "(보증금 1천만 원 초과면 계약 뒤에는 집주인 동의 없이 가능)")
    if "신탁" in d.registry_flags:
        checks.append("신탁 등기: 등기소에서 신탁원부를 떼어 임대 권한이 누구에게 있는지 확인하고, 수탁사(신탁회사)의 "
                      "임대 동의서를 받기. 동의 없이 원래 집주인과만 계약하면 보증금을 보호받지 못할 수 있어요")
    return checks
