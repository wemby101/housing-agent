"""실제 API 응답을 테스트 fixture로 저장한다.

사용법: .venv/bin/python scripts/record_fixture.py <파일명> '<URL, 키 자리는 {JUSO_API_KEY} 처럼>'
응답 본문에 키 값이 들어 있으면 저장하지 않는다.
"""
import re
import sys
from pathlib import Path

import requests

from housing.config import get_key


def main(name: str, url_template: str) -> None:
    names = re.findall(r"\{([A-Z_]+)\}", url_template)
    keys = {n: get_key(n) for n in names}
    try:
        body = requests.get(url_template.format(**keys), timeout=30).text
    except requests.RequestException as e:  # 예외 메시지·traceback에 키가 든 URL이 찍히지 않게
        sys.exit(f"요청 실패 ({type(e).__name__})")
    if any(k in body for k in keys.values()):
        sys.exit("응답에 키가 들어 있어서 저장하지 않았어요")
    out = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / name
    out.write_text(body, encoding="utf-8")
    print(f"saved {out} ({len(body)} chars)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
