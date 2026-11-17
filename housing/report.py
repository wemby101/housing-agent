"""진단 결과를 한 파일짜리 HTML 보고서로 만든다 (폰·PC에서 열고, 인쇄하면 PDF로 저장)."""
from datetime import date
from html import escape

from housing.pipeline import Diagnosis

LEVEL = {"danger": "위험", "caution": "주의", "info": "참고"}
STYLE = """
body{font-family:Geist,Pretendard,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;color:#171717;background:#fafafa;
max-width:720px;margin:2rem auto;padding:0 1rem;line-height:1.6;word-break:keep-all}
h1{font-size:1.5rem;font-weight:600;letter-spacing:-.04em;margin-bottom:.2rem}
h2{font-size:1.25rem;font-weight:600;letter-spacing:-.02em;margin-top:2rem}
.plate{font-size:1.25rem;font-weight:600;letter-spacing:-.02em}
.head{padding:.8rem 1rem;border-radius:6px;font-weight:500;background:#eaf3ff;color:#0761d1}
.head-danger{background:#fdecec;color:#c50000}.head-caution{background:#ffefcf;color:#ab570a}
.card{background:#fff;border:1px solid #ebebeb;border-radius:12px;padding:.8rem 1rem;margin:.6rem 0}
.danger{border-left:4px solid #ee0000}.caution{border-left:4px solid #f5a623}.info{border-left:4px solid #a1a1a1}
small{color:#8f8f8f}a{color:#0070f3}ul{padding-left:1.2rem}
@media print{body{margin:0;background:#fff}}
"""


def build_report(d: Diagnosis, checklist: list[str], done: set[int], disclaimer: str, today: date) -> str:
    e = escape
    parts = [
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>집 진단 결과 - {e(d.address.road_addr)}</title><style>{STYLE}</style></head><body>",
        f"<h1>청년 집 구하기 진단 결과</h1><small>진단일 {today.isoformat()}</small>",
        f"<p><span class='plate'>{e(d.address.road_addr)}</span></p>",
        f"<p class='head head-{d.risk.signals[0].level if d.risk.signals else 'info'}'>{e(d.risk.headline)}</p>",
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
    if d.youth:
        parts.append("<h2>이 지역의 다른 청년 주거 정책</h2><small>출처: 온통청년. 자격은 자동으로 따지지 않았어요.</small><ul>")
        parts += [f"<li><a href='{e(y.url)}'>{e(y.name)}</a> · {e(y.org)} · 신청 {e(y.period)}</li>" for y in d.youth]
        parts.append("</ul>")
    if checklist:
        parts.append("<h2>해야 할 일</h2><ul style='list-style:none;padding:0'>")
        parts += [f"<li>{'☑' if i in done else '☐'} {e(it.replace('**', ''))}</li>" for i, it in enumerate(checklist)]
        parts.append("</ul>")
    parts.append(f"<hr><small>{e(disclaimer)}</small></body></html>")
    return "".join(parts)
