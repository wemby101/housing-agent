"""발표용 예비 응답 저장: 예시 집 3곳을 실제 API로 조회해 data/demo에 남긴다.

실행: DEMO_RECORD=1 PYTHONPATH=. .venv/bin/python scripts/record_demo.py
발표 때 API가 403·503·시간 초과로 실패하면 앱이 이 응답을 대신 쓴다 (같은 검색어·같은 호만).
"""
import os
from datetime import date
from urllib.parse import urlencode

from housing.address import search_address
from housing.official_price import list_units
from housing.pipeline import diagnose
from housing.policies import Profile

SEARCH_LIMIT = 20  # app.py와 같아야 같은 요청으로 저장된다
DEMOS = [  # (검색어, 동, 호, 보증금 만원)
    ("관악구 봉천동 1-70", "", "201", 22000),
    ("인천 연수구 해돋이로 107", "1", "202", 20000),
    ("분당구 내정로 55", "301", "101", 30000),
]

assert os.environ.get("DEMO_RECORD"), "DEMO_RECORD=1을 붙여 실행하세요"
for q, dong, ho, dep in DEMOS:
    addr = search_address(q, limit=SEARCH_LIMIT)[0]
    unit = next(u for u in list_units(addr) if (u.dong, u.ho) == (dong, ho))
    profile = Profile(age=27, income_won=35_000_000, married=False, homeless=True, deposit_won=dep * 10_000,
                      monthly_rent_won=0, area_m2=unit.area_m2)
    d = diagnose(addr, dong, ho, unit.area_m2, dep * 10_000, 0, 0, profile, today=date.today())
    print(addr.road_addr, "| 실패:", list(d.errors) or "없음")
    print("  http://localhost:8501/?" + urlencode(dict(q=q, pnu=addr.pnu, dong=dong, ho=ho, area=unit.area_m2, dep=dep, rent=0, debt=0)))
