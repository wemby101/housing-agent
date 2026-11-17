import pytest

from housing import address
from housing.address import parse_juso_row, search_address
from housing.errors import DataSourceError
from tests.helpers import fake_get, load_fixture

CITYHALL_ROW = load_fixture("juso_seoul_cityhall.json")["results"]["juso"][0]


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("JUSO_API_KEY", "test-key")


def test_parse_cityhall_row():
    a = parse_juso_row(CITYHALL_ROW)  # 서울시청 서소문별관
    assert (a.sigungu_cd, a.bjdong_cd, a.bun, a.ji, a.plat_gb_cd) == ("11140", "16600", "0037", "0000", "0")
    assert a.pnu == "1114016600100370000"
    assert CITYHALL_ROW["bdMgtSn"].startswith(a.pnu)
    assert a.emd_name == "서소문동"
    assert a.building_name == "서울시청 서소문별관"


def test_parse_san_lot():
    a = parse_juso_row({**CITYHALL_ROW, "mtYn": "1", "lnbrMnnm": "12", "lnbrSlno": "3"})
    assert a.plat_gb_cd == "1"
    assert a.pnu[10] == "2"
    assert (a.bun, a.ji) == ("0012", "0003")
    assert len(a.pnu) == 19


def test_search_returns_addresses(monkeypatch):
    monkeypatch.setattr(address.requests, "get", fake_get("juso_seoul_cityhall.json"))
    results = search_address("서울시청")
    assert len(results) == 3
    assert results[0].road_addr == "서울특별시 중구 덕수궁길 15 (서소문동)"


def test_search_error_code_raises(monkeypatch):
    monkeypatch.setattr(address.requests, "get", fake_get("juso_bad_key.json"))
    with pytest.raises(DataSourceError, match="승인되지 않은 KEY"):
        search_address("서울시청")


def test_search_zero_results_returns_empty(monkeypatch):
    monkeypatch.setattr(address.requests, "get", fake_get("juso_empty.json"))
    assert search_address("없는주소") == []


def test_network_error_message_hides_key(monkeypatch):
    def leak(url, params=None, timeout=None):
        raise address.requests.ConnectionError(f"failed: {url}?confmKey={params['confmKey']}")
    monkeypatch.setattr(address.requests, "get", leak)
    with pytest.raises(DataSourceError) as e:
        search_address("서울시청")
    assert "test-key" not in str(e.value)


def test_eup_myeon_name_includes_ri():  # 실거래가 umdNm은 "봉담읍 동화리"처럼 리까지 온다
    a = parse_juso_row({**CITYHALL_ROW, "emdNm": "봉담읍", "liNm": "동화리"})
    assert a.emd_name == "봉담읍 동화리"
