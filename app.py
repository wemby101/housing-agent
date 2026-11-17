"""청년 집 구하기 진단 — 폼 모드 (Streamlit)."""
from datetime import date
from urllib.parse import urlencode
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from housing import agent
from housing.address import search_address
from housing.errors import DataSourceError
from housing.official_price import list_units
from housing.pipeline import LEGAL_CONVERSION_PCT, diagnose, rent_for_cut, what_if
from housing.policies import Profile
from housing.registry import read_registry_pdf
from housing.report import build_report
from housing.risk import man

DISCLAIMER = "참고용 진단입니다. 계약 전 공인중개사·HUG(1566-9009) 상담으로 확인하세요."
MAN = 10_000
SEARCH_LIMIT = 20
ERROR_LABELS = {"building": "건축물대장", "official": "공시가격", "market": "실거래가 시세", "jeonse": "주변 전세 시세", "youth": "온통청년 정책"}

st.set_page_config(page_title="청년 집 구하기 진단", page_icon="🏠")
st.markdown(f"<style>{(Path(__file__).parent / 'style.css').read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)
with st.container(key="hero"):
    st.title("청년 집 구하기 진단")
    st.caption("계약 전에 이 집의 위험 신호와 내가 받을 수 있는 주거 지원을 한 번에 확인해요. 서울·경기·인천의 빌라(연립·다세대)와 아파트 대상.")

if agent.available():  # 키가 없으면 폼만 보인다
    with st.expander("💬 AI에게 말로 물어보기", expanded=bool(st.session_state.get("chat"))):
        st.caption("예: \"봉천동 1-70 201호 전세 2억 2천인데 괜찮아? 나는 27살, 연봉 3천이야\"  \n"
                   "숫자는 공공데이터로 계산한 값만 써요. 무료 AI를 쓰고 있어 입력한 내용이 AI 제공사에 전달돼요.")
        chat = st.session_state.setdefault("chat", [])
        talk = st.container()  # 새 질문·답도 입력칸 위에 이어 붙도록
        for m in chat:
            if m["role"] in ("user", "assistant") and m.get("content"):
                talk.chat_message(m["role"]).markdown(m["content"])
        if question := st.chat_input("집 주소와 계약 조건을 적어 주세요"):
            talk.chat_message("user").markdown(question)
            with talk.chat_message("assistant"), st.spinner("AI가 공공데이터를 조회하고 있어요…"):
                try:
                    answer, st.session_state["chat"] = agent.reply([*chat, {"role": "user", "content": question}])
                    st.markdown(answer)
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


@st.cache_data(ttl=3600, show_spinner="공공데이터를 조회하고 있어요…")
def cached_diagnose(*args, **kwargs):
    return diagnose(*args, **kwargs)


# 링크(주소창)에 담긴 집·계약 정보. 나이·소득 같은 내 정보는 담지 않는다.
# 처음 열 때 한 번만 읽는다: 기본값이 바뀌면 Streamlit이 입력칸을 새로 만들어 방금 친 값이 사라진다
if "link" not in st.session_state:
    st.session_state["link"] = st.query_params.to_dict()
link = st.session_state["link"]


def link_num(name, default=0):
    try:
        return type(default)(float(link[name]))
    except (KeyError, ValueError):
        return default


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
        area = c3.number_input("전용면적 (㎡)", min_value=1.0, value=link_num("area", 40.0), step=1.0)
    else:  # 공시자료의 면적을 기본값으로, 실제와 다르면 고칠 수 있게
        dong, ho = unit.dong, unit.ho
        linked = (unit.dong, unit.ho) == (link.get("dong", ""), link.get("ho"))
        area = st.number_input("전용면적 (㎡)", min_value=1.0, step=1.0,
                               value=link_num("area", 40.0) if linked and "area" in link else unit.area_m2 or 40.0)

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
    if d.errors:  # 일시적 실패를 1시간 동안 재사용하지 않도록
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
    icons = {"danger": "🔴", "caution": "🟠", "info": "⚪"}
    for i, s in enumerate(r.signals):
        with st.container(border=True, key=f"signal-{i}"):
            st.markdown(f"{icons[s.level]} **{s.title}**")
            st.write(s.detail)
            st.caption(f"출처: {s.source}")
    for key, msg in d.errors.items():
        st.warning(f"{ERROR_LABELS.get(key, key)} — {msg}")
    if d.building:
        st.caption(f"건축물대장: {d.building.main_purpose} · {d.building.etc_purpose} · {d.building.households}세대 · "
                   f"{d.building.approval_year or '?'}년 사용승인")

    # 2-1) 보증금을 바꿔 보면: 같은 집 데이터로 위험만 다시 계산한다 (공공데이터는 다시 부르지 않음)
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
        alt = what_if(d, new_man * MAN, new_rent)

        def won(x):  # 29.2만 원처럼 월세는 소수 한 자리까지
            return f"{x / MAN:,.1f}만 원".replace(".0만", "만")

        def terms(col, title, dep, rent, rep):
            lvl = rep.signals[0].level if rep.signals else "info"
            ratio = "-" if rep.deposit_ratio is None else f"{int(rep.deposit_ratio * 100)}%"  # 보증금 0원·시세 의심이면 비율 없음
            col.markdown(f"**{title}**  \n보증금 {won(dep)} · 월세 {won(rent)}  \n"
                         f"시세 대비 **{ratio}**  \n{icons[lvl]} {rep.headline}")

        c1, c2 = st.columns(2)
        terms(c1, "지금 조건", d.deposit_won, d.monthly_rent_won, r)
        terms(c2, "바꾼 조건", new_man * MAN, new_rent, alt)
        if cut > 0:
            st.caption(f"보증금을 {won(cut)} 낮추면 월세가 {won(new_rent - d.monthly_rent_won)} 늘어요 (전환율 {rate:g}%). "
                       f"1년에 {won((new_rent - d.monthly_rent_won) * 12)}을 더 내는 대신, 돌려받지 못할 수 있는 돈이 {won(cut)} 줄어요.")

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
        "특약 넣기: \"잔금일 다음 날까지 근저당 등 권리 변동을 하지 않는다\"",
        "특약 넣기: \"전세보증보험 가입이 거절되면 계약을 해제하고 계약금을 돌려준다\"",
    ]
    done = set(link.get("done", "").split(",")) - {""}
    checked = [str(i) for i, it in enumerate(items) if st.checkbox(it, value=str(i) in done)]
    if set(checked) != set(st.query_params.get("done", "").split(",")) - {""}:  # 체크한 항목을 링크에 저장해 새로고침해도 남게
        if checked:
            st.query_params["done"] = ",".join(checked)
        else:
            del st.query_params["done"]

    # 5) 저장·공유
    st.markdown("#### 저장·공유")
    st.download_button(
        "결과 파일로 저장 (.html)", build_report(d, items, {int(i) for i in checked}, DISCLAIMER, date.today()),
        file_name=f"집진단_{date.today():%Y%m%d}.html", mime="text/html",
        help="폰·PC 어디서나 열려요. 파일을 열고 인쇄하면 PDF로도 저장할 수 있어요.")
    if st.context.url:
        st.code(f"{st.context.url}?{urlencode(st.query_params.to_dict())}", language=None)
        st.caption("이 링크를 복사해 보내면 같은 집·계약 조건으로 열려요. 나이·소득 같은 내 정보는 링크에 들어가지 않아요.")

st.divider()
st.caption(DISCLAIMER)
