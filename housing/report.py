"""진단 결과를 한 파일짜리 HTML 보고서로 만든다 (폰·PC에서 열고, 인쇄하면 PDF로 저장)."""
from datetime import date
from html import escape

from housing.pipeline import Diagnosis

LEVEL = {"danger": "위험", "caution": "주의", "info": "참고"}
STYLE = """
body{font-family:'IBM Plex Sans KR','Apple SD Gothic Neo','Malgun Gothic',sans-serif;color:#1F2430;
max-width:720px;margin:2rem auto;padding:0 1rem;line-height:1.6}
h1{font-size:1.4rem;margin-bottom:.2rem}
.plate{display:inline-block;background:#1F4E8C;color:#fff;padding:.4rem .9rem;border-radius:8px;font-weight:700}
.head{padding:.8rem 1rem;border-radius:6px;background:#FBEDEA;font-weight:600}
.card{border:1px solid #DDD5CE;border-radius:6px;padding:.6rem 1rem;margin:.6rem 0}
.danger{border-left:5px solid #B3261E}.caution{border-left:5px solid #C77700}.info{border-left:5px solid #8A8F98}
small{color:#5B6170}ul{padding-left:1.2rem}
@media print{body{margin:0}}
"""


def build_report(d: Diagnosis, checklist: list[str], done: set[int], disclaimer: str, today: date) -> str:
    e = escape
    parts = [
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>집 진단 결과 - {e(d.address.road_addr)}</title><style>{STYLE}</style></head><body>",
        f"<h1>청년 집 구하기 진단 결과</h1><small>진단일 {today.isoformat()}</small>",
        f"<p><span class='plate'>{e(d.address.road_addr)}</span></p>",
        f"<p class='head'>{e(d.risk.headline)}</p>",
        "<h2>위험 신호</h2>",
    ]
    for s in d.risk.signals:
        parts.append(f"<div class='card {s.level}'><b>[{LEVEL[s.level]}] {e(s.title)}</b>"
                     f"<p>{e(s.detail)}</p><small>출처: {e(s.source)}</small></div>")
    for msg in d.errors.values():
        parts.append(f"<p><small>{e(msg)}</small></p>")
    parts.append("<h2>받을 수 있는 지원</h2>")
    for m in d.policies:
        p = m.policy
        badge = "조건 충족" if m.status == "eligible" else "추가 확인 필요"
        reasons = "".join(f"<li>{e(r)}</li>" for r in m.reasons)
        parts.append(f"<div class='card'><b>[{badge}] {e(p['name'])}</b><p>{e(p['benefit_text'])}</p>"
                     f"<ul>{reasons}</ul><a href='{e(p['apply_url'])}'>신청하기</a></div>")
    if not d.policies:
        parts.append("<p>입력한 조건으로 맞는 지원을 찾지 못했어요.</p>")
    if checklist:
        parts.append("<h2>해야 할 일</h2><ul style='list-style:none;padding:0'>")
        parts += [f"<li>{'☑' if i in done else '☐'} {e(it.replace('**', ''))}</li>" for i, it in enumerate(checklist)]
        parts.append("</ul>")
    parts.append(f"<hr><small>{e(disclaimer)}</small></body></html>")
    return "".join(parts)
