from pathlib import Path

import pytest

from housing.errors import DataSourceError
from housing.registry import Lien, parse_registry_text, read_registry_pdf

SAMPLE = (Path(__file__).parent / "fixtures" / "registry_sample.txt").read_text(encoding="utf-8")


def test_active_liens_after_cancel_and_change():
    r = parse_registry_text(SAMPLE)
    assert r.liens == [Lien(2, "근저당권", 84_000_000), Lien(4, "전세권", 50_000_000)]
    assert r.total_won == 134_000_000  # 요약 페이지의 근저당은 두 번 세지 않는다


def test_active_seizures_are_flagged_cancelled_ones_are_not():
    assert parse_registry_text(SAMPLE).flags == ["압류"]


def test_trust_is_flagged():
    text = SAMPLE.replace("1 소유권보존", "1 소유권이전 2021년1월1일 신탁 수탁자 주식회사 코리아신탁\n  신탁원부 제2021-12호\n9 소유권보존")
    assert "신탁" in parse_registry_text(text).flags


def test_no_eulgu_means_no_liens():
    text = SAMPLE.split("【 을 구 】")[0]
    assert parse_registry_text(text).liens == []


def test_not_a_registry_raises():
    with pytest.raises(DataSourceError, match="등기부등본"):
        parse_registry_text("월세 계약서입니다")


def test_pdf_without_text_raises():
    from io import BytesIO
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = BytesIO()
    w.write(buf)
    with pytest.raises(DataSourceError, match="직접 입력"):
        read_registry_pdf(buf.getvalue())


def test_reads_sample_pdf_end_to_end():
    data = (Path(__file__).parent / "fixtures" / "registry_sample.pdf").read_bytes()  # 테스트용 예시, 실제 등기부 아님
    r = read_registry_pdf(data)
    assert r.total_won == 134_000_000 and r.flags == ["압류"]


def test_header_gives_address_and_unit_for_search():
    """등기부를 먼저 올리면 첫 줄 '[집합건물] … 제2층 제201호'로 주소 검색·호 선택을 채운다."""
    r = parse_registry_text(SAMPLE)
    assert (r.address, r.dong, r.ho) == ("서울특별시 관악구 봉천동 1-70", "", "201")


def test_header_with_building_dong():
    text = SAMPLE.replace("서울특별시 관악구 봉천동 1-70 봉천하이빌 제2층 제201호",
                          "경기도 수원시 장안구 정자동 111 백설마을 제112동 제25층 제2501호")
    r = parse_registry_text(text)
    assert (r.address, r.dong, r.ho) == ("경기도 수원시 장안구 정자동 111", "112", "2501")


def test_header_missing_leaves_address_empty():
    text = "\n".join(l for l in SAMPLE.splitlines() if not l.startswith("[집합건물]"))
    assert parse_registry_text(text).address is None
