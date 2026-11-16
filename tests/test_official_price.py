import pytest

from housing import official_price
from housing.errors import DataSourceError
from housing.official_price import get_official_price
from tests.helpers import fake_get
from tests.test_building import BONGCHEON


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("VWORLD_API_KEY", "test-key")


def test_latest_year_price_for_unit(monkeypatch):
    get = fake_get("vworld_price_bongcheon_201.json")  # 봉천동 1-70 201호, 2006~2026년
    monkeypatch.setattr(official_price.requests, "get", get)
    p = get_official_price(BONGCHEON, "", "201호")
    assert (p.price_won, p.year, p.source) == (281_000_000, 2026, "브이월드 공동주택가격")
    params = get.calls[0][1]
    assert params["pnu"] == "1162010100100010070" and params["hoNm"] == "201" and "dongNm" not in params
    assert params["domain"] == "localhost:8501"  # 브이월드에 등록한 서비스URL과 같아야 INCORRECT_KEY가 안 난다


def test_domain_follows_env_for_deployment(monkeypatch):
    monkeypatch.setenv("VWORLD_DOMAIN", "housing.streamlit.app")
    get = fake_get("vworld_price_bongcheon_201.json")
    monkeypatch.setattr(official_price.requests, "get", get)
    get_official_price(BONGCHEON, "", "201")
    assert get.calls[0][1]["domain"] == "housing.streamlit.app"


def test_dong_is_sent_when_given(monkeypatch):
    get = fake_get("vworld_price_bongcheon_201.json")
    monkeypatch.setattr(official_price.requests, "get", get)
    get_official_price(BONGCHEON, "A동", "201")
    assert get.calls[0][1]["dongNm"] == "A동"


def test_no_rows_returns_none(monkeypatch):
    monkeypatch.setattr(official_price.requests, "get", fake_get("vworld_no_rows.json"))
    assert get_official_price(BONGCHEON, "", "9999") is None


def test_bad_key_raises_without_key(monkeypatch):
    monkeypatch.setattr(official_price.requests, "get", fake_get("vworld_bad_key.json"))
    with pytest.raises(DataSourceError, match="등록되지 않은 인증키") as e:
        get_official_price(BONGCHEON, "", "201")
    assert "test-key" not in str(e.value)


def test_missing_ho_returns_none_without_call(monkeypatch):
    monkeypatch.setattr(official_price.requests, "get", lambda *a, **k: pytest.fail("호 없이 호출함"))
    assert get_official_price(BONGCHEON, "", " ") is None


def test_missing_vworld_key_raises_user_message(monkeypatch):
    monkeypatch.delenv("VWORLD_API_KEY")
    with pytest.raises(DataSourceError, match="직접 입력") as e:
        get_official_price(BONGCHEON, "", "201")
    assert "VWORLD_API_KEY" not in str(e.value) and ".env" not in str(e.value)


def test_list_units_latest_year_unique_sorted(monkeypatch):
    from housing.official_price import Unit, list_units
    get = fake_get("vworld_units_bongcheon.json")  # 봉천동 1-70 전체 호, 2026년 9개 호(행은 중복)
    monkeypatch.setattr(official_price.requests, "get", get)
    units = list_units(BONGCHEON)
    assert [u.ho for u in units] == ["101", "102", "103", "201", "202", "203", "301", "302", "401"]
    assert Unit("", "201", 41.01) in units
    assert "hoNm" not in get.calls[0][1]


def test_list_units_empty_when_no_rows(monkeypatch):
    from housing.official_price import list_units
    monkeypatch.setattr(official_price.requests, "get", fake_get("vworld_no_rows.json"))
    assert list_units(BONGCHEON) == []


def _vworld(pages_by_year):
    """stdrYear·pageNo별 행을 돌려주는 가짜 브이월드. pages_by_year = {"2026": [[행...], [행...]]}."""
    import json
    from tests.helpers import FakeResponse
    calls = []

    def get(url, params, timeout):
        calls.append(dict(params))
        pages = pages_by_year.get(params.get("stdrYear"), [])
        total = sum(len(p) for p in pages)
        field = pages[params["pageNo"] - 1] if params["pageNo"] <= len(pages) else []
        body = {"apartHousingPrices": {"totalCount": str(total), "resultCode": "", "field": field}} if total else {"response": {"totalCount": "0"}}
        return FakeResponse(json.dumps(body))
    get.calls = calls
    return get


def _row(year, dong, ho):
    return {"stdrYear": year, "dongNm": dong, "hoNm": ho, "prvuseAr": "84.9", "pblntfPc": "500000000"}


def test_list_units_reads_every_page_of_this_year(monkeypatch):
    from housing.official_price import list_units
    get = _vworld({"2026": [[_row("2026", "1", str(100 + i)) for i in range(1000)], [_row("2026", "2", "101")]]})
    monkeypatch.setattr(official_price.requests, "get", get)
    units = list_units(BONGCHEON, year=2026)
    assert len(units) == 1001 and units[-1].dong == "2"
    assert [c["pageNo"] for c in get.calls] == [1, 2] and get.calls[0]["stdrYear"] == "2026"


def test_list_units_falls_back_to_last_year_before_announcement(monkeypatch):
    from housing.official_price import list_units
    get = _vworld({"2025": [[_row("2025", "1", "101")]]})  # 올해 공시가격 발표(4월 말) 전
    monkeypatch.setattr(official_price.requests, "get", get)
    assert [u.ho for u in list_units(BONGCHEON, year=2026)] == ["101"]
    assert [c["stdrYear"] for c in get.calls] == ["2026", "2025"]
