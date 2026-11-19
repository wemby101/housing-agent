import json

import pytest

from housing import youth
from housing.errors import DataSourceError
from housing.youth import get_youth_policies
from tests.helpers import FakeResponse, fake_get


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("YOUTH_POLICY_API_KEY", "test-key")


def test_only_open_local_policies_that_fit_age(monkeypatch):
    """실제 인천 연수구 응답: 전국(256개 시군구)·다른 지역·마감·나이 밖·이미 보여주는 보증료 정책은 뺀다."""
    get = fake_get("youth_incheon_28185.json")
    monkeypatch.setattr(youth.requests, "get", get)  # demo.get이 쓰는 requests와 같은 모듈
    got = get_youth_policies("28185", age=27)
    assert [p.name for p in got] == ["검단신도시 워라밸빌리지 특화구역 청년주거단지 조성", "인품 자립주택 운영"]
    assert got[0].url.startswith("https://youth.incheon.go.kr/youthpolicy/") and got[0].org.startswith("인천광역시")
    params = get.calls[0][1]
    assert (params["apiKeyNm"], params["zipCd"], params["lclsfNm"], params["rtnType"]) == ("test-key", "28185", "주거", "json")


def test_age_outside_range_excluded(monkeypatch):
    monkeypatch.setattr(youth.requests, "get", fake_get("youth_incheon_28185.json"))
    names = [p.name for p in get_youth_policies("28185", age=36)]  # 인품 자립주택은 19~34세, 천원주택은 34세 이상
    assert names == ["검단신도시 워라밸빌리지 특화구역 청년주거단지 조성", "아이플러스(i+) 집드림_천원주택"]


def test_api_error_raises_without_key(monkeypatch):
    body = json.dumps({"resultCode": 401, "resultMessage": "인증키가 유효하지 않습니다.", "result": None})
    monkeypatch.setattr(youth.requests, "get", lambda *a, **k: FakeResponse(body))
    with pytest.raises(DataSourceError) as e:
        get_youth_policies("28185", age=27)
    assert "test-key" not in str(e.value) and "인증키" in str(e.value)


def test_policies_whose_period_already_ended_excluded(monkeypatch):
    from datetime import date
    row = {"zipCd": "28185", "rgtrInstCdNm": "인천광역시"}
    monkeypatch.setattr(youth, "_fetch", lambda zip_cd: [
        {**row, "plcyNm": "끝난 이사 지원", "aplyYmd": "20250507 ~ 20251231"},
        {**row, "plcyNm": "진행 중 지원", "aplyYmd": "20250409 ~ 20261231"},
        {**row, "plcyNm": "상시 지원", "aplyYmd": ""},
    ])
    names = [p.name for p in get_youth_policies("28185", age=27, today=date(2026, 11, 17))]
    assert names == ["진행 중 지원", "상시 지원"]


def test_period_shown_as_readable_dates(monkeypatch):
    """API 원문 "20260101 ~ 20261231"을 사람이 읽기 쉬운 "2026.01.01 ~ 2026.12.31"로 보여 준다."""
    from datetime import date
    row = {"zipCd": "28185", "rgtrInstCdNm": "인천광역시"}
    monkeypatch.setattr(youth, "_fetch", lambda zip_cd: [
        {**row, "plcyNm": "기간 있는 지원", "aplyYmd": "20260101 ~ 20261231"},
        {**row, "plcyNm": "상시 지원", "aplyYmd": ""},
    ])
    got = get_youth_policies("28185", age=27, today=date(2026, 11, 17))
    assert [p.period for p in got] == ["2026.01.01 ~ 2026.12.31", "상시"]
