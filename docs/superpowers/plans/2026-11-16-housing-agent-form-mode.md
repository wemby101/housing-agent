# 청년 집 구하기 에이전트 — 폼 모드(v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 주소·호수·면적·보증금·월세·나이·소득을 넣으면 공공데이터로 계산한 위험 신호, 받을 수 있는 지원, 해야 할 일을 보여주는 Streamlit 웹앱(AI 없이 동작).

**Architecture:** `housing/` 패키지에 데이터 출처별 클라이언트(주소·건축물대장·공시가격·실거래가)와 순수 계산 함수(위험 판정·정책 매칭)를 두고, `housing/pipeline.py`의 `diagnose()`가 이를 묶는다. `app.py`(Streamlit)는 `diagnose()`만 호출한다. 다음 계획(AI 대화 모드)은 같은 `diagnose()`와 개별 함수들을 도구로 쓴다.

**Tech Stack:** Python 3.11 (`/opt/homebrew/bin/python3.11` venv), requests, python-dotenv, streamlit, pytest.

**Spec:** `docs/design.md`

**이 계획의 범위 밖:** AI 대화 모드(AI 키 결정 후 별도 계획, 11/20 전), 온통청년 API 연동(승인 후 별도 작업), 오피스텔.

## Global Constraints

- 모든 금액은 내부에서 **원(int)**. 화면 입력·표시는 **만원**. 실거래가 `dealAmount`는 쉼표가 들어간 만원 문자열(`"12,500"`).
- 키는 `.env`에서만 읽는다: `JUSO_API_KEY`, `DATA_GO_KR_KEY`, `VWORLD_API_KEY`. 코드·테스트·커밋에 키 값 금지. `.env`는 `.gitignore`에 있음.
- 결과 문구에 "안전합니다"를 쓰지 않는다. 위험 신호가 없을 때 헤드라인은 정확히: `확인된 위험 신호가 없어요. 단, 등기부등본은 꼭 직접 확인하세요.`
- 모든 화면에 고지문: `참고용 진단입니다. 계약 전 공인중개사·HUG(1566-9009) 상담으로 확인하세요.`
- 보증보험 한도 = `floor(공시가격 × 1.26) − 선순위채권`. 보증금 비율 = 보증금 ÷ 추정 매매시세. `≥ 0.8` 위험, `0.7 ≤ r < 0.8` 주의.
- 협상 목표 보증금 = 위 한도와 `floor(추정시세 × 0.7)` 중 있는 값들의 최솟값.
- 추정 시세: 최근 24개월, 같은 지번 매매 2건 이상이면 그것으로. 아니면 같은 법정동에서 전용면적 ±20%, 건축년도 ±5년. 그래도 2건 미만이면 시세 없음. 해제된 거래(`cdealType == "O"`)는 제외. 추정값 = 비교 거래 ㎡당 가격의 **중앙값** × 사용자 전용면적.
- 범위: 서울, 연립·다세대.
- API 응답은 실제 호출로 녹화한 fixture로 테스트한다(`tests/fixtures/`). 테스트는 네트워크를 쓰지 않는다.

## Review Focus

1. **키 미활성·API 오류**: data.go.kr은 `_type=json`이어도 오류를 XML(`SERVICE_KEY_IS_NOT_REGISTERED_ERROR`)로 준다 → `DataSourceError`로 올리고, 앱은 해당 카드만 "데이터를 가져오지 못했어요"로 표시하고 나머지는 계속 보여준다. (Task 2, 6에 테스트)
2. **보증금 0인 순수 월세**: 보증금 비율·보증보험 위험 신호를 내지 않고 헤드라인이 위험을 말하지 않아야 한다. (Task 5에 테스트)
3. **산 번지 주소**(`mtYn == "1"`): `platGbCd = "1"`, PNU 11번째 자리 `"2"`. (Task 2에 테스트)
4. **주소 검색 결과 0건/여러 건**: 0건이면 "주소를 찾을 수 없어요", 여러 건이면 사용자가 고른다(자동 첫 번째 선택 금지). (Task 2·7)
5. **금액 단위 혼동**: `"12,500"`(만원) → 125,000,000원. (Task 4에 테스트)

---

### Task 1: 프로젝트 뼈대와 설정 로딩

**Files:**
- Create: `requirements.txt`, `housing/__init__.py`, `housing/config.py`, `housing/errors.py`, `tests/__init__.py`, `tests/test_config.py`
- Modify: `.gitignore` (add `.venv/`, `.cache/`, `__pycache__/`)

**Interfaces:**
- Produces: `get_key(name: str) -> str` (없으면 `DataSourceError(f"{name} 키가 .env에 없어요")`), `class DataSourceError(Exception)` in `housing/errors.py`.

- [ ] **Step 1:** `git init` in `housing-agent/`, `/opt/homebrew/bin/python3.11 -m venv .venv`, `requirements.txt` = `requests`, `python-dotenv`, `streamlit`, `pytest` → `.venv/bin/pip install -r requirements.txt`. Expected: install succeeds.
- [ ] **Step 2: Write failing test** `tests/test_config.py`:
```python
def test_get_key_reads_env(monkeypatch):
    monkeypatch.setenv("JUSO_API_KEY", "abc")
    assert get_key("JUSO_API_KEY") == "abc"

def test_get_key_missing_raises(monkeypatch):
    monkeypatch.delenv("VWORLD_API_KEY", raising=False)
    with pytest.raises(DataSourceError, match="VWORLD_API_KEY"):
        get_key("VWORLD_API_KEY")
```
- [ ] **Step 3:** Run `.venv/bin/pytest tests/test_config.py -v` → FAIL (import error).
- [ ] **Step 4:** Implement `get_key` (`load_dotenv()` once at import, then `os.environ`; empty string counts as missing).
- [ ] **Step 5:** Run → PASS. Commit `chore: project skeleton and key loading`.

### Task 2: 주소 → 코드 변환 (도로명주소 API)

**Files:**
- Create: `housing/address.py`, `tests/test_address.py`, `tests/fixtures/juso_seoul_cityhall.json`, `scripts/record_fixture.py`

**Interfaces:**
- Consumes: `get_key`, `DataSourceError`.
- Produces:
```python
@dataclass(frozen=True)
class Address:
    road_addr: str; jibun_addr: str; building_name: str
    sigungu_cd: str   # 5자리, admCd[:5]
    bjdong_cd: str    # 5자리, admCd[5:]
    emd_name: str     # emdNm, 실거래가 umdNm과 비교
    plat_gb_cd: str   # "0" 일반, "1" 산
    bun: str; ji: str # 4자리 0채움
    pnu: str          # admCd + ("2" if 산 else "1") + bun + ji (19자리)
def search_address(keyword: str, limit: int = 10) -> list[Address]
def parse_juso_row(row: dict) -> Address
```
Endpoint: `https://business.juso.go.kr/addrlink/addrLinkApi.do` (`confmKey`, `keyword`, `currentPage=1`, `countPerPage`, `resultType=json`). `results.common.errorCode != "0"` → `DataSourceError(errorMessage)`.

- [ ] **Step 1:** Write `scripts/record_fixture.py <name> <url-with-{KEY_NAME}-placeholders>`: fills keys from `.env`, saves raw body to `tests/fixtures/<name>`, and **refuses to save if any key value appears in the body**. Record `juso_seoul_cityhall.json` with keyword `서울시청`.
- [ ] **Step 2: Failing tests:**
```python
def test_parse_cityhall_row():
    row = load_fixture("juso_seoul_cityhall.json")["results"]["juso"][0]
    a = parse_juso_row(row)  # 서울시청 서소문별관
    assert (a.sigungu_cd, a.bjdong_cd, a.bun, a.ji, a.plat_gb_cd) == ("11140", "16600", "0037", "0000", "0")
    assert a.pnu == "1114016600100370000"
    assert row["bdMgtSn"].startswith(a.pnu)

def test_parse_san_lot():
    row = {**base_row, "mtYn": "1", "lnbrMnnm": "12", "lnbrSlno": "3"}
    a = parse_juso_row(row)
    assert a.plat_gb_cd == "1" and a.pnu[10] == "2" and a.bun == "0012" and a.ji == "0003"

def test_search_error_code_raises(monkeypatch):  # errorCode "E0001"
    ... with pytest.raises(DataSourceError): search_address("x")

def test_search_zero_results_returns_empty(monkeypatch):
    ... assert search_address("없는주소") == []
```
- [ ] **Step 3:** Run `pytest tests/test_address.py -v` → FAIL. **Step 4:** implement. **Step 5:** PASS. Commit `feat: address search and code conversion`.

### Task 3: 건축물대장 표제부와 공시가격

**Files:**
- Create: `housing/datagokr.py`, `housing/building.py`, `housing/official_price.py`, `tests/test_building.py`, `tests/test_official_price.py`, fixtures `bldg_title_bongcheon_1_70.json`, `datagokr_key_error.xml`, `vworld_price_<sample>.json`

**Interfaces:**
- Consumes: `Address`, `get_key`, `DataSourceError`.
- Produces:
```python
# datagokr.py — data.go.kr 공통 호출
def call_datagokr(url: str, params: dict) -> dict  # JSON 파싱; 본문이 '<'로 시작하면 errMsg/returnAuthMsg로 DataSourceError; resultCode != "00"도 DataSourceError
def items_of(body: dict) -> list[dict]             # items가 "" / dict / list 어느 형태든 list로

@dataclass(frozen=True)
class BuildingInfo:
    main_purpose: str; etc_purpose: str; households: int
    approval_year: int | None   # useAprDay[:4]
    is_residential: bool        # main_purpose in {"공동주택", "단독주택"}
def get_building(addr: Address) -> BuildingInfo | None  # getBrTitleInfo, mainAtchGbCdNm=="주건축물" 첫 행; 0건이면 None

@dataclass(frozen=True)
class OfficialPrice:
    price_won: int; year: int; source: str  # "브이월드 공동주택가격" | "사용자 입력"
def get_official_price(addr: Address, dong: str, ho: str) -> OfficialPrice | None  # 최신 연도 1건; 없으면 None
```
건축물대장 endpoint `https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo` (`sigunguCd, bjdongCd, platGbCd, bun, ji, _type=json, numOfRows=100`). 공시가격 endpoint `https://api.vworld.kr/ned/data/getApartHousingPriceAttr` (PNU·동·호 기반).

- [ ] **Step 1:** Record fixtures: 봉천동 1-70 표제부(`11620/10100/0/0001/0070`), a key-error XML (call with an invalid key), and — **once the user adds `VWORLD_API_KEY`** — one VWorld price response for a real Seoul 다세대. Read the VWorld response and the VWorld API reference page to pin the request parameter names and the price/year/동/호 field names; write them as constants at the top of `official_price.py`. If `VWORLD_API_KEY` is not yet available, implement `get_building` now and mark Steps for `get_official_price` blocked; Task 6 handles `None`.
- [ ] **Step 2: Failing tests:**
```python
def test_building_bongcheon(): # fixture
    b = get_building(addr_bongcheon)
    assert b.main_purpose == "공동주택" and b.etc_purpose == "다세대주택"
    assert b.households == 9 and b.approval_year == 2003 and b.is_residential

def test_neighborhood_facility_not_residential():  # main_purpose "제2종근린생활시설"
    assert not get_building(addr).is_residential

def test_datagokr_xml_error_raises():
    with pytest.raises(DataSourceError, match="SERVICE_KEY_IS_NOT_REGISTERED_ERROR"): ...

def test_items_of_shapes():
    assert items_of({"items": ""}) == [] and items_of({"items": {"item": {"a": 1}}}) == [{"a": 1}]

def test_official_price_latest_year():  # fixture with ≥2 years → picks max year, price_won int
```
- [ ] **Step 3–5:** run FAIL → implement → PASS. Commit `feat: building register and official price`.

### Task 4: 실거래가 기반 추정 시세

**Files:**
- Create: `housing/market.py`, `tests/test_market.py`, fixture `rh_trade_11620_<YYYYMM>.json` (1개월)

**Interfaces:**
- Consumes: `Address`, `call_datagokr`, `items_of`.
- Produces:
```python
@dataclass(frozen=True)
class Trade: price_won: int; area_m2: float; build_year: int | None; umd_name: str; jibun: str; ym: str
@dataclass(frozen=True)
class MarketEstimate:
    price_won: int; comp_count: int
    basis: str      # "same_jibun" | "same_dong"
    months: int     # 24
def parse_trade(item: dict) -> Trade | None          # 해제 거래(cdealType=="O")는 None
def fetch_trades(lawd_cd: str, ym: str) -> list[Trade]  # 연립다세대 매매, 전 페이지; .cache/rh_<lawd>_<ym>.json 디스크 캐시 (지난달 이전만 캐시)
def estimate_market_price(trades: list[Trade], addr: Address, area_m2: float, build_year: int | None) -> MarketEstimate | None  # 순수 함수
def get_market_price(addr: Address, area_m2: float, build_year: int | None, today: date) -> MarketEstimate | None  # 최근 24개월 fetch → estimate
```
Endpoint `https://apis.data.go.kr/1613000/RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade` (`LAWD_CD`, `DEAL_YMD`, `numOfRows=1000`, `pageNo`). 지번 비교: 주소의 지번 문자열 = `str(int(bun))` + (`"-"+str(int(ji))` if ji != "0000"). Field names (`dealAmount`, `excluUseAr`, `buildYear`, `umdNm`, `jibun`, `cdealType`, `dealYear`, `dealMonth`) — confirm against the recorded fixture before writing `parse_trade`; this key was not yet active on 11/16.

- [ ] **Step 1:** Record one month fixture for 관악구(11620).
- [ ] **Step 2: Failing tests** (synthetic `Trade` lists for estimate):
```python
def test_parse_amount_with_comma():
    assert parse_trade({**item, "dealAmount": "12,500"}).price_won == 125_000_000

def test_cancelled_trade_excluded():
    assert parse_trade({**item, "cdealType": "O"}) is None

def test_same_jibun_basis_uses_median_per_m2():
    # 같은 지번 3건: ㎡당 400만, 500만, 600만 → 중앙값 500만 × 40㎡ = 2억
    e = estimate_market_price(trades, addr_1_70, 40.0, 2003)
    assert (e.basis, e.comp_count, e.price_won) == ("same_jibun", 3, 200_000_000)

def test_falls_back_to_same_dong_with_area_and_year_window():
    # 같은 지번 1건뿐; 같은 동 후보 중 면적 ±20%·건축년도 ±5년만 포함, 범위 밖 2건 제외
    assert e.basis == "same_dong" and e.comp_count == 2

def test_fewer_than_two_comps_returns_none():
    assert estimate_market_price([one_trade], addr, 40.0, 2003) is None
```
- [ ] **Step 3–5:** FAIL → implement → PASS. Commit `feat: market price estimate from transactions`.

### Task 5: 위험 판정 (순수 함수)

**Files:**
- Create: `housing/risk.py`, `tests/test_risk.py`

**Interfaces:**
- Produces:
```python
@dataclass(frozen=True)
class Signal:
    code: str    # "GUARANTEE_BLOCKED" | "HIGH_RATIO" | "CAUTION_RATIO" | "NOT_RESIDENTIAL" | "CHECK_REGISTRY" | "NO_MARKET_DATA" | "NO_OFFICIAL_PRICE"
    level: str   # "danger" | "caution" | "info"
    title: str; detail: str; source: str
@dataclass(frozen=True)
class RiskReport:
    headline: str; signals: list[Signal]
    deposit_ratio: float | None; guarantee_limit_won: int | None; target_deposit_won: int | None
def assess_risk(deposit_won: int, senior_debt_won: int | None, official: OfficialPrice | None,
                market: MarketEstimate | None, building: BuildingInfo | None) -> RiskReport
```
Signal 순서: danger → caution → info. `headline` = 첫 danger/caution 신호의 `title`, 없으면 Global Constraints의 고정 문구. `senior_debt_won is None` → 0으로 계산 + `CHECK_REGISTRY`(caution). `deposit_won == 0` → 보증보험·비율 신호 없음, `target_deposit_won = None`.

- [ ] **Step 1: Failing tests:**
```python
def test_guarantee_blocked_example():
    r = assess_risk(220_000_000, 0, OfficialPrice(160_000_000, 2026, "x"), None, residential)
    assert r.guarantee_limit_won == 201_600_000
    assert r.signals[0].code == "GUARANTEE_BLOCKED" and "1,840만" in r.signals[0].title
    assert r.target_deposit_won == 201_600_000

def test_ratio_thresholds():
    assert codes(assess_risk(80_000_000, 0, None, market(100_000_000), residential)) >= {"HIGH_RATIO"}
    assert codes(assess_risk(70_000_000, 0, None, market(100_000_000), residential)) >= {"CAUTION_RATIO"}
    assert not codes(assess_risk(69_000_000, 0, None, market(100_000_000), residential)) & {"HIGH_RATIO", "CAUTION_RATIO"}

def test_target_is_min_of_limit_and_70pct():
    r = assess_risk(150_000_000, 0, OfficialPrice(160_000_000, 2026, "x"), market(200_000_000), residential)
    assert r.target_deposit_won == min(201_600_000, 140_000_000)

def test_unknown_debt_adds_registry_caution():
    assert "CHECK_REGISTRY" in codes(assess_risk(1, None, None, None, residential))

def test_zero_deposit_monthly_rent_has_no_deposit_risk():
    r = assess_risk(0, None, OfficialPrice(1, 2026, "x"), market(1), residential)
    assert not codes(r) & {"GUARANTEE_BLOCKED", "HIGH_RATIO", "CAUTION_RATIO"}

def test_no_risk_headline_never_says_safe():
    r = assess_risk(50_000_000, 0, OfficialPrice(200_000_000, 2026, "x"), market(300_000_000), residential)
    assert r.headline == "확인된 위험 신호가 없어요. 단, 등기부등본은 꼭 직접 확인하세요."
    assert "안전" not in r.headline

def test_not_residential_is_danger(): ...
def test_missing_data_are_info(): ...  # official None → NO_OFFICIAL_PRICE, market None → NO_MARKET_DATA
```
- [ ] **Step 2–4:** FAIL → implement → PASS. Commit `feat: risk assessment rules`.

### Task 6: 지원 정책 매칭 + 정책표 조사

**Files:**
- Create: `housing/policies.py`, `data/policies.json`, `tests/test_policies.py`, `tests/fixtures/policies_sample.json`

**Interfaces:**
- Produces:
```python
@dataclass(frozen=True)
class Profile:
    age: int; income_won: int; married: bool; homeless: bool
    deposit_won: int; monthly_rent_won: int
    @property
    def contract_type(self) -> str: ...  # "monthly" if monthly_rent_won > 0 else "jeonse"
@dataclass(frozen=True)
class PolicyMatch:
    policy: dict; status: str   # "eligible" | "check_needed"
    reasons: list[str]          # 충족/미확인 조건 설명
def load_policies(path: str = "data/policies.json") -> list[dict]
def match_policies(profile: Profile, policies: list[dict]) -> list[PolicyMatch]  # 불충족 정책은 제외
```
정책 JSON 필드: `id, name, contract_types ("jeonse"|"monthly"|"any" 리스트), min_age, max_age, max_income_won, max_income_won_married, max_deposit_won, max_monthly_rent_won, homeless_required, benefit_max_won, benefit_text, documents, apply_url, source_url, checked_date, unverifiable_conditions`. 값이 `null`인 조건은 검사하지 않는다. `unverifiable_conditions`가 비어 있지 않으면 status는 `check_needed`.

- [ ] **Step 1: Failing tests** against `policies_sample.json` (합성 정책, 실제 데이터와 무관):
```python
def test_guarantee_fee_support_eligible():
    p = Profile(age=26, income_won=32_000_000, married=False, homeless=True, deposit_won=220_000_000, monthly_rent_won=0)
    assert ids(match_policies(p, sample)) >= {"guarantee_fee"}

def test_income_over_limit_excluded():  # 5,100만 → 제외
def test_married_uses_married_limit():
def test_monthly_contract_gets_monthly_policies_only():
def test_unverifiable_condition_marks_check_needed():
def test_null_condition_not_checked():
```
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Research `data/policies.json`** — 5개 이상, 서울 청년 기준, 공식 출처(gov.kr, housing.seoul.go.kr, khug.or.kr, 국토부)만. 최소 포함: 국토부 전세보증금반환보증 보증료 지원(보증금 3억 이하, 청년 연소득 5천만 원 이하, 최대 40만 원, 무주택), 청년 월세 지원 1개 이상. 각 항목에 `source_url`, `checked_date: "2026-11-1x"`. 공식 페이지에서 확인 못 한 조건은 값으로 넣지 말고 `unverifiable_conditions`에 문장으로.
- [ ] **Step 6:** Commit `feat: youth housing policy matching`.

### Task 7: 파이프라인과 Streamlit 화면

**Files:**
- Create: `housing/pipeline.py`, `app.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes: 위 모든 함수.
- Produces (AI 계획이 그대로 재사용):
```python
@dataclass
class Diagnosis:
    address: Address; building: BuildingInfo | None; official: OfficialPrice | None
    market: MarketEstimate | None; risk: RiskReport; policies: list[PolicyMatch]
    errors: dict[str, str]  # 출처별 실패 메시지, 예: {"market": "데이터를 가져오지 못했어요: ..."}
def diagnose(address: Address, dong: str, ho: str, area_m2: float, deposit_won: int,
             monthly_rent_won: int, senior_debt_won: int | None, profile: Profile,
             manual_official_won: int | None = None, today: date | None = None) -> Diagnosis
```
각 출처 호출을 따로 try/except `DataSourceError` → `errors`에 기록하고 `None`으로 계속. `manual_official_won`이 있으면 VWorld 대신 `OfficialPrice(manual, today.year, "사용자 입력")`.

- [ ] **Step 1: Failing tests** (monkeypatch 각 get_* 함수):
```python
def test_market_failure_does_not_block_other_results():
    # get_market_price가 DataSourceError → errors["market"] 존재, risk·policies는 계산됨
def test_manual_official_price_used_when_given():
    assert d.official.source == "사용자 입력"
```
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: `app.py`** — 입력(만원 단위): 주소 검색어 → `search_address` 결과 `selectbox`(0건이면 "주소를 찾을 수 없어요"), 동(선택), 호, 전용면적㎡, 보증금, 월세, 선순위채권(+ "모름" 체크박스 → None), 공시가격 직접 입력(선택), 나이, 연소득, 혼인, 무주택. 출력 순서는 spec 5.3: 헤드라인 → 신호 카드(숫자·출처) → 지원 목록과 예상 금액 합계(`eligible`만 합산) → 체크리스트(협상 목표 보증금과 계산식, "등기부등본에서 근저당 확인", "건축물대장에서 위반건축물 여부 확인" — 표제부 API에 위반 항목이 없어서 안내로 대신함) → 고지문. `errors`의 각 항목은 해당 카드 자리에 경고로 표시. 결과 조회는 `st.cache_data`.
- [ ] **Step 6: Verify** — `.venv/bin/streamlit run app.py`, 브라우저에서 시나리오 3개 확인: (a) 보증금이 한도를 넘는 빌라 전세, (b) 보증금 1,000만 원 월세, (c) 존재하지 않는 주소. 각 화면 캡처. Commit `feat: streamlit form mode`.

### Task 8: 배포

**Files:**
- Create: `README.md` (실행법, 데이터 출처, 고지문)

- [ ] **Step 1:** 사용자가 GitHub 저장소를 만들고 push 승인 → push (`.env` 제외 확인: `git ls-files | grep -c .env` → 0, `.gitignore`만 해당).
- [ ] **Step 2:** Streamlit Community Cloud에서 앱 생성, Secrets에 세 키를 root-level로 등록(환경변수로 노출됨) → 배포 URL에서 시나리오 (a)~(c) 재확인.
- [ ] **Step 3:** Commit `docs: readme`.
