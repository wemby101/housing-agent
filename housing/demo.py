"""발표용 예비 응답: DEMO_RECORD=1로 실행하면 성공한 공공 API 응답을 data/demo에 저장하고,
나중에 같은 요청이 네트워크·HTTP 오류(403, 503, 시간 초과)로 실패하면 저장한 응답을 대신 쓴다.

shortcut: HTTP 200으로 오는 오류(키 만료 등)는 대신하지 않는다. 필요해지면 각 모듈의 DataSourceError에서 되살린다.
"""
import hashlib
import json
import os
from pathlib import Path

import requests

DEMO_DIR = Path(__file__).resolve().parent.parent / "data" / "demo"
SECRET_PARAMS = {"serviceKey", "confmKey", "key", "domain"}  # 키는 저장하지 않고, 배포 주소가 바뀌어도 같은 요청으로 본다


class _Saved:
    status_code = 200

    def __init__(self, text: str):
        self.text = text

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        pass


def _path(url: str, params: dict) -> Path:
    public = sorted((k, str(v)) for k, v in params.items() if k not in SECRET_PARAMS)
    return DEMO_DIR / (hashlib.sha256(json.dumps([url, public], ensure_ascii=False).encode()).hexdigest()[:20] + ".txt")


def get(url: str, params: dict, timeout: float):
    """requests.get 대신 쓴다. 실패하면 저장한 응답이 있을 때만 그걸 돌려주고, 없으면 원래 오류를 낸다."""
    path = _path(url, params)
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException:
        if path.exists():
            return _Saved(path.read_text(encoding="utf-8"))
        raise
    if os.environ.get("DEMO_RECORD"):
        DEMO_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(resp.text, encoding="utf-8")
    return resp
