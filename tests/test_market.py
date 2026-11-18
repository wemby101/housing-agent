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
        t(19_000, jibun="9-2", area=36.0),     # 포함
        t(20_000, jibun="7", area=50.0),       # 면적 +25% → 제외
        t(20_000, jibun="8", year=1995),       # 건축년도 -8 → 제외
        t(20_000, jibun="3", umd="신림동"),     # 다른 동 → 제외
    ]
    e = estimate_market_price(trades, BONGCHEON, 40.0, 2003)
    assert e.basis == "same_dong" and e.comp_count == 3
    assert sorted(c.jibun for c in e.comps) == ["5-1", "9", "9-2"]  # 화면에 근거로 보여 줄 거래


def test_fewer_than_three_comps_returns_none():
    assert estimate_market_price([t(20_000), t(24_000)], BONGCHEON, 40.0, 2003) is None


def test_two_odd_same_jibun_trades_fall_back_to_same_dong():
    """봉천하이빌 실제 사례: 같은 지번 2건(㎡당 1,400만·1,950만)만으로는 시세가 공시가격의 2.5배로 튀었다."""
    trades = [t(72_800, area=50.71), t(99_000, area=50.71)] + [t(20_000, jibun=j) for j in ("2", "3", "4")]
    e = estimate_market_price(trades, BONGCHEON, 40.0, 2003)
    assert (e.basis, e.comp_count, e.price_won) == ("same_dong", 3, 200_000_000)


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
        return [t(16_000), t(20_000), t(24_000)] if ym == "202611" else []

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


RENT = {"deposit": "21,000", "monthlyRent": 0, "excluUseAr": 40.0, "buildYear": 2003, "umdNm": "봉천동",
        "jibun": "1-70", "contractType": "신규", "dealYear": 2026, "dealMonth": 9}


def test_parse_rent_keeps_only_new_jeonse():
    from housing.market import parse_rent
    assert parse_rent(RENT).price_won == 210_000_000
    assert parse_rent({**RENT, "monthlyRent": 50}) is None       # 월세는 보증금끼리 비교가 안 된다
    assert parse_rent({**RENT, "contractType": "갱신"}) is None  # 갱신은 5% 상한이라 시세보다 낮다
    assert parse_rent({**RENT, "contractType": " "}).price_won == 210_000_000


def test_rent_kind_uses_rent_api_and_parser(monkeypatch, tmp_path):
    monkeypatch.setattr(market, "CACHE_DIR", tmp_path)
    urls = []
    monkeypatch.setattr(market, "call_datagokr", lambda url, params: urls.append(url) or {"totalCount": 2, "items": {"item": [RENT, {**RENT, "monthlyRent": 30}]}})
    assert len(fetch_trades("11620", "202601", "rh_rent")) == 1
    assert "RTMSDataSvcRHRent" in urls[0] and (tmp_path / "rh_rent_11620_202601.json").exists()
    assert "RTMSDataSvcAptRent" in market.TRADE_URLS["apt_rent"]


def test_get_market_price_months_option(monkeypatch):
    asked = []
    monkeypatch.setattr(market, "fetch_trades", lambda lawd, ym, kind: asked.append(ym) or [t(16_000), t(20_000), t(24_000)])
    e = get_market_price(BONGCHEON, 40.0, 2003, date(2026, 11, 16), kind="rh_rent", months=12)
    assert len(asked) == 12 and min(asked) == "202512" and (e.months, e.kind) == (12, "rh_rent")


def test_simultaneous_fetches_of_same_month_do_not_crash(monkeypatch, tmp_path):
    """심사위원 여럿이 같은 데모 링크를 동시에 열면 같은 달을 동시에 받아 캐시에 쓴다."""
    import threading
    monkeypatch.setattr(market, "CACHE_DIR", tmp_path)
    errors = []
    for n in range(30):
        ym = f"2024{n % 12 + 1:02d}" if n < 12 else f"2023{n % 12 + 1:02d}" if n < 24 else f"2022{n % 12 + 1:02d}"
        gate = threading.Barrier(2)

        def fake_call(url, params):
            gate.wait()  # 두 요청이 같은 순간에 캐시를 쓰게 한다
            return {"totalCount": 1, "items": {"item": [ITEM]}}

        monkeypatch.setattr(market, "call_datagokr", fake_call)

        def run():
            try:
                fetch_trades("11620", ym)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
    assert errors == []


def at(price_man, ym):
    return Trade(price_won=price_man * 10_000, area_m2=40.0, build_year=2003, umd_name="봉천동", jibun="1-70", ym=ym)


TODAY = date(2026, 11, 17)  # 최근 12개월 = 2025.12 ~ 2026.11


def test_price_trend_compares_last_year_with_year_before():
    older = [at(20_000, "202501"), at(20_000, "202506"), at(20_000, "202511")]   # ㎡당 500만
    recent = [at(18_000, "202512"), at(18_000, "202605"), at(18_000, "202610")]  # ㎡당 450만
    assert market.price_trend(older + recent, TODAY) == pytest.approx(-0.10)


def test_price_trend_needs_three_trades_on_each_side():
    assert market.price_trend([at(20_000, "202501"), at(20_000, "202506"), at(18_000, "202605"),
                               at(18_000, "202606"), at(18_000, "202607")], TODAY) is None


def test_quarterly_points_for_chart():
    pts = market.quarterly_per_m2([at(20_000, "202511"), at(24_000, "202512"), at(18_000, "202601"), at(16_000, "202603")])
    assert pts == [("25년 4분기", 5_500_000), ("26년 1분기", 4_250_000)]  # 분기별 ㎡당 중간값, 시간 순서


def test_market_price_carries_trend(monkeypatch):
    months = {"202501": [20_000] * 3, "202605": [18_000] * 3}
    monkeypatch.setattr(market, "fetch_trades", lambda lawd, ym, kind="rh": [at(p, ym) for p in months.get(ym, [])])
    e = get_market_price(BONGCHEON, 40.0, 2003, TODAY)
    assert e.trend == pytest.approx(-0.10)


def test_unknown_build_year_taken_from_same_jibun_trades():
    """건축물대장을 못 가져와도 같은 지번 거래에 건축년도가 있으면 그걸로 비슷한 집을 고른다 (새 건물 거래가 섞여 시세가 부풀지 않게)."""
    from housing.market import estimate_market_price
    trades = [t(20000, jibun="1-70"), t(21000, jibun="5"), t(22000, jibun="6"), t(23000, jibun="7"),
              t(60000, year=2022, jibun="8"), t(61000, year=2022, jibun="9"), t(62000, year=2022, jibun="10")]
    e = estimate_market_price(trades, BONGCHEON, 40.0, None)
    assert e.comp_count == 4 and all(c.build_year == 2003 for c in e.comps)
