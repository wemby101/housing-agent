"""실거래가(연립다세대·아파트 매매, 전세) → 추정 시세 (㎡당 가격 중앙값 × 면적)."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from statistics import median

from housing.address import Address
from housing.datagokr import call_datagokr, items_of
from housing.errors import DataSourceError

TRADE_URLS = {  # 매매 두 API, 전월세 두 API는 각각 응답 필드가 같다. 활용신청은 API마다 따로 해야 한다
    "rh": "https://apis.data.go.kr/1613000/RTMSDataSvcRHTrade2/getRTMSDataSvcRHTrade2",
    "apt": "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade",
    "rh_rent": "https://apis.data.go.kr/1613000/RTMSDataSvcRHRent/getRTMSDataSvcRHRent",
    "apt_rent": "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent",
}
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
MONTHS = 24
MIN_COMPS = 3  # 2건이면 특이 거래 하나가 시세를 크게 흔든다 (봉천하이빌: 공시가격의 2.5배)
PAGE_SIZE = 1000


@dataclass(frozen=True)
class Trade:
    price_won: int
    area_m2: float
    build_year: int | None
    umd_name: str
    jibun: str
    ym: str


@dataclass(frozen=True)
class MarketEstimate:
    price_won: int
    comp_count: int
    basis: str  # "same_jibun" | "same_dong"
    months: int
    kind: str = "rh"  # "rh" 연립다세대 | "apt" 아파트, 전세는 "rh_rent" | "apt_rent"
    comps: tuple[Trade, ...] = ()  # 시세 계산에 쓴 거래 (화면에 근거로 보여 준다)
    trend: float | None = None  # 최근 12개월 ㎡당 중간값 ÷ 그 전 12개월 − 1 (-0.1 = 10% 하락)
    year_known: bool = True  # 같은 동 거래를 건축년도 ±5년으로 걸렀는지 (모르면 새 건물이 섞여 부풀 수 있다)


def parse_trade(item: dict) -> Trade | None:
    if str(item.get("cdealType") or "").strip() == "O":  # 해제된 거래
        return None
    year = str(item.get("buildYear") or "").strip()
    return Trade(
        price_won=int(str(item["dealAmount"]).replace(",", "").strip()) * 10_000,
        area_m2=float(item["excluUseAr"]),
        build_year=int(year) if year.isdigit() else None,
        umd_name=str(item.get("umdNm") or "").strip(),
        jibun=str(item.get("jibun") or "").strip(),
        ym=f"{int(item['dealYear']):04d}{int(item['dealMonth']):02d}",
    )


def parse_rent(item: dict) -> Trade | None:
    """신규 전세 계약만 (월세는 보증금끼리 비교가 안 되고, 갱신은 5% 상한이라 시세보다 낮다)."""
    if str(item.get("monthlyRent") or "0").replace(",", "").strip() not in ("0", "") \
            or str(item.get("contractType") or "").strip() == "갱신":
        return None
    return parse_trade({**item, "dealAmount": item["deposit"], "cdealType": ""})


def _ym_shift(d: date, back: int) -> str:
    n = d.year * 12 + d.month - 1 - back
    return f"{n // 12:04d}{n % 12 + 1:02d}"


def fetch_trades(lawd_cd: str, ym: str, kind: str = "rh") -> list[Trade]:
    cache = CACHE_DIR / f"{kind}_{lawd_cd}_{ym}.json"
    try:
        items = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # 없거나 깨진 캐시는 다시 받는다
        items = None
    if items is None:
        items, page = [], 1
        while True:
            body = call_datagokr(TRADE_URLS[kind], {"LAWD_CD": lawd_cd, "DEAL_YMD": ym, "numOfRows": PAGE_SIZE, "pageNo": page})
            batch = items_of(body)
            items += batch
            if not batch or len(items) >= int(body.get("totalCount") or 0):
                break
            page += 1
        if ym < _ym_shift(date.today(), 1):  # 지난달·이번 달은 신고가 계속 들어오므로 캐시하지 않음
            CACHE_DIR.mkdir(exist_ok=True)
            tmp = cache.with_suffix(f".{threading.get_ident()}.tmp")  # 여러 사람이 같은 달을 동시에 받아도 서로 덮어쓰지 않게
            tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
            tmp.replace(cache)
    try:
        return [tr for tr in map(parse_rent if kind.endswith("_rent") else parse_trade, items) if tr]
    except (KeyError, ValueError, TypeError) as e:
        raise DataSourceError(f"실거래가 응답 형식이 예상과 달라요 ({type(e).__name__}: {e})") from None


def _jibun_of(addr: Address) -> str:
    ji = int(addr.ji)
    return str(int(addr.bun)) + (f"-{ji}" if ji else "")


def estimate_market_price(trades: list[Trade], addr: Address, area_m2: float, build_year: int | None) -> MarketEstimate | None:
    def build(comps: list[Trade], basis: str) -> MarketEstimate:
        per_m2 = _per_m2(comps)
        return MarketEstimate(price_won=round(per_m2 * area_m2), comp_count=len(comps), basis=basis, months=MONTHS,
                              comps=tuple(comps), year_known=build_year is not None)

    same_dong = [tr for tr in trades if tr.umd_name == addr.emd_name and tr.area_m2 > 0]
    same_jibun = [tr for tr in same_dong if tr.jibun == _jibun_of(addr)]
    if len(same_jibun) >= MIN_COMPS:
        return build(same_jibun, "same_jibun")
    if build_year is None:  # 건축물대장이 없거나 실패 — 건축년도 조건이 빠지면 새 건물 거래가 섞여 시세가 부푼다
        build_year = next((tr.build_year for tr in same_jibun if tr.build_year), None)
    similar = [
        tr for tr in same_dong
        if abs(tr.area_m2 - area_m2) <= area_m2 * 0.2
        and (build_year is None or (tr.build_year is not None and abs(tr.build_year - build_year) <= 5))
    ]
    return build(similar, "same_dong") if len(similar) >= MIN_COMPS else None


def _per_m2(trades: list[Trade]) -> float:
    return median(tr.price_won / tr.area_m2 for tr in trades)


def price_trend(comps, today: date) -> float | None:
    """최근 12개월과 그 전 12개월의 ㎡당 중간값 변화율. 양쪽에 거래가 3건 이상일 때만 (적으면 한 건에 크게 흔들린다)."""
    cut = _ym_shift(today, 11)
    recent, older = [t for t in comps if t.ym >= cut], [t for t in comps if t.ym < cut]
    if len(recent) < MIN_COMPS or len(older) < MIN_COMPS:
        return None
    return _per_m2(recent) / _per_m2(older) - 1


def quarterly_per_m2(comps) -> list[tuple[str, int]]:
    """그래프용: 분기별 ㎡당 가격 중간값 (원), 시간 순서."""
    by: dict[str, list[Trade]] = {}
    for t in comps:
        by.setdefault(f"{t.ym[2:4]}년 {(int(t.ym[4:]) - 1) // 3 + 1}분기", []).append(t)
    return [(q, round(_per_m2(ts))) for q, ts in sorted(by.items())]


def get_market_price(addr: Address, area_m2: float, build_year: int | None, today: date,
                     kind: str = "rh", months: int = MONTHS) -> MarketEstimate | None:
    def one(ym: str) -> list[Trade] | DataSourceError:
        try:
            return fetch_trades(addr.sigungu_cd, ym, kind)
        except DataSourceError as e:
            return e

    with ThreadPoolExecutor(max_workers=6) as pool:  # 24개월을 순서대로 부르면 데모에서 너무 느리다
        results = list(pool.map(one, [_ym_shift(today, back) for back in range(months)]))
    ok = [r for r in results if not isinstance(r, DataSourceError)]
    if not ok:  # 전부 실패(키 미활성 등)면 오류로 알린다; 일부 달만 실패하면 나머지로 추정
        raise next(r for r in results if isinstance(r, DataSourceError))
    e = estimate_market_price([tr for m in ok for tr in m], addr, area_m2, build_year)
    return replace(e, kind=kind, months=months, trend=price_trend(e.comps, today)) if e else None
