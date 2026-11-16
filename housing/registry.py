"""등기부등본(인터넷등기소 PDF)에서 선순위 채권과 위험 기록을 읽는다.

shortcut: 실제 등기부 PDF로 검증하지 못한 형식 가정(순위번호로 시작하는 줄, 【 갑 구 】 표시)에 기댄다.
읽은 내용을 화면에 보여주고 사용자가 고칠 수 있게 해서 보완한다. 실제 PDF를 받으면 fixture로 확인할 것.
"""
import re
from dataclasses import dataclass, field
from io import BytesIO

from housing.errors import DataSourceError

GAPGU = re.compile(r"[【\[]\s*갑\s*구\s*[】\]]")
EULGU = re.compile(r"[【\[]\s*을\s*구\s*[】\]]")
END = re.compile(r"이\s*하\s*여\s*백|주\s*요\s*등\s*기\s*사\s*항\s*요\s*약")
ENTRY = re.compile(r"^\s*(\d+)(?:-\d+)?\s+(?=\S)", re.M)  # 줄 맨 앞 순위번호 (부기등기 2-1 포함)
LIEN_KINDS = {"근저당권설정": "근저당권", "전세권설정": "전세권", "주택임차권": "임차권", "임차권설정": "임차권"}
AMOUNT = re.compile(r"(?:채권최고액|전세금|임차보증금)금([\d,]+)원")
FLAG_WORDS = ("가압류", "압류", "가처분", "경매개시결정")


@dataclass(frozen=True)
class Lien:
    rank: int
    kind: str  # 근저당권 | 전세권 | 임차권
    amount_won: int


@dataclass(frozen=True)
class RegistryReading:
    liens: list[Lien] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)  # 압류·가압류·가처분·경매개시결정·신탁

    @property
    def total_won(self) -> int:
        return sum(l.amount_won for l in self.liens)


def _entries(section: str) -> list[tuple[int, str, str]]:
    """(순위번호, 등기목적, 공백 없앤 본문) 목록."""
    starts = list(ENTRY.finditer(section))
    out = []
    for m, nxt in zip(starts, starts[1:] + [None]):
        body = re.sub(r"\s", "", section[m.end(): nxt.start() if nxt else len(section)])
        purpose = re.split(r"\d{4}년", body, maxsplit=1)[0]
        out.append((int(m.group(1)), purpose, body))
    return out


def _cancelled(entries) -> set[int]:
    return {int(m.group(1)) for _, purpose, _ in entries if (m := re.match(r"(\d+)번.*말소$", purpose))}


def parse_registry_text(text: str) -> RegistryReading:
    gap = GAPGU.search(text)
    eul = EULGU.search(text)
    if not gap and not eul:
        raise DataSourceError("등기부등본 형식이 아니에요. 인터넷등기소에서 받은 등기사항증명서 PDF인지 확인해 주세요.")
    end = END.search(text, (eul or gap).end())
    stop = end.start() if end else len(text)
    gap_text = text[gap.end(): eul.start() if eul else stop] if gap else ""
    eul_text = text[eul.end(): stop] if eul else ""

    gap_entries = _entries(gap_text)
    gone = _cancelled(gap_entries)
    flags = []
    for rank, purpose, body in gap_entries:
        if rank in gone:
            continue
        word = next((w for w in FLAG_WORDS if purpose.startswith(w)), None)
        if word is None and ("신탁원부" in body or "수탁자" in body):
            word = "신탁"
        if word and word not in flags:
            flags.append(word)

    eul_entries = _entries(eul_text)
    gone = _cancelled(eul_entries)
    amounts: dict[int, tuple[str, int]] = {}
    for rank, purpose, body in eul_entries:
        amount = AMOUNT.search(body)
        if purpose in LIEN_KINDS and amount:
            amounts[rank] = (LIEN_KINDS[purpose], int(amount.group(1).replace(",", "")))
        elif (m := re.match(r"(\d+)번.*변경$", purpose)) and amount and int(m.group(1)) in amounts:
            amounts[int(m.group(1))] = (amounts[int(m.group(1))][0], int(amount.group(1).replace(",", "")))
    liens = [Lien(r, kind, won) for r, (kind, won) in sorted(amounts.items()) if r not in gone]
    return RegistryReading(liens, flags)


def read_registry_pdf(data: bytes) -> RegistryReading:
    from pypdf import PdfReader
    try:
        text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(data)).pages)
    except Exception as e:  # 깨지거나 암호가 걸린 PDF
        raise DataSourceError(f"PDF를 열 수 없어요 ({type(e).__name__}). 근저당 금액을 직접 입력해 주세요.") from None
    if not text.strip():
        raise DataSourceError("글자를 읽을 수 없는 PDF예요(사진·스캔본). 근저당 금액을 직접 입력해 주세요.")
    return parse_registry_text(text)
