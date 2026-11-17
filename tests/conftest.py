import pytest

from housing import demo


@pytest.fixture(autouse=True)
def _no_saved_responses(monkeypatch, tmp_path):
    """저장해 둔 데모 응답이 오류 테스트를 가리지 않게 테스트마다 빈 폴더를 쓴다."""
    monkeypatch.setattr(demo, "DEMO_DIR", tmp_path / "demo")
    monkeypatch.delenv("DEMO_RECORD", raising=False)
