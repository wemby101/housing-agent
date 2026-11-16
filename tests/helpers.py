import json
import requests
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def load_fixture(name: str):
    return json.loads(fixture_text(name))


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def fake_get(*fixture_names: str):
    """requests.get 대체: 호출 순서대로 fixture를 돌려주고, 받은 URL·params를 기록한다."""
    calls = []
    queue = list(fixture_names)

    def _get(url, params=None, timeout=None):
        calls.append((url, params))
        name = queue.pop(0) if len(queue) > 1 else queue[0]
        return FakeResponse(fixture_text(name))

    _get.calls = calls
    return _get
