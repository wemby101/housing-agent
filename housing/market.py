"""연립다세대 매매 실거래가 → 추정 매매시세 (㎡당 가격 중앙값 × 면적)."""
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import median

from housing.address import Address
from housing.datagokr import call_datagokr, items_of

TRADE_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade"
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
MONTHS = 24
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


def _ym_shift(d: date, back: int) -> str:
    n = d.year * 12 + d.month - 1 - back
    return f"{n // 12:04d}{n % 12 + 1:02d}"


def fetch_trades(lawd_cd: str, ym: str) -> list[Trade]:
    cache = CACHE_DIR / f"rh_{lawd_cd}_{ym}.json"
    if cache.exists():
        items = json.loads(cache.read_text(encoding="utf-8"))
    else:
        items, page = [], 1
        while True:
            body = call_datagokr(TRADE_URL, {"LAWD_CD": lawd_cd, "DEAL_YMD": ym, "numOfRows": PAGE_SIZE, "pageNo": page})
            batch = items_of(body)
            items += batch
            if not batch or len(items) >= int(body.get("totalCount") or 0):
                break
            page += 1
        if ym < _ym_shift(date.today(), 1):  # 지난달·이번 달은 신고가 계속 들어오므로 캐시하지 않음
            CACHE_DIR.mkdir(exist_ok=True)
            cache.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return [tr for tr in map(parse_trade, items) if tr]


def _jibun_of(addr: Address) -> str:
    ji = int(addr.ji)
    return str(int(addr.bun)) + (f"-{ji}" if ji else "")


def estimate_market_price(trades: list[Trade], addr: Address, area_m2: float, build_year: int | None) -> MarketEstimate | None:
    def build(comps: list[Trade], basis: str) -> MarketEstimate:
        per_m2 = median(tr.price_won / tr.area_m2 for tr in comps)
        return MarketEstimate(price_won=round(per_m2 * area_m2), comp_count=len(comps), basis=basis, months=MONTHS)

    same_dong = [tr for tr in trades if tr.umd_name == addr.emd_name and tr.area_m2 > 0]
    same_jibun = [tr for tr in same_dong if tr.jibun == _jibun_of(addr)]
    if len(same_jibun) >= 2:
        return build(same_jibun, "same_jibun")
    similar = [
        tr for tr in same_dong
        if abs(tr.area_m2 - area_m2) <= area_m2 * 0.2
        and (build_year is None or (tr.build_year is not None and abs(tr.build_year - build_year) <= 5))
    ]
    return build(similar, "same_dong") if len(similar) >= 2 else None


def get_market_price(addr: Address, area_m2: float, build_year: int | None, today: date) -> MarketEstimate | None:
    trades = [tr for back in range(MONTHS) for tr in fetch_trades(addr.sigungu_cd, _ym_shift(today, back))]
    return estimate_market_price(trades, addr, area_m2, build_year)
