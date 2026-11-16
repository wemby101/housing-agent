"""공공데이터포털(data.go.kr) 공통 호출: 키 주입, 오류 판정, items 정규화."""
import re

import requests

from housing.config import get_key
from housing.errors import DataSourceError


def call_datagokr(url: str, params: dict) -> dict:
    """응답의 response.body를 돌려준다. 키 오류·결과코드 오류는 DataSourceError."""
    query = {"serviceKey": get_key("DATA_GO_KR_KEY"), "_type": "json", **params}
    try:
        resp = requests.get(url, params=query, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise DataSourceError(f"공공데이터 호출 실패: {e}") from e
    text = resp.text.strip()
    if text.startswith("<"):  # 게이트웨이 오류는 _type=json이어도 XML로 올 때가 있다
        msg = re.search(r"<errMsg>(.*?)</errMsg>", text) or re.search(r"<resultMsg>(.*?)</resultMsg>", text)
        raise DataSourceError(f"공공데이터 오류: {msg.group(1) if msg else text[:200]}")
    try:
        data = resp.json()
    except ValueError as e:
        raise DataSourceError(f"공공데이터 응답 해석 실패: {text[:200]}") from e
    if "OpenAPI_ServiceResponse" in data:
        header = data["OpenAPI_ServiceResponse"].get("cmmMsgHeader", {})
        raise DataSourceError(f"공공데이터 오류: {header.get('errMsg')} ({header.get('returnAuthMsg')})")
    header = data.get("response", {}).get("header", {})
    if header.get("resultCode") != "00":
        raise DataSourceError(f"공공데이터 오류: {header.get('resultCode')} {header.get('resultMsg')}")
    return data["response"].get("body") or {}


def items_of(body: dict) -> list[dict]:
    items = body.get("items")
    if not items:
        return []
    item = items.get("item", [])
    return item if isinstance(item, list) else [item]
