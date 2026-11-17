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
    monkeypatch.setattr(geocode, "_memo", None)
    monkeypatch.setenv("VWORLD_API_KEY", "secret-vworld")
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
