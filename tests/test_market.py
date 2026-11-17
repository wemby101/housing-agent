from datetime import date

import pytest

from housing import market
from housing.market import Trade, estimate_market_price, fetch_trades, get_market_price, parse_trade
from tests.test_building import BONGCHEON  # 봉천동 1-70

ITEM = {  # 필드명은 계획서 기준 — 실거래가 키 활성화 후 실제 응답으로 재확인 필요
    "dealAmount": "21,000", "excluUseAr": "40.5", "buildYear": "2003", "umdNm": "봉천동",
    "jibun": "1-70", "cdealType": " ", "dealYear": "2026", "dealMonth": "9",
}


def t(price_man, area=40.0, year=2003, jibun="1-70", umd="봉천동"):
    return Trade(price_won=price_man * 10_000, area_m2=area, build_year=year, umd_name=umd, jibun=jibun, ym="202609")


def test_parse_amount_with_comma():
    tr = parse_trade({**ITEM, "dealAmount": "12,500"})
    assert tr.price_won == 125_000_000
    assert (tr.area_m2, tr.build_year, tr.jibun, tr.ym) == (40.5, 2003, "1-70", "202609")


def test_cancelled_trade_excluded():
    assert parse_trade({**ITEM, "cdealType": "O"}) is None


def test_same_jibun_basis_uses_median_per_m2():
    trades = [t(16_000), t(20_000), t(24_000), t(99_000, jibun="5-1")]  # ㎡당 400·500·600만
    e = estimate_market_price(trades, BONGCHEON, 40.0, 2003)
    assert (e.basis, e.comp_count, e.price_won, e.months) == ("same_jibun", 3, 200_000_000, 24)


def test_falls_back_to_same_dong_with_area_and_year_window():
    trades = [
        t(30_000, area=100.0),                 # 같은 지번 1건 (면적 범위 밖)
        t(20_000, jibun="5-1", area=45.0),     # 포함
        t(18_000, jibun="9", year=2007),       # 포함
        t(20_000, jibun="7", area=50.0),       # 면적 +25% → 제외
        t(20_000, jibun="8", year=1995),       # 건축년도 -8 → 제외
        t(20_000, jibun="3", umd="신림동"),     # 다른 동 → 제외
    ]
    e = estimate_market_price(trades, BONGCHEON, 40.0, 2003)
    assert e.basis == "same_dong" and e.comp_count == 2


def test_fewer_than_two_comps_returns_none():
    assert estimate_market_price([t(20_000)], BONGCHEON, 40.0, 2003) is None


def test_get_market_price_requests_24_months(monkeypatch):
    asked = []
    monkeypatch.setattr(market, "fetch_trades", lambda lawd, ym, kind: asked.append((lawd, ym)) or [t(16_000), t(24_000)])
    e = get_market_price(BONGCHEON, 40.0, 2003, date(2026, 11, 16))
    assert len(asked) == 24 and min(asked) == ("11620", "202412") and max(asked) == ("11620", "202611")
    assert e.price_won == 200_000_000


def test_fetch_trades_pages_and_caches_old_months(monkeypatch, tmp_path):
    monkeypatch.setattr(market, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(market, "date", type("D", (), {"today": staticmethod(lambda: date(2026, 11, 16))}))
    calls = []

    def fake_call(url, params):
        calls.append(params["pageNo"])
        page = [ITEM] * (1000 if params["pageNo"] == 1 else 5)
        return {"totalCount": 1005, "items": {"item": page}}

    monkeypatch.setattr(market, "call_datagokr", fake_call)
    assert len(fetch_trades("11620", "202601")) == 1005 and calls == [1, 2]
    assert len(fetch_trades("11620", "202601")) == 1005 and calls == [1, 2]  # 캐시
    fetch_trades("11620", "202610")  # 지난달은 아직 신고가 들어오는 중 → 캐시 안 함
    fetch_trades("11620", "202610")
    assert calls == [1, 2, 1, 2, 1, 2]


def test_unexpected_item_shape_raises_datasource_error(monkeypatch, tmp_path):
    from housing.errors import DataSourceError
    monkeypatch.setattr(market, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(market, "call_datagokr", lambda url, params: {"totalCount": 1, "items": {"item": [{"거래금액": "1"}]}})
    with pytest.raises(DataSourceError, match="형식"):
        fetch_trades("11620", "202601")


def test_corrupt_cache_is_refetched(monkeypatch, tmp_path):
    monkeypatch.setattr(market, "CACHE_DIR", tmp_path)
    (tmp_path / "rh_11620_202601.json").write_text("[{trunc", encoding="utf-8")
    monkeypatch.setattr(market, "call_datagokr", lambda url, params: {"totalCount": 1, "items": {"item": [ITEM]}})
    assert len(fetch_trades("11620", "202601")) == 1


def test_one_failed_month_does_not_drop_the_rest(monkeypatch):
    from housing.errors import DataSourceError

    def fetch(lawd, ym, kind):
        if ym == "202605":
            raise DataSourceError("일시 오류")
        return [t(16_000), t(24_000)] if ym == "202611" else []

    monkeypatch.setattr(market, "fetch_trades", fetch)
    assert get_market_price(BONGCHEON, 40.0, 2003, date(2026, 11, 16)).price_won == 200_000_000


def test_all_months_failing_raises(monkeypatch):
    from housing.errors import DataSourceError

    def fetch(lawd, ym, kind):
        raise DataSourceError("HTTP 403")

    monkeypatch.setattr(market, "fetch_trades", fetch)
    with pytest.raises(DataSourceError, match="403"):
        get_market_price(BONGCHEON, 40.0, 2003, date(2026, 11, 16))


def test_apartment_trades_use_apt_api_and_own_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(market, "CACHE_DIR", tmp_path)
    urls = []
    monkeypatch.setattr(market, "call_datagokr", lambda url, params: urls.append(url) or {"totalCount": 1, "items": {"item": [ITEM]}})
    assert len(fetch_trades("11620", "202601", "apt")) == 1
    assert "RTMSDataSvcAptTrade" in urls[0] and (tmp_path / "apt_11620_202601.json").exists()


def test_get_market_price_passes_kind(monkeypatch):
    kinds = []
    monkeypatch.setattr(market, "fetch_trades", lambda lawd, ym, kind: kinds.append(kind) or [t(16_000), t(24_000)])
    e = get_market_price(BONGCHEON, 40.0, 2003, date(2026, 11, 16), kind="apt")
    assert set(kinds) == {"apt"} and e.kind == "apt"


def test_villa_trades_use_current_endpoint():
    # 구 주소(RTMSDataSvcRHTrade)는 키가 있어도 403 SERVICE_KEY_IS_NOT_REGISTERED가 난다 (2026-11-17 확인)
    assert market.TRADE_URLS["rh"].endswith("/RTMSDataSvcRHTrade2/getRTMSDataSvcRHTrade2")
