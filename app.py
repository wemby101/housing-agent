"""청년 집 구하기 진단 — 폼 모드 (Streamlit)."""
import math
from datetime import date
from pathlib import Path
from statistics import median
from urllib.parse import urlencode

import altair as alt
import pydeck as pdk
import streamlit as st
import streamlit.components.v1 as components

from housing import agent
from housing.address import search_address
from housing.contract import landlord_checks, negotiation_message, special_terms, won
from housing.errors import DataSourceError
from housing.geocode import comp_points
from housing.market import quarterly_per_m2
from housing.official_price import list_units
from housing.pipeline import (LEGAL_CONVERSION_PCT, YOUTH_LOAN_RATE_PCT, default_loan, diagnose, monthly_interest,
                              rent_for_cut, what_if)
from housing.policies import Profile
from housing.registry import read_registry_pdf
from housing.report import build_report
from housing.risk import man

DISCLAIMER = "참고용 진단입니다. 계약 전 공인중개사·HUG(1566-9009) 상담으로 확인하세요."
MAN = 10_000
SEARCH_LIMIT = 20
ICONS = {"danger": "🔴", "caution": "🟠", "info": "⚪"}
MAX_COMPARE = 4
AI_MADE = "AI가 생성한 답변이에요. 숫자는 공공데이터로 계산한 도구 결과지만, 계약 전에 꼭 직접 확인하세요."
ERROR_LABELS = {"building": "건축물대장", "official": "공시가격", "market": "실거래가 시세", "jeonse": "주변 전세 시세", "youth": "온통청년 정책"}

st.set_page_config(page_title="청년 집 구하기 진단", page_icon="🏠")
st.markdown(f"<style>{(Path(__file__).parent / 'style.css').read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)
with st.container(key="hero"):
    st.title("청년 집 구하기 진단")
    st.caption("계약 전에 이 집의 위험 신호와 내가 받을 수 있는 주거 지원을 한 번에 확인해요. 서울·경기·인천의 빌라(연립·다세대)와 아파트 대상.")

if agent.available():  # 키가 없으면 폼만 보인다
    with st.expander("💬 AI에게 말로 물어보기", expanded=bool(st.session_state.get("chat"))):
        st.caption("예: \"봉천동 1-70 201호 전세 2억 2천인데 괜찮아? 나는 27살, 연봉 3천이야\"  \n"
                   "숫자는 공공데이터로 계산한 값만 써요. 무료 AI를 쓰고 있어 입력한 내용이 AI 제공사에 전달돼요. "
                   "이름·연락처·주민등록번호 같은 개인정보는 적지 마세요.")
        chat = st.session_state.setdefault("chat", [])
        talk = st.container()  # 새 질문·답도 입력칸 위에 이어 붙도록
        def say(role, text):
            with talk.chat_message(role):
                st.markdown(text)
                if role == "assistant":
                    st.caption(AI_MADE)

        for m in chat:
            if m["role"] in ("user", "assistant") and m.get("content"):
                say(m["role"], m["content"])
        if question := st.chat_input("집 주소와 계약 조건을 적어 주세요"):
            talk.chat_message("user").markdown(question)
            with talk.chat_message("assistant"), st.spinner("AI가 공공데이터를 조회하고 있어요…"):
                try:
                    answer, st.session_state["chat"] = agent.reply([*chat, {"role": "user", "content": question}])
                    st.markdown(answer)
                    st.caption(AI_MADE)
                except agent.AgentError as e:
                    st.session_state["chat"] = [*chat, {"role": "user", "content": question}]  # 질문은 남겨 둔다
                    st.error(str(e))


@st.cache_data(ttl=3600, show_spinner=False)
def cached_search(keyword):
    return search_address(keyword, limit=SEARCH_LIMIT)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_units(addr):
    try:
        return list_units(addr)
    except DataSourceError:  # 호 목록이 없으면 직접 입력으로 대신한다
        return []


@st.cache_data(ttl=3600, show_spinner=False)
def cached_points(addr, comps):
    return comp_points(addr, comps)


@st.cache_data(ttl=3600, show_spinner="공공데이터를 조회하고 있어요…")
def cached_diagnose(*args, **kwargs):
    return diagnose(*args, **kwargs)


def show_map(points):
    """이 집(벽돌색) + 거래 지번(청회색, 거래가 많을수록 크게). 마우스를 올리면 지번·거래 수·㎡당 가격."""
    if not points:
        st.info("거래 위치의 좌표를 찾지 못했어요.")
        return
    center = next((p for p in points if p["home"]), points[0])
    for p in points:  # 점 크기(m): 이 집은 크게, 거래 지번은 거래 수에 따라. 화면에서는 아래 픽셀 범위로 묶는다
        p["r"] = 30 if p["home"] else 12 + 6 * min(p["count"], 8) ** 0.5
    layer = pdk.Layer("ScatterplotLayer", data=[p for p in points if not p["home"]] + [p for p in points if p["home"]],
                      get_position="[lon, lat]", get_radius="r", radius_min_pixels=5, radius_max_pixels=14,
                      pickable=True, stroked=True,  # radius_units="pixels"는 pydeck이 식으로 바꿔 깨진다
                      get_fill_color="home ? [165, 68, 45, 235] : [74, 101, 120, 170]",
                      get_line_color=[255, 253, 249], line_width_min_pixels=1.5)
    # 모든 점이 들어오는 확대 수준: 높이 320px에 보이는 위도 폭 ≈ 450 ÷ 2^zoom
    reach = max(max(abs(p["lat"] - center["lat"]), abs(p["lon"] - center["lon"]) * 0.8) for p in points) or 0.002
    zoom = max(11.0, min(16.0, math.log2(450 / (2.6 * reach))))
    st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=pdk.ViewState(latitude=center["lat"], longitude=center["lon"], zoom=zoom),
                             tooltip={"text": "{label}"}, map_style=pdk.map_styles.CARTO_LIGHT), height=320)
    st.caption("벽돌색 점이 이 집, 청회색 점이 거래한 지번이에요 (점이 클수록 거래가 많아요). 점에 마우스를 올리거나 눌러 보세요.")


def same_house(a, b):  # 같은 집·같은 조건이면 비교 목록에 두 번 담지 않는다
    return (a.address.pnu, a.dong, a.ho, a.deposit_won, a.monthly_rent_won) == \
        (b.address.pnu, b.dong, b.ho, b.deposit_won, b.monthly_rent_won)


# 링크(주소창)에 담긴 집·계약 정보. 나이·소득 같은 내 정보는 담지 않는다.
# 처음 열 때 한 번만 읽는다: 기본값이 바뀌면 Streamlit이 입력칸을 새로 만들어 방금 친 값이 사라진다
if "link" not in st.session_state:
    st.session_state["link"] = st.query_params.to_dict()
link = st.session_state["link"]


def link_num(name, default=0):
    try:
        v = float(link[name])
    except (KeyError, ValueError):
        return default
    return type(default)(v) if math.isfinite(v) and v >= 0 else default  # 고친 링크(inf·음수)로 입력칸이 깨지지 않게


# 1. 주소
keyword = st.text_input("집 주소 검색", value=link.get("q", ""), placeholder="예: 봉천동 1-70, 청림3다길 12")
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
            f"검색 결과 {len(results)}건 — 계약할 집을 골라 주세요", results,
            index=next((i for i, a in enumerate(results) if a.pnu == link.get("pnu")), None),
            placeholder="주소 선택",
            format_func=lambda a: f"{a.road_addr}" + (f" · {a.building_name}" if a.building_name else ""),
        )
        if len(results) >= SEARCH_LIMIT:
            st.caption(f"결과가 많아 {SEARCH_LIMIT}건까지만 보여요. 찾는 집이 없으면 번지·건물명까지 더 자세히 입력해 주세요.")

unit = None
if address is not None and (units := cached_units(address)):
    unit = st.selectbox(
        "호 선택", units,
        index=next((i for i, u in enumerate(units) if (u.dong, u.ho) == (link.get("dong", ""), link.get("ho"))), None),
        placeholder="호를 골라 주세요 (목록에 없으면 비워 두고 아래에 직접 입력)",
        format_func=lambda u: " ".join(filter(None, [u.dong, f"{u.ho}호"])) + (f" · 전용 {u.area_m2:g}㎡" if u.area_m2 else ""),
    )

reading = None
if address is not None:
    pdf = st.file_uploader("등기부등본 PDF (선택) — 올리면 근저당을 자동으로 채워요", type="pdf",
                           help="인터넷등기소(iros.go.kr)에서 열람·발급한 PDF. 사진·스캔본은 읽지 못해요. 파일은 저장하지 않아요.")
    if pdf is not None:
        try:
            reading = read_registry_pdf(pdf.getvalue())
        except DataSourceError as e:
            st.warning(str(e))
        else:
            lines = [f"- {l.rank}번 {l.kind} {man(l.amount_won)} 원" for l in reading.liens] or ["- 남아 있는 근저당·전세권이 없어요"]
            st.success("등기부에서 읽은 선순위 채권 (말소된 건 제외)\n" + "\n".join(lines)
                       + f"\n\n합계 **{man(reading.total_won)} 원**을 아래 칸에 채웠어요. 실제 등기부와 다르면 고쳐 주세요.")
            if reading.flags:
                st.error(f"등기부에 {'·'.join(reading.flags)} 기록이 있어요. 계약 전에 반드시 확인하세요.")

# 다른 집을 고르면 이전 진단 결과를 숨긴다
if (d := st.session_state.get("diagnosis")) is not None and address != d.address:
    del st.session_state["diagnosis"]

# 2. 계약·내 정보
# 폼(st.form)이 아니라 일반 카드: '소득 없음'을 누르는 즉시 소득칸을 0으로 잠가야 해서. 진단은 버튼을 눌러야만 한다
with st.container(key="contract"):
    if unit is None:
        c1, c2, c3 = st.columns(3)
        dong = c1.text_input("동 (없으면 비움)", value=link.get("dong", ""))
        ho = c2.text_input("호", value=link.get("ho", ""), placeholder="201")
        area = c3.number_input("전용면적 (㎡)", min_value=1.0, value=max(link_num("area", 40.0), 1.0), step=1.0)
    else:  # 공시자료의 면적을 기본값으로, 실제와 다르면 고칠 수 있게
        dong, ho = unit.dong, unit.ho
        linked = (unit.dong, unit.ho) == (link.get("dong", ""), link.get("ho"))
        area = st.number_input("전용면적 (㎡)", min_value=1.0, step=1.0,
                               value=max(link_num("area", 40.0), 1.0) if linked and "area" in link else unit.area_m2 or 40.0)

    c1, c2 = st.columns(2)
    deposit = c1.number_input("보증금 (만원)", min_value=0, value=link_num("dep"), step=500)
    rent = c2.number_input("월세 (만원, 전세면 0)", min_value=0, value=link_num("rent"), step=5)
    c1, c2 = st.columns([2, 1])
    debt = c1.number_input("선순위 채권·근저당 (만원)", min_value=0, step=500,
                           value=-(-reading.total_won // MAN) if reading else link_num("debt"),
                           help="등기부등본 을구의 채권최고액. 모르면 옆의 '모름'을 체크하면 0원으로 계산하고 경고해요.")
    # 체크박스에 help를 달면 ? 아이콘이 라벨 안에 들어가 누를 때 체크가 바뀐다 → 설명은 입력칸 쪽에
    debt_unknown = c2.checkbox("모름", value=reading is None and "debt" not in link)
    official = st.number_input(
        "공시가격 직접 입력 (만원, 선택)", min_value=0, value=link_num("off"), step=500,
        help="부동산공시가격알리미(realtyprice.kr)에서 확인할 수 있어요. 비우면 자동 조회를 시도해요.")

    st.markdown("**내 정보**")
    c1, c2 = st.columns(2)
    age = c1.number_input("만 나이", min_value=15, max_value=100, value=None, placeholder="예: 26")
    income = c2.number_input("세전 연소득 (만원, 기혼이면 부부 합산)", min_value=0, value=None, step=100, placeholder="예: 3000",
                             key="income", disabled=st.session_state.get("no_income", False))
    c1, c2, c3 = st.columns(3)
    married = c1.checkbox("기혼")
    homeless = c2.checkbox("무주택", value=True)
    no_income = c3.checkbox("소득 없음 (학생 등)", key="no_income",  # 체크하면 소득칸을 0원으로 잠근다
                            on_change=lambda: st.session_state.update(income=0 if st.session_state["no_income"] else None))
    submitted = st.button("진단하기", type="primary", use_container_width=True)

# 링크로 열면 한 번 자동으로 진단한다 (내 정보는 링크에 없어 지원 정책은 빼고)
from_link = (not st.session_state.get("link_opened") and "diagnosis" not in st.session_state
             and address is not None and address.pnu == link.get("pnu"))
st.session_state["link_opened"] = True
if from_link:
    submitted = True

if submitted and address is None:
    st.warning("먼저 위에서 주소를 검색하고 골라 주세요.")

if submitted and address is not None and deposit == 0 and rent == 0:
    st.warning("보증금이나 월세를 입력해 주세요. 둘 다 0이면 진단할 수 없어요.")
elif submitted and address is not None:
    income = 0 if no_income else income
    profile = None if age is None or income is None else Profile(  # 나이·소득을 모르면 정책은 찾지 않는다
        age=int(age), income_won=int(income) * MAN, married=married, homeless=homeless,
        deposit_won=int(deposit) * MAN, monthly_rent_won=int(rent) * MAN, area_m2=float(area) or None)
    senior = None if debt_unknown and debt == 0 else int(debt) * MAN  # 입력한 채권은 '모름'보다 우선
    st.session_state["senior_debt"] = senior or 0
    d = cached_diagnose(address, dong.strip(), ho.strip(), float(area), int(deposit) * MAN, int(rent) * MAN,
                        senior, profile,
                        manual_official_won=int(official) * MAN or None, today=date.today(),
                        registry_flags=tuple(reading.flags) if reading
                        else tuple(filter(None, link.get("reg", "").split(","))) if from_link else ())
    if set(d.errors) - {"official"}:  # 일시적 실패는 1시간 재사용하지 않는다. 공시가격은 배포 서버(해외)에서 늘 실패하니 제외 — 매번 다시 부르면 data.go.kr이 503
        cached_diagnose.clear()
    if not from_link:  # 새로 진단하면 링크를 갱신하고 체크리스트는 처음부터
        st.query_params.from_dict({k: v for k, v in {
            "q": keyword.strip(), "pnu": address.pnu, "dong": dong.strip(), "ho": ho.strip(), "area": f"{area:g}",
            "dep": deposit, "rent": rent, "debt": None if senior is None else debt, "off": official or None,
            "reg": ",".join(reading.flags) if reading and reading.flags else None,
        }.items() if v not in (None, "")})
    st.session_state["diagnosis"] = d

d = st.session_state.get("diagnosis")
if d is not None:
    r = d.risk

    # 1) 한 줄 결론
    st.divider()
    with st.container(key="addr-plate"):
        st.subheader(d.address.road_addr)
    if submitted:  # 방금 진단했으면 결과(집 주소판)로 내려간다. 슬라이더 등 다른 조작 때는 움직이지 않는다
        st.session_state["scrolls"] = st.session_state.get("scrolls", 0) + 1
        with st.container(key="scroll-js"):
            components.html(f"""<script>/* {st.session_state["scrolls"]} */
                const go = (n) => {{ const el = window.parent.document.querySelector('.st-key-addr-plate');
                  if (el) el.scrollIntoView({{behavior: 'smooth', block: 'start'}}); else if (n) setTimeout(() => go(n - 1), 100); }};
                go(30);</script>""", height=0)

    # 한눈 요약: 시세 대비 비율(막대) · 협상 목표 · 받을 수 있는 지원
    eligible_total = sum(m.policy["benefit_max_won"] or 0 for m in d.policies if m.status == "eligible")
    k1, k2, k3 = st.columns(3)
    k1.metric("시세 대비 보증금", "-" if r.deposit_ratio is None else f"{int(r.deposit_ratio * 100)}%",
              help="(보증금 + 선순위 채권) ÷ 추정 매매시세. 70% 이상 주의, 80% 이상 깡통전세 위험.")
    k2.metric("협상 목표 보증금", "-" if r.target_deposit_won is None else
              f"{man(r.target_deposit_won)} 원 이하" if d.deposit_won > r.target_deposit_won else "지금도 목표 이하",
              help="보증보험 한도와 추정 시세 × 70% 중 낮은 값에서 선순위 채권을 뺀 금액.")
    k3.metric("받을 수 있는 지원", f"{man(eligible_total)} 원" if d.policies_checked else "내 정보 필요",
              help="조건을 충족한 현금 지원의 최대 금액 합계. 나이·소득을 넣어야 찾을 수 있어요.")
    if r.deposit_ratio is not None:
        pct = min(int(r.deposit_ratio * 100), 100)
        st.markdown(  # 0~70% 무난 · 70~80% 주의 · 80% 이상 위험, 내 위치에 표시
            f'<div class="risk-bar" role="img" aria-label="시세 대비 {int(r.deposit_ratio * 100)}%, 70% 이상 주의, 80% 이상 위험">'
            f'<i style="left: {pct}%"></i><span style="left: 70%">70%</span><span style="left: 80%">80%</span></div>',
            unsafe_allow_html=True)
    top = r.signals[0].level if r.signals else "info"
    {"danger": st.error, "caution": st.warning}.get(top, st.info)(
        ("⚠️ " if top in ("danger", "caution") else "") + r.headline)

    # 2) 신호 카드
    st.markdown("#### 위험 신호")
    for i, s in enumerate(r.signals):
        with st.container(border=True, key=f"signal-{i}"):
            st.markdown(f"{ICONS[s.level]} **{s.title}**")
            st.write(s.detail)
            st.caption(f"출처: {s.source}")
    for key, msg in d.errors.items():
        if key != "official":  # 배포 서버(해외)에선 브이월드가 늘 막힌다 — 위 신호 카드가 직접 입력을 안내한다
            st.warning(f"{ERROR_LABELS.get(key, key)} — {msg}")
    if d.building:
        st.caption(f"건축물대장: {d.building.main_purpose} · {d.building.etc_purpose} · {d.building.households}세대 · "
                   f"{d.building.approval_year or '?'}년 사용승인")

    # 2-0) 시세 추이: 분기별 ㎡당 매매가 중간값 (오르는지 떨어지는지)
    if d.market is not None and len(points := quarterly_per_m2(d.market.comps)) >= 3:
        with st.container(border=True, key="trend"):
            st.markdown("**㎡당 매매가 추이** · 비슷한 집 거래의 분기별 중간값")
            data = alt.Data(values=[{"분기": q, "가격": round(v / MAN)} for q, v in points])
            base = alt.Chart(data).encode(
                x=alt.X("분기:O", title=None, sort=None, axis=alt.Axis(labelAngle=0, labelOverlap="greedy", labelSeparation=10, labelFlush=True, ticks=False)),
                y=alt.Y("가격:Q", title=None, scale=alt.Scale(zero=False, padding=12),  # 0부터 그리면 변화가 안 보인다
                        axis=alt.Axis(format=",d", labelExpr="datum.label + '만'", tickCount=4, gridColor="#eadfd3", domain=False)),
                tooltip=[alt.Tooltip("분기:O"), alt.Tooltip("가격:Q", title="㎡당 가격(만원)", format=",d")])
            st.altair_chart((base.mark_line(color="#a5442d", strokeWidth=2)
                             + base.mark_point(color="#a5442d", filled=True, size=70, stroke="#fffdf9", strokeWidth=2))
                            .properties(height=200).configure_view(stroke=None), use_container_width=True)
            if d.market.trend is not None:
                pct = round(d.market.trend * 100)
                st.caption(f"최근 1년 ㎡당 중간값이 그 전 1년보다 " +
                           (f"{-pct}% 떨어졌어요." if pct < 0 else f"{pct}% 올랐어요." if pct > 0 else "거의 그대로예요."))

    # 2-0) 시세 근거: 추정에 쓴 실거래를 그대로 보여 준다
    for est, what in ((d.market, "추정 매매 시세"), (d.jeonse, "주변 전세 시세")):
        if est is None or not est.comps:
            continue
        per = median(t.price_won / t.area_m2 for t in est.comps)
        with st.expander(f"📊 {what} {won(est.price_won)}의 근거 · 실거래 {est.comp_count}건"):
            st.caption(f"최근 {est.months}개월 " + ("같은 건물의 거래" if est.basis == "same_jibun" else
                       "같은 동의 비슷한 집(전용면적 ±20%, 건축년도 ±5년) 거래") +
                       f"예요. ㎡당 가격의 중간값 {won(round(per))} × 전용 {est.price_won / per:.2f}㎡ = {won(est.price_won)}. "
                       "출처: 국토교통부 실거래가 공개시스템")
            st.dataframe([{"계약": f"{t.ym[:4]}.{t.ym[4:]}", "지번": t.jibun, "전용(㎡)": t.area_m2,
                           "금액(만원)": t.price_won // MAN, "㎡당(만원)": round(t.price_won / t.area_m2 / MAN, 1),
                           "건축년도": t.build_year}
                          for t in sorted(est.comps, key=lambda t: t.ym, reverse=True)],
                         hide_index=True, use_container_width=True,
                         column_config={"금액(만원)": st.column_config.NumberColumn(format="localized")})

    # 시세 근거 거래의 위치 (펼침 칸 안에 두면 토글할 때 칸이 접혀서 밖에 둔다)
    if d.market is not None and d.market.comps and st.toggle(
            "📍 거래한 집들을 지도로 보기", key=f"map-{d.address.pnu}",
            help="추정 매매 시세에 쓴 거래의 지번을 지도에 찍어요. 처음 열 때 좌표를 찾느라 몇 초 걸려요."):
        try:
            with st.spinner("거래 위치를 찾고 있어요…"):
                pts = cached_points(d.address, d.market.comps)
        except DataSourceError as e:
            st.warning(f"지도를 그리지 못했어요. {e}")
        else:
            show_map(pts)

    # 2-1) 보증금을 바꿔 보면: 같은 집 데이터로 위험만 다시 계산한다 (공공데이터는 다시 부르지 않음)
    proposal, proposal_note = None, ""  # 협상 메시지에 넣을 (보증금, 월세)와 그 출처
    if d.deposit_won >= 100 * MAN and d.monthly_rent_won == 0 and d.market is not None:  # 전세 계약만
        st.markdown("#### 보증금을 낮추고 월세로 바꾸면")
        top_man = d.deposit_won // MAN
        key = f"whatif-{d.address.pnu}-{d.deposit_won}"  # 새로 진단하면 슬라이더도 처음부터
        target_man = r.target_deposit_won // MAN // 100 * 100 if r.target_deposit_won is not None else None  # 넘지 않게 내림
        new_man = st.slider("보증금 (만원)", min_value=0, max_value=top_man, value=top_man, step=100, key=key)
        if target_man is not None and target_man < top_man:
            st.button(f"협상 목표({man(r.target_deposit_won)} 원 이하)로 맞추기",
                      on_click=lambda: st.session_state.update({key: target_man}))
        rate = st.number_input("전월세 전환율 (%)", min_value=0.0, max_value=20.0, value=LEGAL_CONVERSION_PCT, step=0.5,
                               help="낮춘 보증금을 월세로 바꿀 때 쓰는 연 이율. 기본값은 법이 정한 상한 "
                                    "(한국은행 기준금리 3.00% + 2%p). 실제 월세는 집주인과 협상해 정해요.")
        cut = d.deposit_won - new_man * MAN
        new_rent = d.monthly_rent_won + rent_for_cut(cut, rate)
        changed = what_if(d, new_man * MAN, new_rent)
        if cut > 0:
            proposal = (new_man * MAN, new_rent)
            proposal_note = "제안 조건은 위 '보증금을 낮추고 월세로 바꾸면'에서 고른 값이에요."

        def terms(col, title, dep, rent, rep):
            lvl = rep.signals[0].level if rep.signals else "info"
            ratio = "-" if rep.deposit_ratio is None else f"{int(rep.deposit_ratio * 100)}%"  # 보증금 0원·시세 의심이면 비율 없음
            col.markdown(f"**{title}**  \n보증금 {won(dep)} · 월세 {won(rent)}  \n"
                         f"시세 대비 **{ratio}**  \n{ICONS[lvl]} {rep.headline}")

        c1, c2 = st.columns(2)
        terms(c1, "지금 조건", d.deposit_won, d.monthly_rent_won, r)
        terms(c2, "바꾼 조건", new_man * MAN, new_rent, changed)
        # 전세대출 이자 vs 월세: 내 돈은 그대로 두고, 보증금 중 모자란 만큼 대출받는다고 본다
        st.markdown("**전세대출을 받으면 매달 얼마?**")
        c1, c2 = st.columns(2)
        loan_man = c1.number_input("지금 조건에서 대출받을 금액 (만원)", min_value=0, max_value=top_man, step=500,
                                   value=default_loan(d.deposit_won) // MAN, key=f"loan-{key}",
                                   help="기본값은 청년전용 버팀목전세자금 한도: 보증금의 80% 이내, 최대 1억 5,000만 원.")
        loan_rate = c2.number_input("대출 금리 (%)", min_value=0.0, max_value=20.0, value=YOUTH_LOAN_RATE_PCT, step=0.1,
                                    key=f"loan-rate-{key}",
                                    help="청년전용 버팀목전세자금은 연 2.2~3.3% (소득·보증금에 따라 달라요, 주택도시기금 2026-11 확인). "
                                         "기본값은 그 가운데예요. 은행 전세대출이면 그 금리를 넣으세요.")
        own = d.deposit_won - loan_man * MAN  # 내 돈
        now_cost = monthly_interest(loan_man * MAN, loan_rate)
        new_loan = max(new_man * MAN - own, 0)
        new_cost = monthly_interest(new_loan, loan_rate) + new_rent
        line = f"지금 조건: 월 대출이자 **{won(now_cost)}**"
        if cut > 0:
            line += (f"  \n바꾼 조건: 월 대출이자 {won(monthly_interest(new_loan, loan_rate))} + 월세 {won(new_rent)} = "
                     f"**{won(new_cost)}**  \n1년이면 " +
                     (f"바꾼 조건이 **{won((new_cost - now_cost) * 12)} 더** 들어요" if new_cost > now_cost else
                      f"바꾼 조건이 **{won((now_cost - new_cost) * 12)} 덜** 들어요" if new_cost < now_cost else "똑같아요"))
        else:
            line += "  \n위 슬라이더로 보증금을 낮추면 '대출이자 + 월세'와 비교해 드려요."
        st.markdown(line)
        st.caption(f"내 돈 {won(max(own, 0))}은 두 조건 모두 보증금에 넣고, 모자란 만큼만 대출받는다고 계산했어요. "
                   "내 돈을 예금했을 때의 이자는 빼고 계산했어요.")
        if cut > 0:
            st.caption(f"보증금을 {won(cut)} 낮추면 월세가 {won(new_rent - d.monthly_rent_won)} 늘어요 (전환율 {rate:g}%). "
                       f"1년에 {won((new_rent - d.monthly_rent_won) * 12)}을 더 내는 대신, 돌려받지 못할 수 있는 돈이 {won(cut)} 줄어요.")

    # 2-2) 계약할 때 쓸 문구: 특약과 협상 메시지 (숫자는 진단 결과에서, AI는 말투만)
    if proposal is None and r.target_deposit_won is not None and d.deposit_won > r.target_deposit_won:
        safe = r.target_deposit_won // (100 * MAN) * 100 * MAN  # 목표를 넘지 않게 100만 원 단위로 내림
        if safe > 0:
            proposal = (safe, d.monthly_rent_won + rent_for_cut(d.deposit_won - safe, LEGAL_CONVERSION_PCT))
            proposal_note = (f"제안 보증금은 협상 목표({man(r.target_deposit_won)} 원) 이하로 맞추고, 낮춘 만큼 월세는 "
                             f"법정 상한 전환율 {LEGAL_CONVERSION_PCT:g}%로 계산했어요."
                             + (" 위 슬라이더로 바꿀 수 있어요." if d.monthly_rent_won == 0 and d.market is not None else ""))
    terms_list = special_terms(d)
    with st.container(key="phrases"):
        st.markdown("#### 계약할 때 쓸 문구")
        st.caption("진단 결과로 만든 문구예요. 오른쪽 위 복사 단추로 복사해 쓰세요. 법률 자문이 아니니 공인중개사와 함께 확인하세요.")
        tab1, tab2 = st.tabs(["계약서 특약", "집주인·중개사에게 보낼 메시지"])
        tab1.code("\n".join(f"{i}. {t}" for i, t in enumerate(terms_list, 1)), language=None, wrap_lines=True)
        with tab2:
            msg = negotiation_message(d, *proposal) if proposal else negotiation_message(d)
            if proposal_note:
                st.caption(proposal_note)
            polished = st.session_state.setdefault("polished", {}).get(msg)
            if agent.available() and polished is None and st.button(
                    "✨ AI로 말투 다듬기", help="숫자와 특약 문장은 그대로 두고 말투만 다듬어요. 숫자가 바뀌면 원래 문장을 보여 드려요."):
                with st.spinner("AI가 문장을 다듬고 있어요…"):
                    try:
                        polished = st.session_state["polished"][msg] = agent.polish(msg)
                    except agent.AgentError as e:
                        st.warning(str(e))
            st.code(polished or msg, language=None, wrap_lines=True)
            if polished:
                st.caption("AI가 다듬은 문장이에요. 원래 문장의 숫자가 그대로 있고 새 숫자가 없는지 코드로 확인했어요.")

    # 3) 지원
    st.markdown("#### 받을 수 있는 지원")
    if not d.policies_checked:
        st.info("위 '내 정보'에 만 나이와 세전 연소득(소득이 없으면 '소득 없음' 체크)을 넣고 다시 진단하면 "
                "받을 수 있는 지원을 찾아 드려요.")
    elif not d.policies:
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
    if d.youth:
        with st.expander(f"🗂️ 이 지역의 다른 청년 주거 정책 {len(d.youth)}개"):
            st.markdown("출처: 온통청년 청년정책 API. 자격은 자동으로 따지지 않았으니 링크에서 조건을 확인하세요.")
            for y in d.youth:
                first = next(iter(y.support.lstrip("-•○ \n").splitlines()), "")[:80]
                st.markdown(f"**[{y.name}]({y.url})** · {y.org} · 신청 {y.period}" + (f"  \n{first}" if first else ""))

    # 4) 체크리스트
    st.markdown("#### 해야 할 일")
    items = []
    if r.target_deposit_won is not None and d.deposit_won > r.target_deposit_won:  # 이미 목표 이하면 협상할 게 없다
        parts = []
        if r.guarantee_limit_won is not None:
            parts.append(f"보증보험 한도 {man(max(r.guarantee_limit_won, 0))} 원 (공시가격 × 126% − 선순위 채권)")
        if d.market is not None:
            parts.append(f"추정 시세 × 70% − 선순위 채권 = {man(d.market.price_won * 7 // 10 - st.session_state.get('senior_debt', 0))} 원")
        items.append(f"보증금 협상 목표: **{man(r.target_deposit_won)} 원 이하** — " + " 와 ".join(parts) + " 중 낮은 값")
    items += [
        "등기부등본(을구)에서 근저당·선순위 채권 확인",
        "건축물대장에서 위반건축물 여부 확인 (자동 조회 항목에 없어 직접 확인이 필요해요)",
        *landlord_checks(d),
        "계약서에 위 '계약서 특약' 문구 넣기",
    ]
    done = set(link.get("done", "").split(",")) - {""} if d.address.pnu == link.get("pnu") else set()  # 링크 속 집일 때만
    checked = [str(i) for i, it in enumerate(items) if st.checkbox(it, value=str(i) in done)]
    if set(checked) != set(st.query_params.get("done", "").split(",")) - {""}:  # 체크한 항목을 링크에 저장해 새로고침해도 남게
        if checked:
            st.query_params["done"] = ",".join(checked)
        else:
            del st.query_params["done"]

    # 5) 저장·공유
    st.markdown("#### 저장·공유")
    st.download_button(
        "결과 파일로 저장 (.html)", build_report(d, items, {int(i) for i in checked}, DISCLAIMER, date.today(), terms_list),
        file_name=f"집진단_{date.today():%Y%m%d}.html", mime="text/html",
        help="폰·PC 어디서나 열려요. 파일을 열고 인쇄하면 PDF로도 저장할 수 있어요.")
    if st.context.url:
        st.code(f"{st.context.url}?{urlencode(st.query_params.to_dict())}", language=None)
        st.caption("이 링크를 복사해 보내면 같은 집·계약 조건으로 열려요. 나이·소득 같은 내 정보는 링크에 들어가지 않아요.")
    saved = st.session_state.setdefault("compare", [])
    if any(same_house(x, d) for x in saved):
        st.button("비교 목록에 담겼어요", disabled=True)
    else:
        st.button("후보 집 비교 목록에 담기", help=f"다른 집도 진단해 담으면 아래에서 나란히 비교할 수 있어요 (최대 {MAX_COMPARE}곳).",
                  on_click=lambda: st.session_state.update(compare=[*saved, d][-MAX_COMPARE:]))

# 6) 후보 집 비교: 이번 접속에서 담은 집들 (새 진단을 해도 남는다)
if saved := st.session_state.get("compare"):
    st.markdown("#### 후보 집 비교")
    ratios = [x.risk.deposit_ratio for x in saved]
    best = min((v for v in ratios if v is not None), default=None)

    def cell_ratio(v):
        return "-" if v is None else f"**{int(v * 100)}%** (가장 낮음)" if v == best and len(saved) > 1 else f"{int(v * 100)}%"

    def cell_guarantee(x):
        lim = x.risk.guarantee_limit_won
        return "-" if lim is None else "한도 안" if x.deposit_won <= lim else f"{won(x.deposit_won - max(lim, 0))} 초과"

    def cell_target(x):
        t = x.risk.target_deposit_won
        return "-" if t is None else "지금도 목표 이하" if x.deposit_won <= t else f"{won(t)} 이하"

    def cell_terms(x):
        return (f"전세 {won(x.deposit_won)}" if x.monthly_rent_won == 0
                else f"보증금 {won(x.deposit_won)} / 월세 {won(x.monthly_rent_won)}")

    def cell_verdict(x):
        lvl = x.risk.signals[0].level if x.risk.signals else "info"
        return f"{ICONS[lvl]} {x.risk.headline}"

    names = [" ".join(filter(None, [x.address.building_name or x.address.road_addr or x.address.jibun_addr, x.dong,
                                    f"{x.ho}호" if x.ho else ""])) for x in saved]
    rows = [("조건", [cell_terms(x) for x in saved]), ("시세 대비", [cell_ratio(v) for v in ratios]),
            ("보증보험", [cell_guarantee(x) for x in saved]), ("협상 목표", [cell_target(x) for x in saved]),
            ("판정", [cell_verdict(x) for x in saved])]
    with st.container(key="compare-table"):
        st.markdown("\n".join([f"| | {' | '.join(names)} |", "|---" * (len(saved) + 1) + "|",
                               *[f"| **{label}** | {' | '.join(cells)} |" for label, cells in rows]]))
    st.caption("시세 대비가 낮고 보증보험 한도 안인 집일수록 보증금을 돌려받기 쉬워요.")
    if st.button("비교 목록 비우기"):
        st.session_state["compare"] = []
        st.rerun()

st.divider()
st.caption(DISCLAIMER)
