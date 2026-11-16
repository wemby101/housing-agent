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
