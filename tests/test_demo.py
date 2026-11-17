import pytest

from housing import datagokr, demo
from housing.datagokr import call_datagokr
from housing.errors import DataSourceError
from tests.helpers import FakeResponse

OK = '{"response": {"header": {"resultCode": "00"}, "body": {"totalCount": "1", "items": {"item": [{"a": "1"}]}}}}'


def serve(monkeypatch, text, status=200):
    monkeypatch.setattr(datagokr.requests, "get", lambda *a, **k: FakeResponse(text, status))


def test_recorded_response_replays_when_api_fails(monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_KEY", "secret-1")
    monkeypatch.setenv("DEMO_RECORD", "1")
    serve(monkeypatch, OK)
    call_datagokr("https://x/api", {"a": 1})
    monkeypatch.delenv("DEMO_RECORD")
    monkeypatch.setenv("DATA_GO_KR_KEY", "secret-2")  # 키가 달라도 같은 요청이다
    serve(monkeypatch, "", 503)
    assert call_datagokr("https://x/api", {"a": 1})["totalCount"] == "1"
    saved = list(demo.DEMO_DIR.iterdir())
    assert len(saved) == 1 and "secret" not in saved[0].name + saved[0].read_text()


def test_without_recording_failure_is_reported(monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_KEY", "secret-1")
    serve(monkeypatch, OK)
    call_datagokr("https://x/api", {"a": 1})  # DEMO_RECORD 없이 성공해도 저장하지 않는다
    serve(monkeypatch, "", 503)
    with pytest.raises(DataSourceError, match="호출 실패"):
        call_datagokr("https://x/api", {"a": 1})
