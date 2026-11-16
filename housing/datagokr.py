"""공공데이터포털(data.go.kr) 공통 호출: 키 주입, 오류 판정, items 정규화."""
import re
import xml.etree.ElementTree as ET

import requests

from housing.config import get_key
from housing.errors import DataSourceError

OK_CODES = {"00", "000"}  # 구 API "00", 2024년 이후 실거래가 API "000"


def _xml_body(text: str) -> dict:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        root = None
    if root is None or root.tag != "response":
        msg = re.search(r"<errMsg>(.*?)</errMsg>", text) or re.search(r"<resultMsg>(.*?)</resultMsg>", text)
        raise DataSourceError(f"공공데이터 오류: {msg.group(1) if msg else '응답 해석 실패'}")
    if root.findtext("header/resultCode", "").strip() not in OK_CODES:
        raise DataSourceError(f"공공데이터 오류: {root.findtext('header/resultCode')} {root.findtext('header/resultMsg')}")
    items = [{c.tag: (c.text or "").strip() for c in item} for item in root.iterfind("body/items/item")]
    return {"totalCount": root.findtext("body/totalCount", "0"), "items": {"item": items}}


def call_datagokr(url: str, params: dict) -> dict:
    """응답의 response.body를 돌려준다. 키 오류·결과코드 오류는 DataSourceError."""
    query = {"serviceKey": get_key("DATA_GO_KR_KEY"), "_type": "json", **params}
    try:
        resp = requests.get(url, params=query, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:  # 메시지에 serviceKey가 든 URL이 있으므로 그대로 쓰지 않는다
        status = getattr(e.response, "status_code", None)
        raise DataSourceError(f"공공데이터 호출 실패 ({f'HTTP {status}' if status else type(e).__name__})") from None
    text = resp.text.strip()
    if text.startswith("<"):  # 실거래가 API는 _type=json을 무시하고 XML로 줄 수 있다
        return _xml_body(text)
    try:
        data = resp.json()
    except ValueError as e:
        raise DataSourceError(f"공공데이터 응답 해석 실패: {text[:200]}") from e
    if "OpenAPI_ServiceResponse" in data:
        header = data["OpenAPI_ServiceResponse"].get("cmmMsgHeader", {})
        raise DataSourceError(f"공공데이터 오류: {header.get('errMsg')} ({header.get('returnAuthMsg')})")
    header = data.get("response", {}).get("header", {})
    if header.get("resultCode") not in OK_CODES:
        raise DataSourceError(f"공공데이터 오류: {header.get('resultCode')} {header.get('resultMsg')}")
    return data["response"].get("body") or {}


def items_of(body: dict) -> list[dict]:
    items = body.get("items")
    if not items:
        return []
    item = items.get("item", [])
    return item if isinstance(item, list) else [item]
