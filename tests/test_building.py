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


def test_complex_prefers_residential_main_building(monkeypatch):
    """한 지번에 오피스텔 동과 아파트 동이 같이 있으면 공동주택 동을 본다 (공시가격이 있는 집이라 공동주택)."""
    body = load_fixture("bldg_title_bongcheon_1_70.json")
    apt = body["response"]["body"]["items"]["item"][0]
    office = {**apt, "mainPurpsCdNm": "업무시설", "etcPurps": "오피스텔", "useAprDay": "20090101"}
    apt = {**apt, "mainPurpsCdNm": "공동주택", "etcPurps": "아파트", "useAprDay": "20100301"}
    body["response"]["body"]["items"]["item"] = [office, apt]
    _serve(monkeypatch, json.dumps(body))
    b = get_building(BONGCHEON)
    assert (b.etc_purpose, b.approval_year, b.is_residential) == ("아파트", 2010, True)


def test_building_request_asks_first_page_explicitly(monkeypatch):
    """pageNo 없이 부르면 API가 1개 동만 돌려준다 (송도 28개 동 지번에서 확인)."""
    get = fake_get("bldg_title_bongcheon_1_70.json")
    monkeypatch.setattr(datagokr.requests, "get", get)
    get_building(BONGCHEON)
    assert get.calls[0][1]["pageNo"] == 1 and get.calls[0][1]["numOfRows"] == 100


def test_complex_skips_guardhouse_and_takes_largest_residential(monkeypatch):
    body = load_fixture("bldg_title_bongcheon_1_70.json")
    row = body["response"]["body"]["items"]["item"][0]
    guard = {**row, "mainPurpsCdNm": "공동주택", "etcPurps": "경비실", "hhldCnt": "0"}
    apt = {**row, "mainPurpsCdNm": "공동주택", "etcPurps": "아파트", "hhldCnt": "120"}
    body["response"]["body"]["items"]["item"] = [guard, apt]
    _serve(monkeypatch, json.dumps(body))
    assert get_building(BONGCHEON).is_apartment


def test_garbled_response_is_retried_once(monkeypatch):
    """건축물대장 API는 가끔 HTML 오류 페이지를 준다 — 한 번 더 부르면 대개 정상이다."""
    pages = ["<html><body>503 Service Unavailable</body></html>", RTMS_XML_OK]
    calls = []
    monkeypatch.setattr(datagokr.requests, "get", lambda url, params=None, timeout=None: calls.append(1) or FakeResponse(pages.pop(0)))
    slept = []
    monkeypatch.setattr(datagokr.time, "sleep", slept.append)
    assert int(call_datagokr("https://example", {})["totalCount"]) == 2 and len(calls) == 2
    assert slept and slept[0] >= 1  # 과부하(503)에 바로 다시 부르면 또 실패한다 — 잠깐 쉬고 부른다


def test_key_error_is_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(datagokr.requests, "get", lambda url, params=None, timeout=None: calls.append(1) or FakeResponse(fixture_text("datagokr_key_error.xml")))
    with pytest.raises(DataSourceError):
        call_datagokr("https://example", {})
    assert len(calls) == 1


def test_overload_is_retried_twice_with_longer_waits(monkeypatch):
    """배포 서버(해외)에서 data.go.kr 503이 연달아 와서(2026-11-18 연남동) 건축물대장이 빠지면 시세가 34% 높게 나왔다."""
    pages = ["<html>503</html>", "<html>503</html>", RTMS_XML_OK]
    monkeypatch.setattr(datagokr.requests, "get", lambda url, params=None, timeout=None: FakeResponse(pages.pop(0)))
    slept = []
    monkeypatch.setattr(datagokr.time, "sleep", slept.append)
    assert int(call_datagokr("https://example", {})["totalCount"]) == 2
    assert slept == [1, 2]
