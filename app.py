"""청년 집 구하기 진단 — 폼 모드 (Streamlit)."""
from datetime import date
from pathlib import Path

import streamlit as st

from housing.address import search_address
from housing.errors import DataSourceError
from housing.official_price import list_units
from housing.pipeline import diagnose
from housing.policies import Profile
from housing.risk import man

DISCLAIMER = "참고용 진단입니다. 계약 전 공인중개사·HUG(1566-9009) 상담으로 확인하세요."
MAN = 10_000
SEARCH_LIMIT = 20
ERROR_LABELS = {"building": "건축물대장", "official": "공시가격", "market": "실거래가 시세"}

st.set_page_config(page_title="청년 집 구하기 진단", page_icon="🏠")
st.markdown(f"<style>{(Path(__file__).parent / 'style.css').read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)
st.title("🏠 청년 집 구하기 진단")
st.caption("계약 전에 이 집의 위험 신호와 내가 받을 수 있는 주거 지원을 한 번에 확인해요. 서울 빌라(연립·다세대) 대상.")


@st.cache_data(ttl=3600, show_spinner=False)
def cached_search(keyword):
    return search_address(keyword, limit=SEARCH_LIMIT)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_units(addr):
    try:
        return list_units(addr)
    except DataSourceError:  # 호 목록이 없으면 직접 입력으로 대신한다
        return []


@st.cache_data(ttl=3600, show_spinner="공공데이터를 조회하고 있어요…")
def cached_diagnose(*args, **kwargs):
    return diagnose(*args, **kwargs)


# 1. 주소
keyword = st.text_input("집 주소 검색", placeholder="예: 봉천동 1-70, 청림3다길 12")
address = None
if keyword.strip():
    try:
        results = cached_search(keyword.strip())
    except DataSourceError as e:
        st.error(str(e))
        results = []
    else:
        if not results:
            st.warning("주소를 찾을 수 없어요. 도로명이나 지번을 다시 확인해 주세요.")
    if results:
        address = st.selectbox(
            f"검색 결과 {len(results)}건 — 계약할 집을 골라 주세요", results, index=None,
            placeholder="주소 선택",
            format_func=lambda a: f"{a.road_addr}" + (f" · {a.building_name}" if a.building_name else ""),
        )
        if len(results) >= SEARCH_LIMIT:
            st.caption(f"결과가 많아 {SEARCH_LIMIT}건까지만 보여요. 찾는 집이 없으면 번지·건물명까지 더 자세히 입력해 주세요.")

unit = None
if address is not None and (units := cached_units(address)):
    unit = st.selectbox(
        "호 선택", units, index=None, placeholder="호를 골라 주세요 (목록에 없으면 비워 두고 아래에 직접 입력)",
        format_func=lambda u: " ".join(filter(None, [u.dong, f"{u.ho}호"])) + (f" · 전용 {u.area_m2:g}㎡" if u.area_m2 else ""),
    )

# 다른 집을 고르면 이전 진단 결과를 숨긴다
if (d := st.session_state.get("diagnosis")) is not None and address != d.address:
    del st.session_state["diagnosis"]

# 2. 계약·내 정보
with st.form("diagnose"):
    if unit is None:
        c1, c2, c3 = st.columns(3)
        dong = c1.text_input("동 (없으면 비움)")
        ho = c2.text_input("호", placeholder="201")
        area = c3.number_input("전용면적 (㎡)", min_value=1.0, value=40.0, step=1.0)
    else:  # 공시자료의 면적을 기본값으로, 실제와 다르면 고칠 수 있게
        dong, ho = unit.dong, unit.ho
        area = st.number_input("전용면적 (㎡)", min_value=1.0, value=unit.area_m2 or 40.0, step=1.0)

    c1, c2 = st.columns(2)
    deposit = c1.number_input("보증금 (만원)", min_value=0, value=0, step=500)
    rent = c2.number_input("월세 (만원, 전세면 0)", min_value=0, value=0, step=5)
    c1, c2 = st.columns([2, 1])
    debt = c1.number_input("선순위 채권·근저당 (만원)", min_value=0, value=0, step=500)
    debt_unknown = c2.checkbox("모름", value=True, help="등기부등본 을구의 채권최고액. 모르면 0원으로 계산하고 경고해요.")
    official = st.number_input(
        "공시가격 직접 입력 (만원, 선택)", min_value=0, value=0, step=500,
        help="부동산공시가격알리미(realtyprice.kr)에서 확인할 수 있어요. 비우면 자동 조회를 시도해요.")

    st.markdown("**내 정보**")
    c1, c2 = st.columns(2)
    age = c1.number_input("만 나이", min_value=15, max_value=100, value=26)
    income = c2.number_input("세전 연소득 (만원, 기혼이면 부부 합산)", min_value=0, value=3000, step=100)
    c1, c2 = st.columns(2)
    married = c1.checkbox("기혼")
    homeless = c2.checkbox("무주택", value=True)
    submitted = st.form_submit_button("진단하기", type="primary", use_container_width=True)

if submitted and address is None:
    st.warning("먼저 위에서 주소를 검색하고 골라 주세요.")

if submitted and address is not None and deposit == 0 and rent == 0:
    st.warning("보증금이나 월세를 입력해 주세요. 둘 다 0이면 진단할 수 없어요.")
elif submitted and address is not None:
    profile = Profile(age=int(age), income_won=int(income) * MAN, married=married, homeless=homeless,
                      deposit_won=int(deposit) * MAN, monthly_rent_won=int(rent) * MAN)
    senior = None if debt_unknown and debt == 0 else int(debt) * MAN  # 입력한 채권은 '모름'보다 우선
    st.session_state["senior_debt"] = senior or 0
    d = cached_diagnose(address, dong.strip(), ho.strip(), float(area), int(deposit) * MAN, int(rent) * MAN,
                        senior, profile,
                        manual_official_won=int(official) * MAN or None, today=date.today())
    if d.errors:  # 일시적 실패를 1시간 동안 재사용하지 않도록
        cached_diagnose.clear()
    st.session_state["diagnosis"] = d

d = st.session_state.get("diagnosis")
if d is not None:
    r = d.risk

    # 1) 한 줄 결론
    st.divider()
    with st.container(key="addr-plate"):
        st.subheader(d.address.road_addr)
    top = r.signals[0].level if r.signals else "info"
    {"danger": st.error, "caution": st.warning}.get(top, st.info)(
        ("⚠️ " if top in ("danger", "caution") else "") + r.headline)

    # 2) 신호 카드
    st.markdown("#### 위험 신호")
    icons = {"danger": "🔴", "caution": "🟠", "info": "⚪"}
    for s in r.signals:
        with st.container(border=True):
            st.markdown(f"{icons[s.level]} **{s.title}**")
            st.write(s.detail)
            st.caption(f"출처: {s.source}")
    for key, msg in d.errors.items():
        st.warning(f"{ERROR_LABELS.get(key, key)} — {msg}")
    if d.building:
        st.caption(f"건축물대장: {d.building.main_purpose} · {d.building.etc_purpose} · {d.building.households}세대 · "
                   f"{d.building.approval_year or '?'}년 사용승인")

    # 3) 지원
    st.markdown("#### 받을 수 있는 지원")
    eligible_total = sum(m.policy["benefit_max_won"] or 0 for m in d.policies if m.status == "eligible")
    if not d.policies:
        st.write("입력한 조건으로 맞는 지원을 찾지 못했어요.")
    elif eligible_total == 0:
        st.info("조건 충족이 확정된 현금 지원은 아직 없어요. 아래 '추가 확인 필요' 항목의 조건을 확인해 보세요.")
    else:
        st.metric("조건 충족 지원의 최대 금액 합계", f"{man(eligible_total)} 원",
                  help="'확인 필요' 정책과 대출 이자지원은 합계에서 빠져요.")
    for m in d.policies:
        p = m.policy
        badge = "✅ 조건 충족" if m.status == "eligible" else "🔎 추가 확인 필요"
        with st.expander(f"{badge} · {p['name']}"):
            st.write(p["benefit_text"])
            st.markdown("\n".join(f"- {x}" for x in m.reasons))
            if p["documents"]:
                st.markdown("**준비 서류:** " + ", ".join(p["documents"]))
            st.markdown(f"[신청하기]({p['apply_url']}) · [출처]({p['source_url']}) (확인일 {p['checked_date']})")

    # 4) 체크리스트
    st.markdown("#### 해야 할 일")
    items = []
    if r.target_deposit_won is not None:
        parts = []
        if r.guarantee_limit_won is not None:
            parts.append(f"보증보험 한도 {man(max(r.guarantee_limit_won, 0))} 원 (공시가격 × 126% − 선순위 채권)")
        if d.market is not None:
            parts.append(f"추정 시세 × 70% − 선순위 채권 = {man(d.market.price_won * 7 // 10 - st.session_state.get('senior_debt', 0))} 원")
        items.append(f"보증금 협상 목표: **{man(r.target_deposit_won)} 원 이하** — " + " 와 ".join(parts) + " 중 낮은 값")
    items += [
        "등기부등본(을구)에서 근저당·선순위 채권 확인",
        "건축물대장에서 위반건축물 여부 확인 (자동 조회 항목에 없어 직접 확인이 필요해요)",
        "특약 넣기: \"잔금일 다음 날까지 근저당 등 권리 변동을 하지 않는다\"",
        "특약 넣기: \"전세보증보험 가입이 거절되면 계약을 해제하고 계약금을 돌려준다\"",
    ]
    for it in items:
        st.checkbox(it, key=it)

st.divider()
st.caption(DISCLAIMER)
