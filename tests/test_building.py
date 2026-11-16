import json

import pytest

from housing import datagokr
from housing.address import Address
from housing.building import get_building
from housing.datagokr import call_datagokr, items_of
from housing.errors import DataSourceError
from tests.helpers import FakeResponse, fake_get, fixture_text, load_fixture

BONGCHEON = Address(
    road_addr="", jibun_addr="서울특별시 관악구 봉천동 1-70", building_name="봉천하이빌",
    sigungu_cd="11620", bjdong_cd="10100", emd_name="봉천동", plat_gb_cd="0",
    bun="0001", ji="0070", pnu="1162010100100010070",
)


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_KEY", "test-key")


def _serve(monkeypatch, text):
    monkeypatch.setattr(datagokr.requests, "get", lambda *a, **k: FakeResponse(text))


def test_building_bongcheon(monkeypatch):
    get = fake_get("bldg_title_bongcheon_1_70.json")
    monkeypatch.setattr(datagokr.requests, "get", get)
    b = get_building(BONGCHEON)
    assert b.main_purpose == "공동주택" and b.etc_purpose == "다세대주택"
    assert b.households == 9 and b.approval_year == 2003 and b.is_residential
    params = get.calls[0][1]
    assert (params["sigunguCd"], params["bjdongCd"], params["platGbCd"], params["bun"], params["ji"]) == (
        "11620", "10100", "0", "0001", "0070")


def test_neighborhood_facility_not_residential(monkeypatch):
    body = load_fixture("bldg_title_bongcheon_1_70.json")
    body["response"]["body"]["items"]["item"][0]["mainPurpsCdNm"] = "제2종근린생활시설"
    _serve(monkeypatch, json.dumps(body))
    assert not get_building(BONGCHEON).is_residential


def test_building_not_found_returns_none(monkeypatch):
    body = load_fixture("bldg_title_bongcheon_1_70.json")
    body["response"]["body"].update(items="", totalCount=0)
    _serve(monkeypatch, json.dumps(body))
    assert get_building(BONGCHEON) is None


@pytest.mark.parametrize("name", ["datagokr_key_error.xml", "datagokr_key_error.json"])
def test_datagokr_key_error_raises(monkeypatch, name):
    _serve(monkeypatch, fixture_text(name))
    with pytest.raises(DataSourceError, match="SERVICE_KEY_IS_NOT_REGISTERED_ERROR"):
        call_datagokr("https://example", {})


def test_datagokr_result_code_error_raises(monkeypatch):
    _serve(monkeypatch, json.dumps({"response": {"header": {"resultCode": "03", "resultMsg": "NO_DATA"}}}))
    with pytest.raises(DataSourceError, match="NO_DATA"):
        call_datagokr("https://example", {})


def test_items_of_shapes():
    assert items_of({"items": ""}) == []
    assert items_of({"items": {"item": {"a": 1}}}) == [{"a": 1}]
    assert items_of({"items": {"item": [{"a": 1}, {"a": 2}]}}) == [{"a": 1}, {"a": 2}]


def test_http_error_message_hides_service_key(monkeypatch):
    def leak(url, params=None, timeout=None):
        raise datagokr.requests.HTTPError(f"403 Client Error: Forbidden for url: {url}?serviceKey={params['serviceKey']}",
                                          response=FakeResponse("", 403))
    monkeypatch.setattr(datagokr.requests, "get", leak)
    with pytest.raises(DataSourceError) as e:
        call_datagokr("https://example", {})
    assert "test-key" not in str(e.value) and "403" in str(e.value)


RTMS_XML_OK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<response><header><resultCode>000</resultCode><resultMsg>OK</resultMsg></header>
<body><items><item><dealAmount>   21,000</dealAmount><umdNm>봉천동</umdNm></item>
<item><dealAmount>18,000</dealAmount><umdNm>신림동</umdNm></item></items>
<numOfRows>1000</numOfRows><pageNo>1</pageNo><totalCount>2</totalCount></body></response>"""  # 합성 — 실제 응답 형식 확인 전


def test_xml_success_with_000_code_returns_items(monkeypatch):
    _serve(monkeypatch, RTMS_XML_OK)
    body = call_datagokr("https://example", {})
    assert int(body["totalCount"]) == 2
    assert [i["umdNm"] for i in items_of(body)] == ["봉천동", "신림동"]
    assert items_of(body)[0]["dealAmount"] == "21,000"


def test_json_success_with_000_code(monkeypatch):
    _serve(monkeypatch, json.dumps({"response": {"header": {"resultCode": "000", "resultMsg": "OK"}, "body": {"items": ""}}}))
    assert items_of(call_datagokr("https://example", {})) == []


def test_xml_result_code_error_raises(monkeypatch):
    _serve(monkeypatch, "<response><header><resultCode>99</resultCode><resultMsg>LIMITED</resultMsg></header></response>")
    with pytest.raises(DataSourceError, match="LIMITED"):
        call_datagokr("https://example", {})
