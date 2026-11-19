import json

import pytest

from housing import geocode
from housing.errors import DataSourceError
from housing.market import Trade
from tests.helpers import FakeResponse
from tests.test_building import BONGCHEON


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setattr(geocode, "CACHE", tmp_path / "geocode.json")
    monkeypatch.setattr(geocode, "SEED", tmp_path / "no-seed.json")
    monkeypatch.setattr(geocode, "_memo", None)
    monkeypatch.setenv("VWORLD_API_KEY", "secret-vworld")
    monkeypatch.delenv("JUSO_COORD_API_KEY", raising=False)  # .env에 있어도 이 테스트들은 브이월드 경로
    seen = []

    def get(url, params, timeout):
        seen.append(params["address"])
        if params["address"].endswith("999"):
            return FakeResponse(json.dumps({"response": {"status": "NOT_FOUND"}}))
        n = len(seen)
        return FakeResponse(json.dumps({"response": {"status": "OK", "result": {"point": {"x": f"126.9{n}", "y": f"37.4{n}"}}}}))

    monkeypatch.setattr(geocode.demo, "get", get)
    return seen


def test_geocode_returns_lat_lon_and_caches(api):
    assert geocode.geocode("서울특별시 관악구 봉천동 1-70") == (37.41, 126.91)
    assert geocode.geocode("서울특별시 관악구 봉천동 1-70") == (37.41, 126.91) and len(api) == 1
    assert geocode.geocode("서울특별시 관악구 봉천동 999") is None


def test_geocode_error_hides_key(api, monkeypatch):
    def boom(url, params, timeout):
        raise geocode.requests.ConnectionError(f"failed {params['key']}")
    monkeypatch.setattr(geocode.demo, "get", boom)
    with pytest.raises(DataSourceError) as e:
        geocode.geocode("서울특별시 관악구 봉천동 2")
    assert "secret-vworld" not in str(e.value)


def tr(price_man, jibun):
    return Trade(price_man * 10_000, 40.0, 2003, "봉천동", jibun, "202609")


def test_comp_points_group_trades_by_lot(api):
    pts = geocode.comp_points(BONGCHEON, (tr(20_000, "5-1"), tr(24_000, "5-1"), tr(16_000, "9"), tr(10_000, "999")))
    home = next(p for p in pts if p["home"])
    others = sorted((p for p in pts if not p["home"]), key=lambda p: p["label"])
    assert "봉천동 1-70" in home["label"]
    assert [p["label"] for p in others] == ["5-1 · 거래 2건 · ㎡당 550만 원", "9 · 거래 1건 · ㎡당 400만 원"]  # 999는 좌표가 없어 빠진다
    assert set(api) >= {"서울특별시 관악구 봉천동 5-1", "서울특별시 관악구 봉천동 9"}


def test_one_failed_lot_does_not_hide_the_map(api, monkeypatch):
    ok = geocode.demo.get

    def flaky(url, params, timeout):
        if params["address"].endswith(" 9"):
            raise geocode.requests.Timeout("slow")
        return ok(url, params, timeout)
    monkeypatch.setattr(geocode.demo, "get", flaky)
    pts = geocode.comp_points(BONGCHEON, (tr(20_000, "5-1"), tr(16_000, "9")))
    assert [p["home"] for p in pts].count(True) == 1 and len(pts) == 2  # 9번지만 빠진다


def test_utmk_to_wgs84_matches_vworld_point():
    """도로명주소 좌표(UTM-K) → 경위도. 봉천하이빌 건물 좌표가 브이월드 지번 좌표(37.49017, 126.95808)와 몇 m 안."""
    lat, lon = geocode.utmk_to_wgs84(952091.2380637666, 1943572.3516789335)
    assert abs(lat - 37.49017) < 0.0003 and abs(lon - 126.95808) < 0.0003


@pytest.fixture
def juso(monkeypatch, tmp_path):
    """좌표제공 키가 있으면 도로명주소 API(검색 → 좌표)로 찾는다 — 배포 서버(해외)에서 브이월드가 막혀서."""
    monkeypatch.setattr(geocode, "CACHE", tmp_path / "geocode.json")
    monkeypatch.setattr(geocode, "SEED", tmp_path / "no-seed.json")
    monkeypatch.setattr(geocode, "_memo", None)
    monkeypatch.setenv("JUSO_API_KEY", "secret-search")
    monkeypatch.setenv("JUSO_COORD_API_KEY", "secret-coord")
    seen = []
    ok = {"common": {"errorCode": "0", "errorMessage": "정상"}}

    def get(url, params, timeout):
        seen.append(url)
        if url.endswith(geocode.JUSO_SEARCH_PATH):
            rows = [] if params["keyword"].endswith("999") else [
                {"admCd": "1162010100", "rnMgtSn": "116204160733", "udrtYn": "0", "buldMnnm": "12", "buldSlno": "0"}]
            return FakeResponse(json.dumps({"results": {**ok, "juso": rows}}))
        assert params["confmKey"] == "secret-coord" and params["rnMgtSn"] == "116204160733"
        return FakeResponse(json.dumps({"results": {**ok, "juso": [{"entX": "952091.2380637666", "entY": "1943572.3516789335"}]}}))

    monkeypatch.setattr(geocode.demo, "get", get)
    return seen


def test_geocode_uses_juso_coordinates_when_key_set(juso):
    lat, lon = geocode.geocode("서울특별시 관악구 봉천동 1-70")
    assert abs(lat - 37.49017) < 0.0003 and abs(lon - 126.95808) < 0.0003
    assert geocode.geocode("서울특별시 관악구 봉천동 999") is None  # 도로명주소가 없는 지번(빈 땅)


def test_juso_error_hides_keys(juso, monkeypatch):
    def denied(url, params, timeout):
        return FakeResponse(json.dumps({"results": {"common": {"errorCode": "E0001", "errorMessage": "승인되지 않은 KEY 입니다."}, "juso": None}}))
    monkeypatch.setattr(geocode.demo, "get", denied)
    with pytest.raises(DataSourceError) as e:
        geocode.geocode("서울특별시 관악구 봉천동 2")
    assert "secret" not in str(e.value) and "E0001" in str(e.value)


def test_juso_rate_limit_is_retried(juso, monkeypatch):
    """E0007(짧은 시간 다량 요청)은 지도 점을 여러 개 동시에 찾을 때 난다 — 잠깐 쉬고 다시 부른다."""
    ok = geocode.demo.get
    busy = [True]

    def rate_limited(url, params, timeout):
        if busy and url.endswith(geocode.JUSO_COORD_PATH):
            busy.pop()
            return FakeResponse(json.dumps({"results": {"common": {"errorCode": "E0007", "errorMessage": "다량 요청"}, "juso": None}}))
        return ok(url, params, timeout)
    monkeypatch.setattr(geocode.demo, "get", rate_limited)
    monkeypatch.setattr(geocode.time, "sleep", lambda s: None)
    assert geocode.geocode("서울특별시 관악구 봉천동 1-70") is not None


def test_seed_coordinates_skip_api(api, monkeypatch, tmp_path):
    """데모 집의 거래 지번 좌표는 data/에 같이 배포해 첫 지도도 바로 뜬다 (배포 서버는 재시작하면 .cache가 빈다)."""
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps({"서울특별시 관악구 봉천동 5-1": [37.5, 126.9]}), encoding="utf-8")
    monkeypatch.setattr(geocode, "SEED", seed)
    assert geocode.geocode("서울특별시 관악구 봉천동 5-1") == (37.5, 126.9) and api == []
